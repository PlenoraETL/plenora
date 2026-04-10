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

import time
import hashlib
import json
import re
import requests
import pandas as pd
import threading
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional, List, Tuple, Callable
from datetime import datetime, timezone
from collections import defaultdict

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

logger = get_logger(__name__)


def _geojson_to_wkt(geom: dict) -> str:
    """Converte un dict geometry GeoJSON in WKT. Delegato a shared/api_utils."""
    return _shared_geojson_to_wkt(geom)


class RateLimiter:
    """
    Rate limiter thread-safe con algoritmo token bucket.
    
    Garantisce che le chiamate API non superino il rate specificato.
    Usato sia dall'esecuzione sequenziale che parallela per rispettare
    i limiti delle API esterne (es. Nominatim: 1 req/s).
    """
    
    def __init__(self, rate: float):
        """
        Args:
            rate: Richieste al secondo (es. 5.0 = 5 req/s)
        """
        self.rate = rate
        self.interval = 1.0 / rate if rate > 0 else 0
        self.lock = threading.Lock()
        self.last_call = 0.0
    
    def acquire(self):
        """Attende se necessario per rispettare il rate limit."""
        if self.interval == 0:
            return
        
        with self.lock:
            now = time.time()
            time_since_last = now - self.last_call
            
            if time_since_last < self.interval:
                sleep_time = self.interval - time_since_last
                time.sleep(sleep_time)
            
            self.last_call = time.time()


class EnrichmentExecutor:
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
        
        # Cache per deduplicazione (thread-safe)
        self._cache: Dict[str, Dict] = {}
        self._cache_lock = threading.Lock()
        self._cache_hits = 0
        
        # Statistiche (thread-safe)
        self._response_times: List[float] = []
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

        # Controlla valori nulli — solo sui parametri required
        for idx, row in df.iterrows():
            has_null = False
            for api_param, mapping_value in self.param_mapping.items():
                if not mapping_value:
                    continue
                if not _is_blocking(api_param):
                    continue
                # Per ogni colonna richiesta dal mapping (1 per nome, N per template)
                for col in _required_columns(mapping_value):
                    value = row.get(col)
                    if pd.isna(value) or value == "" or value is None:
                        has_null = True
                        break
                if has_null:
                    break
            if has_null:
                null_param_rows.append(idx)
        
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
        
        # Esecuzione: batch nativo > parallelo > sequenziale
        if self.batch_config.enabled:
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
                # Errore su tutto il batch
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
                
                if method == "GET":
                    # GET con batch è raro, ma supportiamo
                    response = requests.get(
                        url,
                        params=payload,
                        headers=self._headers,
                        timeout=(self.options.connect_timeout, self.options.request_timeout)
                    )
                else:
                    # POST è il metodo tipico per batch
                    response = requests.post(
                        url,
                        json=payload,
                        headers=self._headers,
                        timeout=(self.options.connect_timeout, self.options.request_timeout)
                    )
                
                elapsed_ms = (time.time() - start_time) * 1000
                with self._stats_lock:
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
        _token_renew_counter = 0
        _TOKEN_RENEW_EVERY = 500  # righe — stesso valore di api_connector

        for cache_key, row_indices in unique_groups.items():
            # Rinnova il token OAuth2 ogni TOKEN_RENEW_EVERY chiamate API effettive
            # (evita 401 su dataset grandi quando il token scade durante l'esecuzione)
            _token_renew_counter += 1
            if (_token_renew_counter % _TOKEN_RENEW_EVERY == 0 and
                    self.auth.get("type") in ("oauth2_password", "oauth2_client_credentials")):
                new_headers = self._prepare_headers()
                self._headers = new_headers
                logger.info(f"Token OAuth2 rinnovato alla chiamata #{_token_renew_counter}")

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
                    self._cache[cache_key] = api_result

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
    # METODI HELPER
    # Costruzione parametri, chiamate API con retry, estrazione output JSON,
    # deduplicazione tramite cache MD5, autenticazione OAuth2/API key.
    # =========================================================================

    @staticmethod
    def _is_template(value: Any) -> bool:
        """True se il valore è una stringa con almeno un placeholder {colonna}."""
        return isinstance(value, str) and bool(re.search(r"\{(\w+)\}", value))

    @staticmethod
    def _template_columns(template: str) -> List[str]:
        """Estrae i nomi di colonna referenziati nel template."""
        return re.findall(r"\{(\w+)\}", template)

    @staticmethod
    def _interpolate_template(template: str, row: "pd.Series") -> Optional[str]:
        """Sostituisce {col} con i valori della riga.

        Ritorna None se almeno una colonna referenziata è null/NaN
        (così il chiamante può gestirla come parametro nullo).
        """
        out_parts: List[str] = []
        last_end = 0
        for m in re.finditer(r"\{(\w+)\}", template):
            col_name = m.group(1)
            out_parts.append(template[last_end:m.start()])
            if col_name not in row.index:
                # Colonna non esiste → consideriamo il template "non risolvibile"
                return None
            val = row[col_name]
            if val is None:
                return None
            try:
                if pd.isna(val) if not isinstance(val, (list, dict)) else False:
                    return None
            except Exception:
                pass
            # Converti numpy → Python nativo per str()
            try:
                import numpy as _np
                if isinstance(val, (_np.integer,)):
                    val = int(val)
                elif isinstance(val, (_np.floating,)):
                    val = float(val)
                elif isinstance(val, _np.bool_):
                    val = bool(val)
            except Exception:
                pass
            # Formattazione: int/float/bool senza decoration, str raw
            out_parts.append(str(val))
            last_end = m.end()
        out_parts.append(template[last_end:])
        return "".join(out_parts)

    def _build_params(self, row: pd.Series) -> Dict[str, Any]:
        """
        Costruisce parametri API dalla riga DataFrame.

        Ordine di priorità (i successivi sovrascrivono i precedenti):
        1. static_parameters dalla config API (fissi per tutte le righe)
        2. Parametri dinamici dal param_mapping (colonna -> parametro API,
           oppure template stringa con placeholder {col})
        3. API key in query string (se configurata come query param)

        Sintassi del param_mapping value:
          - "nome_colonna"      → legge la colonna direttamente (comportamento storico)
          - "{X},{Y}"           → template: interpola colonne X e Y
          - "address: {via}"    → template misto con prefisso fisso
        """
        params = {}

        # 1. Prima aggiungi static_parameters dalla config API
        static_params = self.api_config.get("static_parameters", {})
        if static_params:
            params.update(static_params)

        # 2. Poi aggiungi parametri dinamici dal mapping
        for api_param, mapping_value in self.param_mapping.items():
            if not mapping_value:
                continue

            # ── 2a. Template con placeholder {col} ──
            if self._is_template(mapping_value):
                interpolated = self._interpolate_template(mapping_value, row)
                if interpolated is None:
                    # Una delle colonne referenziate è null o non esiste
                    if self._has_param_definitions and api_param not in self._required_param_names:
                        continue
                    params[api_param] = None
                else:
                    params[api_param] = interpolated
                continue

            # ── 2b. Mapping classico: nome di una colonna ──
            if mapping_value not in row.index:
                continue
            value = row[mapping_value]
            if pd.isna(value) if not isinstance(value, (list, dict)) else False:
                # Parametro null: se è opzionale (e abbiamo definizioni dei parametri),
                # NON lo aggiungiamo affatto — così l'API riceve solo i campi presenti
                # e _has_null_params non lo segnala come bloccante.
                if self._has_param_definitions and api_param not in self._required_param_names:
                    continue
                params[api_param] = None
            elif isinstance(value, (bool, int, float)):
                # Preserva tipo nativo Python: {"id": 10} non diventa {"id": "10"}
                params[api_param] = value
            else:
                # numpy int64/float64 -> converti in tipo Python nativo
                import numpy as np
                if isinstance(value, (np.integer,)):
                    params[api_param] = int(value)
                elif isinstance(value, (np.floating,)):
                    params[api_param] = float(value)
                elif isinstance(value, np.bool_):
                    params[api_param] = bool(value)
                else:
                    params[api_param] = str(value)

        # 3. API Key in query string
        if self.auth.get("type") == "api_key" and self.auth.get("location") == "query":
            params[self.auth.get("key_name", "apiKey")] = self.auth.get("key_value", "")

        return params
    
    def _has_null_params(self, params: Dict) -> bool:
        """Verifica se ci sono parametri OBBLIGATORI nulli (esclusa API key).

        Se le definizioni dei parametri sono disponibili (api_config["parameters"]),
        controlla solo quelli con required=True. Se non lo sono, fallback al
        comportamento legacy che blocca su qualsiasi null.
        """
        api_key_name = self.auth.get("key_name", "apiKey") if self.auth.get("type") == "api_key" else None

        for key, value in params.items():
            if key == api_key_name:
                continue
            # Se abbiamo le definizioni dei parametri, ignora gli opzionali
            if self._has_param_definitions and key not in self._required_param_names:
                continue
            if value is None or value == "" or value == "None":
                return True
        return False
    
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

                logger.info(
                    f"API call: {self.method} {sanitize_url_for_log(call_url)} | "
                    f"params={sanitize_for_log(call_params, self.auth)}"
                )

                if self.method == "GET":
                    response = requests.get(
                        call_url,
                        params=call_params,
                        headers=self._headers,
                        timeout=(self.options.connect_timeout, self.options.request_timeout),
                        verify=ssl_verify
                    )
                else:
                    response = requests.post(
                        call_url,
                        json=call_params,
                        headers=self._headers,
                        timeout=(self.options.connect_timeout, self.options.request_timeout),
                        verify=ssl_verify
                    )

                elapsed_ms = (time.time() - start_time) * 1000
                with self._stats_lock:
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
    
    def _extract_output(self, data: Any) -> List[Dict]:
        """Estrae campi output dalla response. Restituisce SEMPRE una lista di dict.

        - Con output_mapping vuoto (auto-flatten, B-BE-27):
            - GeoJSON FeatureCollection -> lista con 1 dict per feature (esplosione)
            - Dict generico -> lista con 1 dict (chiavi di primo livello come colonne)
            - List -> lista di dict (1 dict per elemento se sono dict, altrimenti 1 dict con _response)
        - Con output_mapping configurato:
            - Path con [] o [*] (es. "features[].properties.x") -> multi-row, 1 dict per elemento
            - Path con livelli annidati diversi (es. "features[*].nome" + "features[*].valori[*].anno")
              -> prodotto cartesiano: 1 riga per ogni combinazione (feature, valore)
            - Altrimenti -> lista con 1 dict (comportamento standard)
        """
        if not self.output_mapping:
            return self._auto_flatten(data)

        # Normalizza [*] → [] per uniformare la logica multi-row
        def _norm(path: str) -> str:
            return path.replace("[*]", "[]") if path else path

        # Trova il path più profondo (max numero di [])
        max_depth = 0
        normalized_mappings = []
        for mapping in self.output_mapping:
            path = _norm(mapping.get("path", ""))
            depth = path.count("[]")
            max_depth = max(max_depth, depth)
            normalized_mappings.append({
                "path": path,
                "column": mapping.get("column") or mapping.get("path", ""),
                "depth": depth,
            })

        # ── SINGLE-ROW: nessun path usa [] ──
        if max_depth == 0:
            result = {}
            for m in normalized_mappings:
                result[m["column"]] = self._extract_json_value(data, m["path"])

            if result and all(v is None for v in result.values()):
                # Tutti None è legittimo quando il punto non ha dati per questo
                # servizio (es. Tiranti idrici NoData, Frana PAI fuori zona).
                # NON fare fallback ad auto_flatten: scaricherebbe le chiavi
                # della struttura response (results, fields, fieldAliases, ecc.)
                # come colonne fantasma nel DataFrame. Ritorna la riga con None.
                logger.info(
                    f"output_mapping ha prodotto tutti None per path={[m['path'] for m in normalized_mappings]}. "
                    f"Risposta top-level keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
                )
            return [result]

        # ── MULTI-ROW: esplora ricorsivamente i livelli di [] e costruisce righe ──
        # Per ogni livello k (1..max_depth) raccoglie il "contesto" (elemento corrente
        # a quel livello), poi per ogni mapping estrae il valore dal contesto giusto.
        rows = []
        self._explode_rows(data, normalized_mappings, contexts=[data], rows=rows, current_depth=0, max_depth=max_depth)
        return rows if rows else [{}]

    def _explode_rows(self, current_data: Any, mappings: List[Dict], contexts: List[Any],
                       rows: List[Dict], current_depth: int, max_depth: int):
        """Esplora ricorsivamente i livelli di [] producendo una riga per ogni foglia.

        contexts[k] = elemento corrente al livello k (0 = root, 1 = primo []-iterator, ...)
        Una volta raggiunto max_depth, costruisce una riga leggendo ogni mapping dal
        contesto corrispondente alla sua profondità.
        """
        if current_depth == max_depth:
            row = {}
            for m in mappings:
                path = m["path"]
                depth = m["depth"]
                # Il contesto da cui leggere il valore è contexts[depth]
                # Il suffix è la porzione del path dopo l'ultimo []
                if depth == 0:
                    suffix = path
                    ctx = contexts[0]
                else:
                    # Dopo il depth-esimo [], quello che resta è il suffix
                    parts = path.split("[]", depth)
                    suffix = parts[-1].lstrip(".") if len(parts) > depth else ""
                    ctx = contexts[depth]
                row[m["column"]] = self._extract_json_value(ctx, suffix) if suffix else ctx
            rows.append(row)
            return

        # Trova la base del prossimo livello: prefix fino al (current_depth+1)-esimo []
        # Usa il primo mapping che ha almeno quella profondità
        next_base = None
        for m in mappings:
            if m["depth"] > current_depth:
                parts = m["path"].split("[]", current_depth + 1)
                # parts[0..current_depth] sono i prefix separati da []
                # Il segmento da applicare al contesto corrente è parts[current_depth]
                next_base = parts[current_depth].rstrip(".")
                break

        if next_base is None:
            return

        # Estrai la lista al livello corrente
        items = self._extract_json_value(current_data, next_base) if next_base else current_data
        if not isinstance(items, list):
            return

        for item in items:
            new_contexts = contexts + [item]
            self._explode_rows(item, mappings, new_contexts, rows, current_depth + 1, max_depth)

    def _auto_flatten(self, data: Any) -> List[Dict]:
        """Auto-flatten senza output_mapping: restituisce lista di righe.
        Popola self.detected_column_types con i tipi rilevati (es. geometry:point).
        """
        # GeoJSON FeatureCollection -> 1 riga per feature
        if isinstance(data, dict) and data.get("type") == "FeatureCollection":
            rows = []
            features = data.get("features", [])
            for feature in features:
                row = {}
                props = feature.get("properties") or {}
                for k, v in props.items():
                    # Fix: interi rimangono interi (pandas li upcastava a float con NaN)
                    row[k] = int(v) if isinstance(v, float) and v == int(v) else v
                geom = feature.get("geometry")
                if geom:
                    row["geometry"] = _geojson_to_wkt(geom)
                    # Rileva tipo geometria
                    geom_type = geom.get("type", "").lower()
                    self._detect_geometry_type("geometry", geom_type)
                if "id" in feature:
                    row.setdefault("id", feature["id"])
                rows.append(row)
            # Rileva tipi delle properties dalla prima feature
            if features:
                first_props = features[0].get("properties") or {}
                for k, v in first_props.items():
                    if k not in self.detected_column_types:
                        self.detected_column_types[k] = self._infer_scalar_type(v)
                if "id" in features[0]:
                    self.detected_column_types.setdefault("id", "int")
            return rows if rows else [{}]

        # Lista di dict -> 1 riga per elemento
        if isinstance(data, list):
            if data and isinstance(data[0], dict):
                rows = [self._flatten_dict(item) for item in data]
                # Rileva tipi dal primo elemento
                for k, v in data[0].items():
                    self.detected_column_types[k] = self._infer_scalar_type(v)
                return rows
            return [{"_response": json.dumps(data, ensure_ascii=False)}]

        # Dict generico -> 1 riga, primo livello come colonne
        if isinstance(data, dict):
            for k, v in data.items():
                self.detected_column_types[k] = self._infer_scalar_type(v)
            return [self._flatten_dict(data)]

        return [{"_response": str(data)}]

    def _detect_geometry_type(self, col_name: str, geom_type: str):
        """Mappa tipo GeoJSON -> tipo sistema (geometry:point, geometry:polygon, ecc.)"""
        geo_map = {
            "point": "geometry:point",
            "multipoint": "geometry:multipoint",
            "linestring": "geometry:line",
            "multilinestring": "geometry:line",
            "polygon": "geometry:polygon",
            "multipolygon": "geometry:multipolygon",
            "geometrycollection": "geometry",
        }
        self.detected_column_types[col_name] = geo_map.get(geom_type, "geometry")

    def _infer_scalar_type(self, value: Any) -> str:
        """Inferisce tipo colonna da un valore scalare."""
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "real"
        if isinstance(value, dict) and value.get("type") in (
            "Point", "MultiPoint", "LineString", "MultiLineString",
            "Polygon", "MultiPolygon", "GeometryCollection"
        ):
            self._detect_geometry_type("_tmp", value.get("type", "").lower())
            return self.detected_column_types.pop("_tmp", "geometry")
        return "str"

    def _flatten_dict(self, data: Dict) -> Dict:
        """Appiattisce un dict: valori scalari diretti, dict/list -> JSON string.
        Fix: interi floats (es 114367654.0) vengono convertiti a int.
        """
        result = {}
        for key, value in data.items():
            if isinstance(value, float) and value == int(value):
                result[key] = int(value)
            elif isinstance(value, (dict, list)):
                result[key] = json.dumps(value, ensure_ascii=False)
            else:
                result[key] = value
        return result
    
    def _apply_result(self, df: pd.DataFrame, idx: int, result: Dict):
        """Applica risultato API a una riga del DataFrame."""
        for col_name, value in result.items():
            df.at[idx, col_name] = value
    
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
    
    @staticmethod
    def _make_cache_key(params: Dict) -> str:
        """Genera chiave cache da parametri."""
        # Ordina per consistenza
        sorted_items = sorted(params.items())
        param_str = json.dumps(sorted_items, sort_keys=True)
        return hashlib.md5(param_str.encode()).hexdigest()
    
    def _prepare_headers(self) -> Dict:
        """Prepara headers HTTP. Delegato a shared/api_utils."""
        return _shared_prepare_headers(self.auth)

    def _get_oauth2_token(self) -> Optional[str]:
        """Ottiene token OAuth2 con password flow. Delegato a shared/api_utils."""
        return _shared_get_oauth2_token(self.auth)

    def _get_oauth2_client_credentials_token(self) -> Optional[str]:
        """Ottiene token OAuth2 con client_credentials flow. Delegato a shared/api_utils."""
        return _shared_get_oauth2_cc_token(self.auth)

    def _resolve_nested_list(self, data: Any, path: str) -> List[Any]:
        """Risolve un path con [] annidati, restituendo la lista "flat" di tutti
        gli elementi al livello più profondo.

        Es: path = 'features[].properties.valori'
            data = {features: [{properties: {valori: [1,2]}}, {properties: {valori: [3]}}]}
            → [1, 2, 3]

        Accetta sia [] che [*] come marker di iterazione.
        Se non ci sono marker, applica extract_json_value normale.
        """
        # Normalizza [*] → []
        path = path.replace("[*]", "[]") if path else path
        if "[]" not in path:
            return self._extract_json_value(data, path)

        # Split sul primo [] per processare a strati
        prefix, _, rest = path.partition("[]")
        prefix = prefix.rstrip(".")
        rest = rest.lstrip(".")

        # Estrai la lista al livello corrente
        items = self._extract_json_value(data, prefix) if prefix else data
        if not isinstance(items, list):
            return []

        # Se non c'è altro dopo [], restituisci la lista
        if not rest:
            return items

        # Altrimenti, per ogni elemento, ricorsa e appiattisci
        result = []
        for item in items:
            sub = self._resolve_nested_list(item, rest)
            if isinstance(sub, list):
                result.extend(sub)
            else:
                result.append(sub)
        return result

    def _extract_json_value(self, data: Any, path: str) -> Any:
        """Estrae valore da JSON usando dot notation. Delegato a shared/api_utils."""
        return _shared_extract_json_value(data, path)
    
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

def generate_error_report(errors: List[RowError], output_path: str) -> str:
    """
    Genera report CSV degli errori di arricchimento.
    
    Ogni riga del CSV contiene: indice riga originale, status, tipo errore,
    messaggio, HTTP status, numero retry, parametri input, timestamp.
    Il file viene salvato accanto all'output con suffisso '_errors.csv'.
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
            writer.writerow({
                "row_index": error.row_index,
                "status": error.status.value,
                "error_type": error.error_type,
                "error_message": error.error_message,
                "http_status": error.http_status or "",
                "retry_count": error.retry_count,
                "input_params": json.dumps(error.input_params) if hasattr(error, 'input_params') else "{}",
                "timestamp": error.timestamp
            })
    
    logger.info(f"Report errori salvato: {file_path}")
    return file_path