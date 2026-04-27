"""
Servizio di esecuzione arricchimento dati via API esterne.

Architettura
============
Questo modulo implementa l'esecuzione dell'arricchimento: dato un DataFrame di input
e una configurazione API, itera le righe, chiama l'API per ciascuna, e arricchisce
il DataFrame con i dati estratti dalla risposta JSON.

Componenti:
- RateLimiter: token bucket thread-safe per rispettare i limiti API
- EnrichmentExecutor: classe principale con tre strategie di esecuzione
- generate_error_report(): utility per esportare errori in CSV

Strategie di esecuzione (scelta automatica in execute()):
1. Batch nativo -> per API che accettano array di input in una sola chiamata
2. Parallelo -> ThreadPoolExecutor con N worker + rate limiting
3. Sequenziale -> riga per riga con deduplicazione cache

Flusso di ogni chiamata API:
1. _build_params(): costruisce parametri dalla riga (static + mapping + auth)
2. _call_api_with_retry(): chiama con retry + exponential backoff
3. _extract_output(): naviga la risposta JSON con dot notation annidata
4. _apply_result(): scrive i valori estratti nelle colonne output

Funzionalità chiave:
- Deduplicazione: raggruppa righe con parametri identici, chiama API una volta
  e applica risultato a tutto il gruppo (cache in-memory con chiave MD5)
- Gestione errori granulare per riga (SUCCESS/ERROR/SKIPPED/RETRY_EXHAUSTED)
- Preview su campione per stimare tempo totale prima dell'esecuzione
- Validazione input: controlla colonne richieste e valori nulli
- Rate limiting: token bucket + rispetto header Retry-After (HTTP 429)
- Retry: exponential backoff configurabile con status code retriabili

Autenticazione: API key (header/query), OAuth2 password grant

Thread safety: cache, response_times e statistiche protetti da Lock dedicati.
RateLimiter thread-safe per esecuzione parallela.

Uso:
    executor = EnrichmentExecutor(api_config, param_mapping, options)
    result_df, result = executor.execute(df)
"""

import os
import time
import hashlib
import json
import re
import requests
from app.shared.api_utils import get_shared_session
import pandas as pd
import threading
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional, List, Tuple, Callable
from datetime import datetime, timezone
from collections import defaultdict, deque

# Disabilita SSL warnings (fix Windows)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from app.core.arricchisci.models import (
    RowStatus, RowError, EnrichmentStats, EnrichmentOptions,
    EnrichmentResult, PreviewResult, EnrichmentValidationResult,
    BatchConfig, BatchInputFormat
)
from app.utils.logger import get_logger
from app.shared.api_utils import (
    geojson_to_wkt as _shared_geojson_to_wkt,
    extract_json_value as _shared_extract_json_value,
    prepare_headers as _shared_prepare_headers,
    get_oauth2_token as _shared_get_oauth2_token,
    get_oauth2_client_credentials_token as _shared_get_oauth2_cc_token,
    sanitize_for_log,
    sanitize_url_for_log,
)

# Phase 5 B3-A.1 cutover: `RateLimiter` (token bucket thread-safe
# in-memory) spostato in `app.core.arricchisci._rate_limiter`.
# `EnrichmentExecutor.__init__` continua a usare
# `RateLimiter(self.options.rate_limit)` via l'import sotto; le 4
# callsite `self._rate_limiter.acquire()` sono invariate (instance
# attribute). Re-import per preservare retrocompat di eventuali
# consumer storici di `RateLimiter` da `enrichment_service` direct
# (verificato pre-extraction: zero consumer esterni, ma cheap insurance).
from app.core.arricchisci._rate_limiter import RateLimiter  # noqa: F401  (re-export)

# Phase 5 B3-A.2 cutover: cluster TEMPLATE_PARAMS (`_is_template`,
# `_template_columns`, `_interpolate_template`, `_build_params`,
# `_has_null_params`, `_make_cache_key`) spostato in
# `app.core.arricchisci.enrichment_template_params.EnrichmentTemplateParamsMixin`.
# `EnrichmentExecutor` eredita dal mixin (vedi class declaration sotto).
# I 6 metodi restano accessibili via MRO. Le call-site
# `self._is_template / _interpolate_template / _build_params / _has_null_params /
# _template_columns / _make_cache_key` da `validate_input` /
# `_execute_*` / `_group_by_params` continuano a risolvere via MRO
# senza modifiche. Vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A.
from app.core.arricchisci.enrichment_template_params import (
    EnrichmentTemplateParamsMixin,
)

# Phase 5 B3-A.3 cutover: cluster RESPONSE_PROCESSING (`_extract_output`,
# `_explode_rows`, `_auto_flatten`, `_detect_geometry_type`,
# `_infer_scalar_type`, `_flatten_dict`, `_apply_result`,
# `_resolve_nested_list`, `_extract_json_value`) spostato in
# `app.core.arricchisci.enrichment_response_processing.EnrichmentResponseProcessingMixin`.
# `EnrichmentExecutor` eredita dal mixin (vedi class declaration sotto).
# I 9 metodi restano accessibili via MRO. Le call-site
# `self._apply_result / _extract_output / _extract_json_value` da
# `_call_api_with_retry / _execute_* / _call_batch_api` continuano a
# risolvere via MRO senza modifiche. Vedi
# `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A.
from app.core.arricchisci.enrichment_response_processing import (
    EnrichmentResponseProcessingMixin,
)

logger = get_logger(__name__)


def _geojson_to_wkt(geom: dict) -> str:
    """Converte un dict geometry GeoJSON in WKT. Delegato a shared/api_utils."""
    return _shared_geojson_to_wkt(geom)


# Phase 5 B3-A.1 cutover: `class RateLimiter` spostato in
# `app.core.arricchisci._rate_limiter`. Re-import in testa al file
# (vedi block sopra) per preservare i path simbolici esistenti.


class EnrichmentExecutor(
    EnrichmentTemplateParamsMixin,
    EnrichmentResponseProcessingMixin,
):
    """
    Esecutore avanzato per enrichment API.
    
    Supporta:
    - Deduplicazione automatica delle chiamate
    - Gestione errori granulare per riga
    - Preview su campione
    - Validazione input
    - Retry intelligente
    - Esecuzione parallela con rate limiting
    """
    
    def __init__(
        self,
        api_config: Dict,
        param_mapping: Dict,
        options: EnrichmentOptions = None,
        progress_callback: Callable[[int, str], None] = None
    ):
        """
        Args:
            api_config: Configurazione API (endpoint, auth, output_mapping)
            param_mapping: Mapping {api_param: df_column}
            options: Opzioni esecuzione
            progress_callback: Callback per aggiornare progress (percent, message)
        """
        self.api_config = api_config
        self.param_mapping = param_mapping
        self.options = options or EnrichmentOptions()
        self.progress_callback = progress_callback
        
        # Cache per deduplicazione (thread-safe, bounded LRU-style)
        self._cache: Dict[str, Dict] = {}
        self._cache_lock = threading.Lock()
        self._cache_hits = 0
        self._CACHE_MAX_SIZE = 5000
        
        # Statistiche (thread-safe). deque(maxlen=1000) auto-trunca i
        # response times piu' vecchi invece di accumulare indefinitamente
        # tra arricchimenti successivi eseguiti dalla stessa istanza (memory
        # leak lento con executor pool riusato dai worker Celery).
        self._response_times: deque = deque(maxlen=1000)
        self._stats_lock = threading.Lock()
        
        # Estrai config API
        endpoint = api_config.get("endpoint", {})
        self.url = endpoint.get("url", "")
        self.method = endpoint.get("method", "GET").upper()
        self.auth = api_config.get("auth", {})
        self.output_mapping = api_config.get("output_mapping", [])
        self.api_name = api_config.get("name", "API")

        # Set di parametri "required": serve a _has_null_params per skippare
        # solo quando manca un parametro davvero obbligatorio (gli opzionali null
        # vengono semplicemente omessi dalla chiamata).
        # Se l'info non è disponibile (parameters vuoto), fallback al comportamento
        # legacy: tutti i parametri sono trattati come required.
        params_def = api_config.get("parameters") or []
        self._required_param_names = {
            p.get("name") for p in params_def
            if isinstance(p, dict) and p.get("required") and p.get("name")
        }
        self._has_param_definitions = bool(params_def)
        
        # Headers preparati
        self._headers = self._prepare_headers()

        # Tipi colonna rilevati durante auto-flatten (usato per aggiornare schema_fields)
        self.detected_column_types: Dict[str, str] = {}
        
        # Rate limiter
        self._rate_limiter = RateLimiter(self.options.rate_limit)
        
        # Batch config (per API che supportano batch nativo)
        batch_data = api_config.get("batch", {})
        self.batch_config = BatchConfig.from_dict(batch_data)
    
    # =========================================================================
    # API PUBBLICA
    # =========================================================================
    # Metodi chiamabili dall'esterno: validate_input(), preview(), execute().
    # L'ordine tipico è: validate -> preview (opzionale) -> execute.
    
    def validate_input(self, df: pd.DataFrame) -> EnrichmentValidationResult:
        """
        Valida il DataFrame input prima dell'esecuzione.
        
        Controlla:
        - Colonne richieste esistono
        - Valori nulli nei parametri
        
        Args:
            df: DataFrame da validare
            
        Returns:
            EnrichmentValidationResult con dettagli validazione
        """
        missing_columns = []
        null_param_rows = []
        warnings = []

        # Helper: True se il parametro deve essere considerato bloccante
        # (required oppure abbiamo fallback legacy senza definizioni).
        def _is_blocking(api_param: str) -> bool:
            if not self._has_param_definitions:
                return True  # legacy: tratta tutto come required
            return api_param in self._required_param_names

        # Helper: ritorna l'elenco delle colonne richieste da un mapping value.
        # - Mapping classico ("nome_col") → ["nome_col"]
        # - Template ("{X},{Y}")          → ["X", "Y"]
        def _required_columns(mapping_value: Any) -> List[str]:
            if not mapping_value:
                return []
            if self._is_template(mapping_value):
                return self._template_columns(mapping_value)
            return [mapping_value]

        # Controlla colonne richieste — solo per parametri required.
        # Le colonne mancanti per parametri opzionali sono ignorate (il param
        # verrà semplicemente omesso dalla chiamata API).
        for api_param, mapping_value in self.param_mapping.items():
            cols = _required_columns(mapping_value)
            for col in cols:
                if col not in df.columns and _is_blocking(api_param):
                    if col not in missing_columns:
                        missing_columns.append(col)

        if missing_columns:
            return EnrichmentValidationResult(
                valid=False,
                total_rows=len(df),
                valid_rows=0,
                invalid_rows=len(df),
                missing_columns=missing_columns,
                warnings=[f"Colonne mancanti: {', '.join(missing_columns)}"]
            )

        # Controlla valori nulli — solo sui parametri required (vectorized)
        # Raccogli tutte le colonne required prima
        required_cols_set = set()
        for api_param, mapping_value in self.param_mapping.items():
            if not mapping_value or not _is_blocking(api_param):
                continue
            for col in _required_columns(mapping_value):
                if col in df.columns:
                    required_cols_set.add(col)

        if required_cols_set:
            # Maschera vectorizzata: True se QUALSIASI required column è null/vuota
            null_mask = pd.Series(False, index=df.index)
            for col in required_cols_set:
                col_null = df[col].isna() | (df[col].astype(str) == "")
                null_mask = null_mask | col_null
            null_param_rows = df.index[null_mask].tolist()
        
        valid_rows = len(df) - len(null_param_rows)
        
        if null_param_rows:
            warnings.append(
                f"{len(null_param_rows)} righe hanno parametri nulli/vuoti"
            )
        
        return EnrichmentValidationResult(
            valid=True,
            total_rows=len(df),
            valid_rows=valid_rows,
            invalid_rows=len(null_param_rows),
            missing_columns=[],
            null_param_rows=null_param_rows,
            warnings=warnings
        )
    
    def preview(self, df: pd.DataFrame, sample_size: int = None) -> PreviewResult:
        """
        Esegue preview su un campione per stimare tempo e verificare funzionamento.
        
        Args:
            df: DataFrame completo
            sample_size: Dimensione campione (default da options)
            
        Returns:
            PreviewResult con stime e anteprima
        """
        sample_size = sample_size or self.options.preview_sample_size
        sample_size = min(sample_size, len(df))
        
        # Campiona righe
        if len(df) > sample_size:
            sample_df = df.sample(n=sample_size, random_state=42)
        else:
            sample_df = df.copy()
        
        # Calcola combinazioni uniche per stimare chiamate API
        unique_combinations = self._count_unique_combinations(df)
        
        # Esegui su campione
        start_time = time.time()
        
        # Disabilita progress callback per preview
        original_callback = self.progress_callback
        self.progress_callback = None
        
        result_df, stats, errors = self._execute_internal(sample_df)
        
        self.progress_callback = original_callback
        
        elapsed_ms = (time.time() - start_time) * 1000
        avg_time_per_row = elapsed_ms / sample_size if sample_size > 0 else 0
        
        # Stima tempo totale considerando deduplicazione e parallelismo
        unique_in_sample = self._count_unique_combinations(sample_df)
        
        # Tempo stimato = chiamate uniche / (rate_limit * min(parallel_workers, chiamate))
        estimated_api_calls = unique_combinations
        effective_parallelism = min(self.options.parallel_workers, estimated_api_calls)
        
        if self.options.parallel_workers > 1 and effective_parallelism > 1:
            # Con parallelismo: tempo ridotto
            rate = self.options.rate_limit if self.options.rate_limit > 0 else 10
            estimated_time = estimated_api_calls / rate
        else:
            # Sequenziale: tempo per riga * chiamate
            estimated_time = (estimated_api_calls * avg_time_per_row) / 1000
        
        # Warning se tante righe
        warnings = []
        if len(df) > 10000:
            warnings.append(f"Dataset grande ({len(df)} righe). Considera di usare batch.")
        if stats.errors > sample_size * 0.2:
            warnings.append(f"Alto tasso errori nel campione ({stats.errors}/{sample_size})")
        if self.options.parallel_workers > 1:
            warnings.append(f"Esecuzione parallela con {self.options.parallel_workers} worker")
        
        return PreviewResult(
            sample_size=sample_size,
            success_count=stats.success,
            error_count=stats.errors,
            success_rate=stats.success_rate,
            avg_time_per_row_ms=avg_time_per_row,
            estimated_total_time_seconds=estimated_time,
            estimated_api_calls=estimated_api_calls,
            unique_param_combinations=unique_combinations,
            preview_data=result_df.head(5).to_dict(orient="records"),
            errors=errors,
            warnings=warnings
        )
    
    def execute(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, EnrichmentResult]:
        """
        Esegue l'enrichment completo.

        Args:
            df: DataFrame da arricchire

        Returns:
            Tuple (DataFrame arricchito, EnrichmentResult)
        """
        started_at = datetime.now(timezone.utc).isoformat()

        # Validazione
        if self.options.validate_input:
            validation = self.validate_input(df)
            if not validation.valid:
                return df, EnrichmentResult(
                    success=False,
                    stats=EnrichmentStats(total_rows=len(df)),
                    warnings=validation.warnings,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat()
                )

        # Feature flag: routing tra sync legacy e async nuovo path
        _async_enabled = os.environ.get("ENABLE_ASYNC_ENRICHMENT", "false").lower() in ("1", "true", "yes")
        _can_use_async = (
            _async_enabled
            and not self.batch_config.enabled  # batch native usa path dedicato
            and len(df) > 1  # async ha senso solo con piu di 1 riga
        )

        if _can_use_async:
            logger.info(f"[ASYNC] Esecuzione enrichment async (pool via ENRICHMENT_POOL_SIZE)")
            import asyncio
            try:
                result_df, stats, errors = asyncio.run(self._execute_parallel_async(df))
            except Exception as e:
                logger.error(f"[ASYNC] Errore nel path async, fallback a sync: {e}", exc_info=True)
                result_df, stats, errors = self._execute_parallel(df)
        elif self.batch_config.enabled:
            result_df, stats, errors = self._execute_batch_native(df)
        elif self.options.parallel_workers > 1:
            result_df, stats, errors = self._execute_parallel(df)
        else:
            result_df, stats, errors = self._execute_internal(df)
        
        # Determina successo
        success = stats.errors == 0 or (
            self.options.continue_on_error and 
            stats.success > 0
        )
        
        return result_df, EnrichmentResult(
            success=success,
            stats=stats,
            errors=errors,
            api_name=self.api_name,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat()
        )
    
    # =========================================================================
    # ESECUZIONE PARALLELA
    # ThreadPoolExecutor con N worker + rate limiting + deduplicazione.
    # Ogni worker processa un gruppo di righe con parametri identici.
    # La cache e le statistiche sono protette da Lock dedicati.
    # =========================================================================
    
    def _execute_parallel(
        self, 
        df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, EnrichmentStats, List[RowError]]:
        """
        Esecuzione parallela con ThreadPoolExecutor.
        """
        result_df = df.copy()
        stats = EnrichmentStats(total_rows=len(df))
        errors: List[RowError] = []
        errors_lock = threading.Lock()
        
        # Prepara colonne output
        for mapping in self.output_mapping:
            col_name = mapping.get("column", mapping.get("path"))
            if col_name not in result_df.columns:
                result_df[col_name] = None
        
        total_rows = len(df)
        start_time = time.time()
        
        # Deduplicazione: raggruppa per parametri unici
        if self.options.enable_deduplication:
            unique_groups = self._group_by_params(df)
            logger.info(
                f"Esecuzione parallela: {len(unique_groups)} combinazioni uniche "
                f"su {total_rows} righe, {self.options.parallel_workers} worker"
            )
        else:
            unique_groups = {str(idx): [idx] for idx in df.index}
        
        processed = [0]  # Lista per permettere modifica in closure
        processed_lock = threading.Lock()
        
        def process_group(cache_key: str, row_indices: List[int]) -> Tuple[str, List[int], Optional[Dict], Optional[RowError]]:
            """Worker function per processare un gruppo.
            
            Double-checked locking per evitare cache stampede:
            se due worker ricevono lo stesso cache_key contemporaneamente,
            solo uno fa la chiamata API — l'altro aspetta e usa il risultato in cache.
            """
            first_idx = row_indices[0]
            row = df.loc[first_idx]
            
            # Costruisci parametri
            params = self._build_params(row)
            
            # Salta se parametri nulli
            if self.options.skip_null_params and self._has_null_params(params):
                return cache_key, row_indices, None, RowError(
                    row_index=first_idx,
                    status=RowStatus.SKIPPED,
                    error_message="Parametri nulli",
                    error_type="null_params"
                )
            
            if self.options.enable_deduplication:
                # Prima lettura cache (senza lock — fast path)
                if cache_key in self._cache:
                    return cache_key, row_indices, self._cache[cache_key], None
                
                # Segna come "in volo" con lock, per evitare che un secondo worker
                # faccia la stessa chiamata mentre il primo è in attesa della risposta
                with self._cache_lock:
                    if cache_key in self._cache:
                        # Altro worker ha già completato mentre attendevamo il lock
                        return cache_key, row_indices, self._cache[cache_key], None
                    # Placeholder: indica che questo worker sta elaborando il gruppo
                    self._cache[cache_key] = "__in_flight__"
            
            # Rate limiting
            self._rate_limiter.acquire()
            
            # Chiamata API
            api_result, error = self._call_api_with_retry(params, first_idx)
            
            # Aggiorna cache con risultato reale (o rimuovi placeholder in caso di errore)
            if self.options.enable_deduplication:
                with self._cache_lock:
                    if api_result:
                        self._cache[cache_key] = api_result
                        if len(self._cache) > self._CACHE_MAX_SIZE:
                            # Pruning FIFO: rimuove le entry più vecchie
                            keys = list(self._cache.keys())[:self._CACHE_MAX_SIZE // 2]
                            for k in keys:
                                self._cache.pop(k, None)
                    else:
                        # Rimuovi placeholder: un retry futuro potrà riprovare
                        self._cache.pop(cache_key, None)
            
            return cache_key, row_indices, api_result, error
        
        # Esegui in parallelo
        with ThreadPoolExecutor(max_workers=self.options.parallel_workers) as executor:
            futures = {
                executor.submit(process_group, cache_key, row_indices): (cache_key, row_indices)
                for cache_key, row_indices in unique_groups.items()
            }
            
            for future in as_completed(futures):
                try:
                    cache_key, row_indices, api_result, error = future.result()
                    
                    if error and error.status == RowStatus.SKIPPED:
                        # Righe saltate
                        with self._stats_lock:
                            stats.skipped += len(row_indices)
                    elif error:
                        # Errore - registra per tutte le righe del gruppo
                        with errors_lock:
                            for idx in row_indices:
                                errors.append(RowError(
                                    row_index=idx,
                                    status=error.status,
                                    error_message=error.error_message,
                                    error_type=error.error_type,
                                    http_status=error.http_status,
                                    retry_count=error.retry_count
                                ))
                        
                        with self._stats_lock:
                            if error.status == RowStatus.RETRY_EXHAUSTED:
                                stats.retry_exhausted += len(row_indices)
                            else:
                                stats.errors += len(row_indices)
                            stats.api_calls_made += 1
                    else:
                        # Successo - api_result dovrebbe essere List[Dict] ma alcune
                        # API restituiscono direttamente un dict (singolo risultato).
                        # Normalizziamo per evitare TypeError su `api_result[0]`.
                        if isinstance(api_result, list):
                            first_row = api_result[0] if api_result else {}
                        elif isinstance(api_result, dict):
                            first_row = api_result
                        else:
                            logger.warning(
                                f"api_result tipo inatteso: {type(api_result).__name__}. "
                                f"Riga ignorata."
                            )
                            first_row = {}
                        for idx in row_indices:
                            self._apply_result(result_df, idx, first_row)
                        
                        with self._stats_lock:
                            # Se era in cache, conta come cached
                            if cache_key in self._cache and len(row_indices) > 0:
                                # Prima riga = successo da API, resto = cached
                                stats.success += 1
                                stats.cached += len(row_indices) - 1
                                stats.api_calls_saved += len(row_indices) - 1
                            else:
                                stats.success += len(row_indices)
                            stats.api_calls_made += 1
                    
                    # Update progress
                    with processed_lock:
                        processed[0] += len(row_indices)
                        self._update_progress(processed[0], total_rows, stats)
                    
                except Exception as e:
                    logger.error(f"[CRASH] Errore in worker: {e}", exc_info=True)
                    cache_key, row_indices = futures[future]
                    with errors_lock:
                        for idx in row_indices:
                            errors.append(RowError(
                                row_index=idx,
                                status=RowStatus.ERROR,
                                error_message=str(e),
                                error_type="worker_error"
                            ))
                    with self._stats_lock:
                        stats.errors += len(row_indices)
        
        # Finalizza statistiche
        stats.total_time_ms = (time.time() - start_time) * 1000
        if self._response_times:
            stats.avg_response_time_ms = sum(self._response_times) / len(self._response_times)
        
        return result_df, stats, errors

    # =========================================================================
    # ESECUZIONE PARALLELA ASYNC (P0 — feature flag ENABLE_ASYNC_ENRICHMENT)
    # httpx.AsyncClient con pool 50 + semaforo per limitare concorrenza.
    # Speedup 10x circa rispetto a ThreadPoolExecutor su dataset medio-grandi.
    # =========================================================================

    async def _execute_parallel_async(
        self,
        df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, EnrichmentStats, List[RowError]]:
        """Versione async di _execute_parallel.

        Usa httpx.AsyncClient dal pool condiviso (app.shared.async_http) e un
        asyncio.Semaphore per limitare la concorrenza a ENRICHMENT_POOL_SIZE (default 50).

        Delega la chiamata HTTP effettiva al metodo sync _call_api_with_retry via
        asyncio.to_thread: questo evita di dover riscrivere tutta la logica di parsing
        risposta, output mapping, template substitution e auth. In pratica il guadagno
        viene dal parallelismo (50 task concorrenti invece di ~5 con thread pool) mentre
        la singola chiamata HTTP resta sync sotto. La Session pool di requests gia
        garantisce il connection reuse, quindi l'overhead e minimo.

        Nota: questa e la versione minima di Fase 2. Quando tutto sara stabile si potra
        passare a chiamate httpx nativamente async per eliminare il to_thread wrapper.
        """
        import asyncio

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

        # Deduplicazione: raggruppa per parametri unici (stessa logica di _execute_parallel)
        if self.options.enable_deduplication:
            unique_groups = self._group_by_params(df)
            logger.info(
                f"Esecuzione async: {len(unique_groups)} combinazioni uniche "
                f"su {total_rows} righe"
            )
        else:
            unique_groups = {str(idx): [idx] for idx in df.index}

        # Semaforo per limitare la concorrenza
        pool_size = int(os.environ.get("ENRICHMENT_POOL_SIZE", "50"))
        pool_size = max(5, min(pool_size, 200))
        semaphore = asyncio.Semaphore(pool_size)
        logger.info(f"[ASYNC] Semaforo concorrenza = {pool_size}")

        processed_counter = [0]
        counter_lock = asyncio.Lock()

        async def _process_group(cache_key: str, row_indices: List) -> Optional[List[RowError]]:
            """Processa un singolo gruppo di righe con parametri identici.

            La chiamata API sync viene eseguita in un thread via asyncio.to_thread,
            cosi non blocca l'event loop.

            Ritorno: None su successo/skip, oppure List[RowError] con UN entry
            per ogni riga del gruppo (parity con il path sync _execute_parallel,
            che replica il RowError su tutti gli idx).
            """
            async with semaphore:
                first_idx = row_indices[0]
                row = df.loc[first_idx]
                # Inizializza params prima del try: in caso di eccezione in
                # _build_params, il blocco except riesce comunque a citarli
                # (anche vuoti) nel RowError.
                params: dict = {}

                try:
                    params = self._build_params(row)

                    # Salta se parametri nulli bloccanti
                    if self._has_null_params(params):
                        if self.options.skip_null_params:
                            stats.skipped += len(row_indices)
                            return None

                    # Cache lookup (deduplicazione tra task concorrenti)
                    api_result = None
                    if self.options.enable_deduplication:
                        cached = self._cache.get(cache_key)
                        if cached and cached != "__in_flight__":
                            api_result = cached
                            stats.cached += len(row_indices)
                            self._cache_hits += len(row_indices)

                    # Se non cached: chiamata API (sync via to_thread)
                    if api_result is None:
                        # Rate limiting: parity con _execute_parallel sync.
                        # RateLimiter.acquire() puo' bloccare via time.sleep:
                        # esecuzione via to_thread evita di bloccare l'event loop.
                        await asyncio.to_thread(self._rate_limiter.acquire)

                        api_result, err_obj = await asyncio.to_thread(
                            self._call_api_with_retry, params, int(first_idx)
                        )

                        if err_obj is not None:
                            stats.errors += len(row_indices)
                            # Parity con sync: replica il RowError per ogni
                            # riga del gruppo preservando http_status/retry_count.
                            return [
                                RowError(
                                    row_index=int(idx),
                                    status=err_obj.status,
                                    error_message=err_obj.error_message,
                                    error_type=err_obj.error_type,
                                    http_status=err_obj.http_status,
                                    retry_count=err_obj.retry_count,
                                    input_params=err_obj.input_params or params,
                                )
                                for idx in row_indices
                            ]

                        if self.options.enable_deduplication and api_result:
                            with self._cache_lock:
                                self._cache[cache_key] = api_result
                                if len(self._cache) > self._CACHE_MAX_SIZE:
                                    keys = list(self._cache.keys())[:self._CACHE_MAX_SIZE // 2]
                                    for k in keys:
                                        self._cache.pop(k, None)

                    # Applica il risultato a tutte le righe del gruppo
                    # (stessa logica del path sync _execute_parallel)
                    if isinstance(api_result, list):
                        first_row = api_result[0] if api_result else {}
                    elif isinstance(api_result, dict):
                        first_row = api_result
                    else:
                        first_row = {}

                    for idx in row_indices:
                        self._apply_result(result_df, idx, first_row)
                    stats.success += len(row_indices)

                    # Update progress
                    async with counter_lock:
                        processed_counter[0] += len(row_indices)
                        if self.progress_callback and processed_counter[0] % 100 == 0:
                            try:
                                self.progress_callback(processed_counter[0], total_rows)
                            except Exception as _cb_err:
                                logger.debug(
                                    f"[ASYNC] progress_callback fallita: {_cb_err}",
                                    op="execute",
                                )

                    return None

                except Exception as e:
                    logger.error(f"[ASYNC] Errore gruppo {cache_key}: {e}", exc_info=True)
                    stats.errors += len(row_indices)
                    # Parity con sync: un RowError per riga del gruppo.
                    return [
                        RowError(
                            row_index=int(idx),
                            status=RowStatus.ERROR,
                            error_message=str(e),
                            error_type="async_exception",
                            input_params=params,
                        )
                        for idx in row_indices
                    ]

        # Lancia tutti i task in parallelo, gestiti dal semaforo
        tasks = [
            _process_group(ck, indices)
            for ck, indices in unique_groups.items()
        ]
        # Chunk i risultati se troppi task (evita di mantenere troppe coroutine in RAM)
        CHUNK = 1000
        for chunk_start in range(0, len(tasks), CHUNK):
            chunk = tasks[chunk_start:chunk_start + CHUNK]
            results = await asyncio.gather(*chunk, return_exceptions=True)
            for r in results:
                if isinstance(r, list):
                    errors.extend(r)
                elif isinstance(r, Exception):
                    logger.error(f"[ASYNC] Exception in task: {r}")

            # Check early stop su troppi errori
            _max_err = self.options.stop_on_error_threshold
            if _max_err > 0 and stats.errors >= _max_err and not self.options.continue_on_error:
                logger.warning(f"[ASYNC] Soglia errori raggiunta ({stats.errors}/{_max_err}), interruzione")
                break

        stats.total_time_ms = (time.time() - start_time) * 1000
        if self._response_times:
            stats.avg_response_time_ms = sum(self._response_times) / len(self._response_times)

        logger.info(
            f"[ASYNC] Completato: {stats.success} ok, {stats.errors} err, "
            f"{stats.cached} cached, {stats.skipped} skipped in {stats.total_time_ms/1000:.1f}s"
        )
        return result_df, stats, errors

    # =========================================================================
    # ESECUZIONE BATCH NATIVA
    # Per API che supportano array di input in una singola chiamata HTTP.
    # Raggruppa le righe in batch di max_size e invia una richiesta per batch.
    # Molto più efficiente del row-by-row per API come geocoding batch.
    # =========================================================================

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
    
    # =========================================================================
    # ESECUZIONE SEQUENZIALE
    # Riga per riga con deduplicazione cache e gestione errori granulare.
    # Strategia default quando non c'è batch nativo né parallelismo.
    # =========================================================================
    
    def _execute_internal(
        self, 
        df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, EnrichmentStats, List[RowError]]:
        """
        Esecuzione interna sequenziale con deduplicazione e gestione errori.
        """
        result_df = df.copy()
        stats = EnrichmentStats(total_rows=len(df))
        errors: List[RowError] = []
        
        # Prepara colonne output
        for mapping in self.output_mapping:
            col_name = mapping.get("column", mapping.get("path"))
            if col_name not in result_df.columns:
                result_df[col_name] = None
        
        # Rate limiting
        delay = 1.0 / self.options.rate_limit if self.options.rate_limit > 0 else 0
        
        total_rows = len(df)
        start_time = time.time()
        
        # Deduplicazione: raggruppa per parametri unici
        if self.options.enable_deduplication:
            unique_groups = self._group_by_params(df)
            logger.info(f"Deduplicazione: {len(unique_groups)} combinazioni uniche su {total_rows} righe", op="enrich")
        else:
            # Senza deduplicazione: ogni riga è un gruppo
            unique_groups = {str(idx): [idx] for idx in df.index}
        
        processed = 0
        _extra_rows: List[Dict] = []  # righe espanse da FeatureCollection o array
        # Token OAuth2 gestito via cache con expires_at (api_utils.get_cached_oauth2_token).
        # Il refresh avviene automaticamente quando serve — non piu counter arbitrario.
        _is_oauth2 = self.auth.get("type") in ("oauth2_password", "oauth2_client_credentials")

        for cache_key, row_indices in unique_groups.items():
            # Per OAuth2: refresh headers (la cache evita HTTP se il token e ancora valido)
            if _is_oauth2:
                self._headers = self._prepare_headers()

            # Prendi la prima riga del gruppo per i parametri
            first_idx = row_indices[0]
            row = df.loc[first_idx]
            
            # Costruisci parametri
            params = self._build_params(row)
            
            # Salta se parametri nulli
            if self._has_null_params(params):
                if self.options.skip_null_params:
                    for idx in row_indices:
                        stats.skipped += 1
                    processed += len(row_indices)
                    self._update_progress(processed, total_rows, stats)
                    continue
                else:
                    # skip_null_params=False -> null è un errore
                    for idx in row_indices:
                        errors.append(RowError(
                            row_index=idx,
                            status=RowStatus.ERROR,
                            error_message="Parametro obbligatorio null",
                            error_type="null_param",
                            input_params=params
                        ))
                        stats.errors += 1
                    if not self.options.continue_on_error:
                        logger.warning(f"Parametro null su riga {first_idx}, continue_on_error=False -> interruzione")
                        processed += len(row_indices)
                        break
                    processed += len(row_indices)
                    self._update_progress(processed, total_rows, stats)
                    continue
            
            # Controlla cache
            if self.options.enable_deduplication and cache_key in self._cache:
                # Cache hit - cached_result è List[Dict]
                cached_result = self._cache[cache_key]
                for idx in row_indices:
                    self._apply_result(result_df, idx, cached_result[0] if cached_result else {})
                    stats.cached += 1
                processed += len(row_indices)
                self._update_progress(processed, total_rows, stats)
                continue
            
            # Chiamata API con retry
            api_result, error = self._call_api_with_retry(params, first_idx)
            stats.api_calls_made += 1
            
            if error:
                # Errore - registra per tutte le righe del gruppo
                for idx in row_indices:
                    errors.append(RowError(
                        row_index=idx,
                        status=error.status,
                        error_message=error.error_message,
                        error_type=error.error_type,
                        input_params=params,
                        http_status=error.http_status,
                        retry_count=error.retry_count
                    ))
                    if error.status == RowStatus.RETRY_EXHAUSTED:
                        stats.retry_exhausted += 1
                        stats.errors += 1  # B-BE-56: conta anche nel threshold
                    else:
                        stats.errors += 1
                # Stop immediato se continue_on_error=False
                if not self.options.continue_on_error:
                    logger.warning(f"Errore su riga {first_idx}, continue_on_error=False -> interruzione")
                    processed += len(row_indices)
                    break
            else:
                # Successo - api_result è ora una List[Dict]
                # Se la lista ha >1 elementi (es. FeatureCollection), esplodi le righe
                if self.options.enable_deduplication:
                    with self._cache_lock:
                        self._cache[cache_key] = api_result
                        if len(self._cache) > self._CACHE_MAX_SIZE:
                            keys = list(self._cache.keys())[:self._CACHE_MAX_SIZE // 2]
                            for k in keys:
                                self._cache.pop(k, None)

                for idx in row_indices:
                    if len(api_result) == 1:
                        # Caso normale: 1 dict -> scrivi nella riga esistente
                        self._apply_result(result_df, idx, api_result[0])
                        stats.success += 1
                    elif len(api_result) > 1:
                        # FeatureCollection o array: prima feature nella riga esistente,
                        # le restanti come righe extra (con i parametri di input replicati)
                        self._apply_result(result_df, idx, api_result[0])
                        input_row_data = result_df.loc[idx].to_dict()
                        for extra_dict in api_result[1:]:
                            new_row = {**input_row_data, **extra_dict}
                            _extra_rows.append(new_row)
                        stats.success += 1

                # Chiamate risparmiate = righe gruppo - 1
                stats.api_calls_saved += len(row_indices) - 1

            processed += len(row_indices)
            self._update_progress(processed, total_rows, stats)

            # Rate limiting
            if delay > 0:
                time.sleep(delay)

            # Check error threshold
            if (self.options.stop_on_error_threshold > 0 and
                stats.errors >= self.options.stop_on_error_threshold):
                logger.warning(f"Raggiunto threshold errori ({stats.errors}), interruzione")
                break

        # Concatena righe espanse (FeatureCollection explosion)
        if _extra_rows:
            extra_df = pd.DataFrame(_extra_rows)
            result_df = pd.concat([result_df, extra_df], ignore_index=True)
            logger.info(f"Esplosione FeatureCollection: aggiunte {len(_extra_rows)} righe extra")

        # Finalizza statistiche
        stats.total_time_ms = (time.time() - start_time) * 1000
        if self._response_times:
            stats.avg_response_time_ms = sum(self._response_times) / len(self._response_times)

        return result_df, stats, errors
    

    # =========================================================================
    # METODI HELPER (post-Phase-5-B3-A)
    # Phase 5 B3-A.2 cutover: cluster TEMPLATE_PARAMS (`_is_template`,
    # `_template_columns`, `_interpolate_template`, `_build_params`,
    # `_has_null_params`) spostato in
    # `app.core.arricchisci.enrichment_template_params.EnrichmentTemplateParamsMixin`.
    # `EnrichmentExecutor` eredita dal mixin (vedi class declaration sopra).
    # I 5 metodi restano accessibili via MRO. Le call-site
    # `self._is_template / _interpolate_template / _build_params /
    # _has_null_params / _template_columns` da `validate_input` /
    # `_execute_*` continuano a risolvere via MRO senza modifiche.
    # `_make_cache_key` (staticmethod) idem (vedi cutover B3-A.2 #2).
    # Restano in questa sezione (gia' nel file): `_call_api_with_retry`
    # (HTTP retry + 429), `_extract_output`, `_explode_rows`,
    # `_auto_flatten`, `_detect_geometry_type`, `_infer_scalar_type`,
    # `_flatten_dict`, `_apply_result`, `_resolve_nested_list`,
    # `_extract_json_value`, `_prepare_headers`, OAuth2 helpers
    # (candidati per future B3-A.3+ extraction).
    # Vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A.
    # =========================================================================

    def _call_api_with_retry(
        self, 
        params: Dict, 
        row_index: int
    ) -> Tuple[Optional[Dict], Optional[RowError]]:
        """
        Chiama API con retry e exponential backoff.
        
        Returns:
            Tuple (risultato, errore) - uno dei due è None
        """
        last_error = None
        
        for attempt in range(self.options.max_retries + 1):
            try:
                start_time = time.time()

                ssl_verify = not getattr(self.options, 'disable_ssl_verify', False)

                # Sostituisci path params nell'URL ({ip}, {coordinates}, ecc.)
                import re
                from urllib.parse import quote as _quote
                call_url = self.url
                call_params = dict(params)
                for pname in re.findall(r'\{(\w+)\}', call_url):
                    if pname in call_params:
                        encoded = _quote(str(call_params.pop(pname)), safe='-._~:@!$&\'()*+,=;/')
                        call_url = call_url.replace(f'{{{pname}}}', encoded)

                # Iniezione token ArcGIS in query string (auth_type=arcgis_token)
                # Il token va come ?token=xxx, NON come Authorization header.
                # La cache su (token_url, username) gestisce l'auto-renewal.
                if self.auth.get("type") == "arcgis_token":
                    from app.shared.api_utils import get_arcgis_token
                    arcgis_tok = get_arcgis_token(self.auth)
                    if arcgis_tok:
                        call_params["token"] = arcgis_tok

                # DEBUG level: per un job di 300 righe × 11 nodi questo produce
                # ~3300 righe di log. A INFO satura journald e rallenta ~400ms per run.
                # Chi deve fare troubleshooting abilita DEBUG sul logger.
                logger.debug(
                    f"API call: {self.method} {sanitize_url_for_log(call_url)} | "
                    f"params={sanitize_for_log(call_params, self.auth)}"
                )

                _session = get_shared_session()
                if self.method == "GET":
                    response = _session.get(
                        call_url,
                        params=call_params,
                        headers=self._headers,
                        timeout=(self.options.connect_timeout, self.options.request_timeout),
                        verify=ssl_verify
                    )
                else:
                    response = _session.post(
                        call_url,
                        json=call_params,
                        headers=self._headers,
                        timeout=(self.options.connect_timeout, self.options.request_timeout),
                        verify=ssl_verify
                    )

                elapsed_ms = (time.time() - start_time) * 1000
                with self._stats_lock:
                    # deque con maxlen=1000: auto-trunca i piu' vecchi
                    self._response_times.append(elapsed_ms)


                # Successo
                if response.status_code == 200:
                    data = response.json()
                    return self._extract_output(data), None
                
                # Rate limited - rispetta Retry-After
                if response.status_code == 429:
                    if self.options.respect_retry_after:
                        retry_after = int(response.headers.get("Retry-After", 60))
                        time.sleep(retry_after)
                    
                    with self._stats_lock:
                        # Conta rate limit hits se presente nelle stats
                        pass
                    
                    last_error = RowError(
                        row_index=row_index,
                        status=RowStatus.ERROR,
                        error_message=f"Rate limited (429)",
                        error_type="rate_limit",
                        http_status=429,
                        retry_count=attempt
                    )
                    continue
                
                # Altri errori retriable
                if response.status_code in self.options.retry_on_status:
                    last_error = RowError(
                        row_index=row_index,
                        status=RowStatus.ERROR,
                        error_message=f"HTTP {response.status_code}",
                        error_type=f"http_{response.status_code}",
                        http_status=response.status_code,
                        retry_count=attempt
                    )
                    
                    # Exponential backoff
                    if attempt < self.options.max_retries:
                        wait_time = (self.options.retry_backoff_factor ** attempt)
                        time.sleep(wait_time)
                    continue
                
                # Errore non retriable
                return None, RowError(
                    row_index=row_index,
                    status=RowStatus.ERROR,
                    error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                    error_type=f"http_{response.status_code}",
                    http_status=response.status_code,
                    retry_count=attempt
                )
                
            except requests.Timeout:
                last_error = RowError(
                    row_index=row_index,
                    status=RowStatus.ERROR,
                    error_message=f"Timeout dopo {self.options.request_timeout}s",
                    error_type="timeout",
                    retry_count=attempt
                )
                
            except requests.RequestException as e:
                last_error = RowError(
                    row_index=row_index,
                    status=RowStatus.ERROR,
                    error_message=str(e),
                    error_type="request_error",
                    retry_count=attempt
                )
            
            # Exponential backoff tra retry
            if attempt < self.options.max_retries:
                wait_time = (self.options.retry_backoff_factor ** attempt)
                time.sleep(wait_time)
        
        # Max retry raggiunto
        if last_error:
            last_error.status = RowStatus.RETRY_EXHAUSTED
        return None, last_error
    

    # Phase 5 B3-A.3 cutover: cluster RESPONSE_PROCESSING (`_extract_output`,
    # `_explode_rows`, `_auto_flatten`, `_detect_geometry_type`,
    # `_infer_scalar_type`, `_flatten_dict`, `_apply_result`) spostato in
    # `EnrichmentResponseProcessingMixin`. I 7 metodi restano accessibili
    # via MRO. Le call-site `self._extract_output / _apply_result` da
    # `_call_api_with_retry / _execute_*` continuano a risolvere via MRO.
    # `_resolve_nested_list` + `_extract_json_value` (cluster B3-A.3
    # parte 2) anch'essi nel mixin (vedi cutover sotto).
    # `_group_by_params` + `_count_unique_combinations` restano qui:
    # candidati per B3-A.7 ExecutionEngines o cluster progress separato.

    def _group_by_params(self, df: pd.DataFrame) -> Dict[str, List[int]]:
        """
        Raggruppa righe per combinazione unica di parametri (deduplicazione).
        
        Genera un hash MD5 per ogni combinazione di parametri (escludendo
        la API key) e raggruppa gli indici riga per hash identico.
        Questo permette di fare una sola chiamata API per N righe identiche.
        
        Returns:
            Dict {cache_key_md5: [lista indici riga con stessi parametri]}
        """
        groups = defaultdict(list)
        
        for idx, row in df.iterrows():
            params = self._build_params(row)
            # Rimuovi API key dalla cache key
            cache_params = {k: v for k, v in params.items() 
                          if k != self.auth.get("key_name", "apiKey")}
            cache_key = self._make_cache_key(cache_params)
            groups[cache_key].append(idx)
        
        return dict(groups)
    
    def _count_unique_combinations(self, df: pd.DataFrame) -> int:
        """Conta combinazioni uniche di parametri."""
        return len(self._group_by_params(df))
    

    # Phase 5 B3-A.2 cutover: `_make_cache_key` (staticmethod, MD5 cache
    # key da dict params) spostato in `EnrichmentTemplateParamsMixin`.
    # Risolve via MRO. Vedi class declaration + import block in testa.

    def _prepare_headers(self) -> Dict:
        """Prepara headers HTTP. Delegato a shared/api_utils."""
        return _shared_prepare_headers(self.auth)

    def _get_oauth2_token(self) -> Optional[str]:
        """Ottiene token OAuth2 con password flow. Delegato a shared/api_utils."""
        return _shared_get_oauth2_token(self.auth)

    def _get_oauth2_client_credentials_token(self) -> Optional[str]:
        """Ottiene token OAuth2 con client_credentials flow. Delegato a shared/api_utils."""
        return _shared_get_oauth2_cc_token(self.auth)


    # Phase 5 B3-A.3 cutover: `_resolve_nested_list` + `_extract_json_value`
    # spostati in `EnrichmentResponseProcessingMixin`. Risolvono via MRO.
    # Vedi class declaration + import block in testa.

    def _update_progress(self, processed: int, total: int, stats: EnrichmentStats):
        """Aggiorna progress callback."""
        if self.progress_callback:
            percent = int((processed / total) * 100) if total > 0 else 0
            message = (
                f"{self.api_name}: {processed}/{total} "
                f"({stats.success} OK, {stats.cached} cache, {stats.errors} errori)"
            )
            self.progress_callback(percent, message)


# =============================================================================
# FUNZIONI HELPER GLOBALI
# =============================================================================

def generate_error_report(
    errors: List[RowError],
    output_path: str,
    auth: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Genera report CSV degli errori di arricchimento.

    Ogni riga del CSV contiene: indice riga originale, status, tipo errore,
    messaggio, HTTP status, numero retry, parametri input, timestamp.
    Il file viene salvato accanto all'output con suffisso '_errors.csv'.

    Phase 4 (P0-01): `input_params` viene redatto via `sanitize_for_log`
    prima della serializzazione. La path CSV pre-fix scriveva in chiaro
    api_key/token/password presenti in `params` (es. api_key in query
    string materializzata da `_build_params`), causando credential leak
    nel file `<output>_errors.csv` distribuito ai consumer del task.
    Ora la stessa redaction usata sul logger debug (`sanitize_for_log`)
    e' applicata anche a questa path. Se il caller passa `auth`, anche
    `auth.key_name` viene oscurato (api_key con nome custom es. `apikey`,
    `subscription-key`).
    """
    if not errors:
        return None

    import csv

    file_path = f"{output_path}_errors.csv"

    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "row_index", "status", "error_type", "error_message",
            "http_status", "retry_count", "input_params", "timestamp"
        ])
        writer.writeheader()

        for error in errors:
            raw_params = getattr(error, "input_params", None) or {}
            safe_params = sanitize_for_log(raw_params, auth)
            writer.writerow({
                "row_index": error.row_index,
                "status": error.status.value,
                "error_type": error.error_type,
                "error_message": error.error_message,
                "http_status": error.http_status or "",
                "retry_count": error.retry_count,
                "input_params": json.dumps(safe_params, ensure_ascii=False),
                "timestamp": error.timestamp
            })

    logger.info(f"Report errori salvato: {file_path}")
    return file_path