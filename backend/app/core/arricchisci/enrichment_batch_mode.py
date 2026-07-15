"""
Mixin BATCH_MODE — esecuzione API per

`EnrichmentExecutor`: invio array di input + processing risultati
"""

from __future__ import annotations

import time
from typing import Any, List, Optional, Tuple

import requests

from app.core.arricchisci.async_poller import (
    AsyncPollError,
    AsyncPoller,
    validate_async_config,
)
from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.tabular import iter_dataframe_indexed_rows
from app.core.arricchisci.http_executor import (
    RequestSpec,
    allow_redirects_from_config,
    body_type_from_config,
    execute_http_request,
    raw_body_from_config,
    validate_request_spec_config,
)
from app.core.arricchisci.retry_policy import clamp_retry_after
from app.core.arricchisci.success_evaluator import (
    evaluate_success,
    success_config_from_api_config,
)
from app.shared.security.outbound_url import validate_external_url
from app.shared.security.tls_policy import resolve_tls_verify
from app.utils.logger import get_logger

from app.core.arricchisci.models import (
    BatchInputFormat,
    EnrichmentStats,
    RowError,
    RowStatus,
)
from app.core.arricchisci.types import JsonObject, JsonRows

# Stesso logger name di `enrichment_service.py` (`__name__` valuta a
# `"app.core.arricchisci.enrichment_service"`). Singleton stdlib per
# nome → record indistinguibili dai consumer che attaccano handler /
# filtrano per nome canonico
logger = get_logger("app.core.arricchisci.enrichment_service")


class EnrichmentBatchModeMixin:
    """Mixin per `EnrichmentExecutor`: batch native API path.

    Retry HTTP completo + 429 + fallback row-by-row. Se configurato, il submit
    batch passa attraverso l'overlay async condiviso.
    """

    def _execute_batch_native(
        self,
        df: Any,
    ) -> Tuple[Any, EnrichmentStats, List[RowError]]:
        """
        Esecuzione con native.

        Per API che supportano array di input in singola chiamata:
        - Raggruppa righe in max_size
        - Invia singola richiesta per batch
        - Mappa risposte alle righe originali
        """
        result_df = copy_frame_for_update(df)
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
        for idx, row in iter_dataframe_indexed_rows(df):
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
                # Errore su tutto il batch: se continue_on_error e batch>1
                # fallback a chiamate row-by-row per isolare le righe buone
                # dalle cattive (es. 1 riga malformata che rompe un 100
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
                    # Errore bloccante o conta tutto come errore
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
                        # Risposta ù corta dell'input
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
        batch_params: JsonRows,
        batch_indices: List[int]
    ) -> Tuple[Optional[JsonRows], Optional[RowError]]:
        """
        Chiama API retry.

        Args:
            batch_params: Lista di parametri per ogni riga
            batch_indices: Lista di indici riga originali

        Returns:
            Tuple (lista risultati, errore) - uno è None
        """
        # Costruisci payload batch
        payload = self._build_batch_payload(batch_params)

        # URL e metodo, con eventuale override batch.
        url = self.batch_config.endpoint_override or self.url
        method = (self.batch_config.method_override or self.method).upper()
        connector_config = getattr(self, "api_config", {}) or {}
        request_cfg = connector_config.get("request", {}) or {}
        options_cfg = connector_config.get("options", {}) or {}
        async_config = connector_config.get("async", {}) or {}
        success_config = success_config_from_api_config(connector_config)
        body_type = body_type_from_config(request_cfg)

        last_error = None

        try:
            validate_external_url(url)
        except ValueError as e:
            return None, RowError(
                row_index=batch_indices[0],
                status=RowStatus.ERROR,
                error_message=str(e),
                error_type="request_error",
                retry_count=0,
            )

        try:
            validate_request_spec_config(method, body_type)
            validate_async_config(async_config)
        except ValueError as e:
            return None, RowError(
                row_index=batch_indices[0],
                status=RowStatus.ERROR,
                error_message=str(e),
                error_type="request_config_error",
                retry_count=0,
            )

        ssl_verify = resolve_tls_verify(
            not getattr(self.options, 'disable_ssl_verify', False),
            context="API enricher batch",
        )

        for attempt in range(self.options.max_retries + 1):
            try:
                request_spec = RequestSpec(
                    url=url,
                    method=method,
                    params=payload,
                    json_body=payload,
                    raw_body=raw_body_from_config(request_cfg),
                    body_type=body_type,
                    headers=self._headers,
                    timeout=(self.options.connect_timeout, self.options.request_timeout),
                    verify=ssl_verify,
                    allow_redirects=allow_redirects_from_config(
                        options_cfg,
                        request_cfg,
                    ),
                    validate_url=True,
                    context="API enricher batch",
                )

                data = None
                async_error_message = None
                if async_config.get("enabled"):
                    try:
                        poll_result = AsyncPoller(
                            execute=execute_http_request,
                            before_poll_request=getattr(
                                getattr(self, "_rate_limiter", None),
                                "acquire",
                                None,
                            ),
                        ).run(
                            request_spec,
                            async_config,
                            base_url=url,
                        )
                        response = poll_result.response
                        data = poll_result.data
                    except AsyncPollError as e:
                        if e.response is None:
                            raise
                        if e.stage != "submit":
                            return None, RowError(
                                row_index=batch_indices[0],
                                status=RowStatus.ERROR,
                                error_message=str(e),
                                error_type=(
                                    f"async_{e.stage}_error"
                                    if e.stage
                                    else "async_error"
                                ),
                                http_status=e.response.status_code,
                                retry_count=attempt,
                            )
                        response = e.response
                        async_error_message = str(e)
                    except TimeoutError as e:
                        return None, RowError(
                            row_index=batch_indices[0],
                            status=RowStatus.ERROR,
                            error_message=str(e),
                            error_type="timeout",
                            retry_count=attempt,
                        )
                    except ValueError as e:
                        return None, RowError(
                            row_index=batch_indices[0],
                            status=RowStatus.ERROR,
                            error_message=str(e),
                            error_type="async_error",
                            retry_count=attempt,
                        )
                else:
                    response = execute_http_request(request_spec)

                elapsed_ms = response.elapsed_ms
                with self._stats_lock:
                    # deque con maxlen=1000: auto-trunca i piu' vecchi
                    self._response_times.append(elapsed_ms)


                # Successo
                if response.status_code == 200:
                    if data is None:
                        try:
                            data = response.json()
                        except (ValueError, requests.exceptions.JSONDecodeError) as e:
                            return None, RowError(
                                row_index=batch_indices[0],
                                status=RowStatus.ERROR,
                                error_message=(
                                    f"Risposta HTTP 200 non e' JSON valido: "
                                    f"{type(e).__name__}"
                                ),
                                error_type="invalid_json_response",
                                http_status=200,
                                retry_count=attempt,
                            )
                    evaluation = evaluate_success(data, success_config)
                    if not evaluation.ok:
                        return None, RowError(
                            row_index=batch_indices[0],
                            status=RowStatus.ERROR,
                            error_message=(
                                evaluation.error_message
                                or "Errore applicativo nella risposta API"
                            ),
                            error_type="application_error",
                            http_status=200,
                            retry_count=attempt,
                        )
                    results = self._extract_batch_output(data)
                    return results, None

                # Rate limited
                if response.status_code == 429:
                    if self.options.respect_retry_after:
                        retry_after = clamp_retry_after(
                            response.headers.get("Retry-After"),
                            max_seconds=self.options.max_retry_after_seconds,
                        )
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
                    error_message=(
                        async_error_message
                        or f"HTTP {response.status_code}: {response.text[:200]}"
                    ),
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
            except (ValueError, KeyError, TypeError) as e:
                return None, RowError(
                    row_index=batch_indices[0],
                    status=RowStatus.ERROR,
                    error_message=str(e),
                    error_type="response_processing_error",
                    retry_count=attempt,
                )

            # Exponential backoff
            if attempt < self.options.max_retries:
                wait_time = self.options.retry_backoff_factor ** attempt
                time.sleep(wait_time)

        # Max retry raggiunto
        if last_error:
            last_error.status = RowStatus.RETRY_EXHAUSTED
        return None, last_error

    def _build_batch_payload(self, batch_params: JsonRows) -> JsonObject:
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

    def _extract_batch_output(self, data: Any) -> JsonRows:
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
                    # Per batch, il path è relativo all'elemento (senza [0
                    clean_path = path.lstrip("[0].")
                    result[col_name] = self._extract_json_value(item, clean_path)
                extracted.append(result)

        return extracted
