"""
Mixin BATCH_MODE — esecuzione batch native API per
`EnrichmentExecutor`: invio array di input + processing risultati
batch + payload format dispatcher.

Origine
=======
Estratto da `app.core.arricchisci.enrichment_service.EnrichmentExecutor`
come parte di Phase 5 Batch B3-A.4 (cluster split + mixin pattern,
vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A; mixin pattern
ratificato in B2.1+...+B2.6 + B3-A.2 + B3-A.3).

Razionale del raggruppamento: 4 metodi che insieme implementano il
"batch native" path di esecuzione enrichment, alternativa al path
sequenziale/parallelo per API che supportano array di input in
singola chiamata:

  - `_execute_batch_native`     — entry point: raggruppa righe in
                                  batch di `batch_config.max_size`,
                                  invia singola richiesta per batch,
                                  mappa risposte alle righe originali.
                                  Su batch failure (continue_on_error
                                  + len(batch) > 1): fallback row-by-row
                                  per isolare le righe buone.
  - `_call_batch_api`           — low-level HTTP batch: retry chain
                                  con 429+Retry-After + retry_on_status
                                  + exponential backoff. URL/method
                                  con possibile override da batch_config.
  - `_build_batch_payload`      — payload builder: 2 formati
                                  (FLAT_ARRAY: `{items: ["v1","v2"]}` /
                                  ARRAY: `{items: [{q:"v1"},{q:"v2"}]}`).
                                  Rimuove API key dai params + reinietta
                                  in query se `auth.location=query`.
  - `_extract_batch_output`     — extract output array dal response,
                                  applica `output_mapping` per item
                                  (clean_path post-`lstrip("[0].")`).

Cluster coeso: tutti sync (non async). `_execute_batch_native` chiama
intra-cluster `_call_batch_api`. `_call_batch_api` chiama intra-cluster
`_build_batch_payload` + `_extract_batch_output`.

Pattern: Mixin class via MRO
============================
`EnrichmentBatchModeMixin` viene ereditato da `EnrichmentExecutor`:

    class EnrichmentExecutor(
        EnrichmentTemplateParamsMixin,
        EnrichmentResponseProcessingMixin,
        EnrichmentBatchModeMixin,
    ):
        ...

I 4 metodi restano accessibili come `EnrichmentExecutor.<method>` via
MRO. `inspect.getsource(EnrichmentExecutor.<method>)` legge il source
DA QUESTO FILE. Le call-site esterne dal cluster
`execute → _execute_batch_native` (entry point dispatcher)
continuano a risolvere via MRO senza modifiche.

Coupling cross-mixin
====================
- `self._build_params(row)` → `EnrichmentTemplateParamsMixin` (B3-A.2). MRO.
- `self._has_null_params(params)` → `EnrichmentTemplateParamsMixin`. MRO.
- `self._apply_result(df, idx, result)` → `EnrichmentResponseProcessingMixin`
  (B3-A.3). MRO.
- `self._extract_json_value(data, path)` → `EnrichmentResponseProcessingMixin`. MRO.
- `self._update_progress(processed, total, stats)` → resta in
  `EnrichmentExecutor` core. MRO.
- `self._rate_limiter.acquire()` → instance attribute
  `RateLimiter` (B3-A.1). Object access, MRO non rilevante.

Coupling esterno (instance attrs)
==================================
- `self.output_mapping`, `self.batch_config`, `self.url`, `self.method`,
  `self.options`, `self.auth`, `self._headers`, `self._stats_lock`,
  `self._response_times` — instance attrs di `EnrichmentExecutor.__init__`.

HTTP/session
============
`_call_batch_api` usa `get_shared_session()` (top-level import da
`app.shared.api_utils`) per ottenere `requests.Session` shared. 429
+ Retry-After handling presente — analogo al pattern in
`_call_api_with_retry` non-batch (candidato consolidamento futuro
in `EnrichmentRetryHttpMixin` B3-A.6, fuori scope B3-A.4 per
preservare body bit-per-bit).

Async/sync
==========
Tutti e 4 i metodi sono **sync** (no `async def`). NON interagiscono
con il path async `_execute_parallel_async` (B3-A.7 candidate).

Cache
=====
`_execute_batch_native` NON usa cache `_make_cache_key`: ogni batch va
in chiamata diretta. La deduplicazione cache e' del path sequenziale
(`_execute_internal`), non del batch native.

Coupling test contract
======================
Verificato pre-extraction (preflight B3-A.4):
  - 0 `patch.object(executor, "<method>", ...)` su questi 4 metodi.
  - 0 `inspect.getsource(EnrichmentExecutor.<method>)` o
    `inspect.getsource(enrichment_service)`.
  - 0 AST scan su `enrichment_service.py` per pattern in questi metodi.
  - 0 `monkeypatch.setattr` su attributi del modulo.

Estrazione safe per costruzione. Nessuna pre-modernization richiesta.

Logger naming contract
======================
4 callsite di log: `_execute_batch_native:870 logger.info` (avvio
batch), `:906 logger.warning` (fallback row-by-row su batch fail),
`_extract_batch_output:1171 logger.warning` (response non-array).
Mixin definisce `logger` esplicito canonico
`get_logger("app.core.arricchisci.enrichment_service")` (parita' B2 +
B3-A.2/A.3; singleton stdlib per nome → record indistinguibili).

Behavior-preserving
===================
NESSUN cambio architetturale in B3-A.4:
  - Nessun Redis, nessun cache distribuito.
  - HTTP/session: `get_shared_session()` invariato.
  - Retry chain: 429+Retry-After + retry_on_status + exponential
    backoff — body bit-per-bit.
  - Payload format dispatcher: FLAT_ARRAY + ARRAY/OBJECT — body
    bit-per-bit.
  - Fallback row-by-row su batch fail con
    `continue_on_error and len(batch) > 1` — body bit-per-bit.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`enrichment_service.py` al momento di B3-A.4. Nessuna modifica di
logica, comment, log payload, ordine di operazione, retry timing,
status code dispatch, payload format selection, output extraction
path normalization.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

from app.shared.api_utils import get_shared_session
from app.utils.logger import get_logger

from app.core.arricchisci.models import (
    BatchInputFormat,
    EnrichmentStats,
    RowError,
    RowStatus,
)

# Stesso logger name di `enrichment_service.py` (`__name__` valuta a
# `"app.core.arricchisci.enrichment_service"`). Singleton stdlib per
# nome → record indistinguibili dai consumer che attaccano handler /
# filtrano per nome canonico.
logger = get_logger("app.core.arricchisci.enrichment_service")


class EnrichmentBatchModeMixin:
    """Mixin per `EnrichmentExecutor`: batch native API path. Sync.
    Zero cache, zero async. Retry chain HTTP completo + 429 +
    fallback row-by-row su batch fail.
    """

    def _execute_batch_native(
        self,
        df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, EnrichmentStats, List[RowError]]:
        """
        Esecuzione con batch API native.

        Per API che supportano array di input in singola chiamata:
        - Raggruppa righe in batch di max_size
        - Invia singola richiesta per batch
        - Mappa risposte alle righe originali
        """
        result_df = df.copy()
        stats = EnrichmentStats(total_rows=len(df))
        errors: List[RowError] = []

        # Prepara colonne output
        for mapping in self.output_mapping:
            col_name = mapping.get("column", mapping.get("path"))
            if col_name not in result_df.columns:
                result_df[col_name] = None

        total_rows = len(df)
        start_time = time.time()
        batch_size = self.batch_config.max_size

        logger.info(
            f"Batch native: {total_rows} righe, batch_size={batch_size}, "
            f"~{(total_rows + batch_size - 1) // batch_size} chiamate"
        )

        # Costruisci lista di (idx, params) per tutte le righe valide
        rows_to_process = []
        for idx, row in df.iterrows():
            params = self._build_params(row)

            if self.options.skip_null_params and self._has_null_params(params):
                stats.skipped += 1
                continue

            rows_to_process.append((idx, params))

        # Processa in batch
        processed = 0

        for batch_start in range(0, len(rows_to_process), batch_size):
            batch = rows_to_process[batch_start:batch_start + batch_size]
            batch_indices = [item[0] for item in batch]
            batch_params = [item[1] for item in batch]

            # Rate limiting tra batch
            self._rate_limiter.acquire()

            # Chiamata batch
            results, batch_error = self._call_batch_api(batch_params, batch_indices)
            stats.api_calls_made += 1

            if batch_error:
                # Errore su tutto il batch: se continue_on_error e batch>1,
                # fallback a chiamate row-by-row per isolare le righe buone
                # dalle cattive (es. 1 riga malformata che rompe un batch da 100).
                if self.options.continue_on_error and len(batch) > 1:
                    logger.warning(
                        f"Batch di {len(batch)} fallito ({batch_error.error_type}), "
                        f"fallback a row-by-row per isolare errori"
                    )
                    for idx_single, params_single in batch:
                        try:
                            self._rate_limiter.acquire()
                            single_results, single_err = self._call_batch_api(
                                [params_single], [idx_single]
                            )
                            stats.api_calls_made += 1
                            if single_err or not single_results or single_results[0] is None:
                                err_obj = single_err or batch_error
                                errors.append(RowError(
                                    row_index=idx_single,
                                    status=err_obj.status,
                                    error_message=err_obj.error_message,
                                    error_type=err_obj.error_type,
                                    http_status=getattr(err_obj, 'http_status', None),
                                ))
                                stats.errors += 1
                            else:
                                self._apply_result(result_df, idx_single, single_results[0])
                                stats.success += 1
                        except Exception as _sre:
                            errors.append(RowError(
                                row_index=idx_single,
                                status=RowStatus.ERROR,
                                error_message=f"Fallback row error: {_sre}",
                                error_type="fallback_exception",
                            ))
                            stats.errors += 1
                else:
                    # Errore bloccante o batch singolo: conta tutto come errore
                    for idx in batch_indices:
                        errors.append(RowError(
                            row_index=idx,
                            status=batch_error.status,
                            error_message=batch_error.error_message,
                            error_type=batch_error.error_type,
                            http_status=batch_error.http_status
                        ))
                        stats.errors += 1
            else:
                # Successo - mappa risultati alle righe
                for i, (idx, params) in enumerate(batch):
                    if i < len(results):
                        result = results[i]
                        if result is not None:
                            self._apply_result(result_df, idx, result)
                            stats.success += 1
                        else:
                            # Risultato nullo per questa riga
                            errors.append(RowError(
                                row_index=idx,
                                status=RowStatus.ERROR,
                                error_message="Risultato nullo nella risposta batch",
                                error_type="null_result"
                            ))
                            stats.errors += 1
                    else:
                        # Risposta batch più corta dell'input
                        errors.append(RowError(
                            row_index=idx,
                            status=RowStatus.ERROR,
                            error_message="Risposta batch incompleta",
                            error_type="incomplete_batch"
                        ))
                        stats.errors += 1

                # Calcola chiamate risparmiate
                stats.api_calls_saved += len(batch) - 1

            processed += len(batch)
            self._update_progress(processed + stats.skipped, total_rows, stats)

        # Finalizza statistiche
        stats.total_time_ms = (time.time() - start_time) * 1000
        if self._response_times:
            stats.avg_response_time_ms = sum(self._response_times) / len(self._response_times)

        return result_df, stats, errors

    def _call_batch_api(
        self,
        batch_params: List[Dict],
        batch_indices: List[int]
    ) -> Tuple[Optional[List[Dict]], Optional[RowError]]:
        """
        Chiama API batch con retry.

        Args:
            batch_params: Lista di parametri per ogni riga
            batch_indices: Lista di indici riga originali

        Returns:
            Tuple (lista risultati, errore) - uno è None
        """
        # Costruisci payload batch
        payload = self._build_batch_payload(batch_params)

        # URL e metodo (con possibile override)
        url = self.batch_config.endpoint_override or self.url
        method = (self.batch_config.method_override or self.method).upper()

        last_error = None

        for attempt in range(self.options.max_retries + 1):
            try:
                start_time = time.time()

                _session = get_shared_session()
                if method == "GET":
                    response = _session.get(
                        url,
                        params=payload,
                        headers=self._headers,
                        timeout=(self.options.connect_timeout, self.options.request_timeout)
                    )
                else:
                    response = _session.post(
                        url,
                        json=payload,
                        headers=self._headers,
                        timeout=(self.options.connect_timeout, self.options.request_timeout)
                    )

                elapsed_ms = (time.time() - start_time) * 1000
                with self._stats_lock:
                    # deque con maxlen=1000: auto-trunca i piu' vecchi
                    self._response_times.append(elapsed_ms)


                # Successo
                if response.status_code == 200:
                    data = response.json()
                    results = self._extract_batch_output(data)
                    return results, None

                # Rate limited
                if response.status_code == 429:
                    if self.options.respect_retry_after:
                        retry_after = int(response.headers.get("Retry-After", 60))
                        time.sleep(retry_after)

                    last_error = RowError(
                        row_index=batch_indices[0],
                        status=RowStatus.ERROR,
                        error_message="Rate limited (429)",
                        error_type="rate_limit",
                        http_status=429,
                        retry_count=attempt
                    )
                    continue

                # Altri errori retriable
                if response.status_code in self.options.retry_on_status:
                    last_error = RowError(
                        row_index=batch_indices[0],
                        status=RowStatus.ERROR,
                        error_message=f"HTTP {response.status_code}",
                        error_type=f"http_{response.status_code}",
                        http_status=response.status_code,
                        retry_count=attempt
                    )

                    if attempt < self.options.max_retries:
                        wait_time = self.options.retry_backoff_factor ** attempt
                        time.sleep(wait_time)
                    continue

                # Errore non retriable
                return None, RowError(
                    row_index=batch_indices[0],
                    status=RowStatus.ERROR,
                    error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                    error_type=f"http_{response.status_code}",
                    http_status=response.status_code,
                    retry_count=attempt
                )

            except requests.Timeout:
                last_error = RowError(
                    row_index=batch_indices[0],
                    status=RowStatus.ERROR,
                    error_message=f"Timeout dopo {self.options.request_timeout}s",
                    error_type="timeout",
                    retry_count=attempt
                )

            except requests.RequestException as e:
                last_error = RowError(
                    row_index=batch_indices[0],
                    status=RowStatus.ERROR,
                    error_message=str(e),
                    error_type="request_error",
                    retry_count=attempt
                )

            # Exponential backoff
            if attempt < self.options.max_retries:
                wait_time = self.options.retry_backoff_factor ** attempt
                time.sleep(wait_time)

        # Max retry raggiunto
        if last_error:
            last_error.status = RowStatus.RETRY_EXHAUSTED
        return None, last_error

    def _build_batch_payload(self, batch_params: List[Dict]) -> Dict:
        """
        Costruisce payload per chiamata batch.

        Supporta diversi formati:
        - ARRAY: {"items": [{"q": "addr1"}, {"q": "addr2"}]}
        - FLAT_ARRAY: {"items": ["addr1", "addr2"]} (singolo parametro)
        - OBJECT: payload personalizzato
        """
        input_key = self.batch_config.input_key
        input_format = self.batch_config.input_format

        if input_format == BatchInputFormat.FLAT_ARRAY:
            # Estrai il primo valore da ogni params dict
            # Assume un solo parametro rilevante
            flat_values = []
            for params in batch_params:
                # Prendi il primo valore non-API-key
                api_key_name = self.auth.get("key_name") if self.auth.get("type") == "api_key" else None
                for key, value in params.items():
                    if key != api_key_name and value is not None:
                        flat_values.append(value)
                        break

            payload = {input_key: flat_values}

        else:  # ARRAY (default) o OBJECT
            # Rimuovi API key dai params per il batch
            clean_params = []
            api_key_name = self.auth.get("key_name") if self.auth.get("type") == "api_key" else None

            for params in batch_params:
                clean = {k: v for k, v in params.items() if k != api_key_name}
                clean_params.append(clean)

            payload = {input_key: clean_params}

        # Aggiungi API key se in query
        if self.auth.get("type") == "api_key" and self.auth.get("location") == "query":
            payload[self.auth.get("key_name", "apiKey")] = self.auth.get("key_value", "")

        return payload

    def _extract_batch_output(self, data: Any) -> List[Dict]:
        """
        Estrae array di risultati dalla risposta batch.

        Returns:
            Lista di dizionari con campi estratti per ogni riga
        """
        # Naviga al path dell'array risultati
        results_array = data
        if self.batch_config.output_path:
            results_array = self._extract_json_value(data, self.batch_config.output_path)

        if not isinstance(results_array, list):
            logger.warning(f"Risposta batch non è un array: {type(results_array)}")
            return []

        # Estrai output per ogni elemento
        extracted = []
        for item in results_array:
            if item is None:
                extracted.append(None)
            else:
                result = {}
                for mapping in self.output_mapping:
                    path = mapping.get("path", "")
                    col_name = mapping.get("column", path)
                    # Per batch, il path è relativo all'elemento (senza [0])
                    clean_path = path.lstrip("[0].")
                    result[col_name] = self._extract_json_value(item, clean_path)
                extracted.append(result)

        return extracted
