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

# Phase 5 B3-A.4 cutover: cluster BATCH_MODE (`_execute_batch_native`,
# `_call_batch_api`, `_build_batch_payload`, `_extract_batch_output`)
# spostato in
# `app.core.arricchisci.enrichment_batch_mode.EnrichmentBatchModeMixin`.
# `EnrichmentExecutor` eredita dal mixin (vedi class declaration sotto).
# I 4 metodi restano accessibili via MRO. Le call-site
# `self._execute_batch_native` da `execute()` (entry point dispatcher)
# continuano a risolvere via MRO senza modifiche. Vedi
# `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A.
from app.core.arricchisci.enrichment_batch_mode import (
    EnrichmentBatchModeMixin,
)

# Phase 5 B3-A.5 cutover: cluster VALIDATION_PREVIEW (`validate_input`,
# `preview`) spostato in
# `app.core.arricchisci.enrichment_validation_preview.EnrichmentValidationPreviewMixin`.
# `EnrichmentExecutor` eredita dal mixin (vedi class declaration sotto).
# I 2 metodi restano accessibili via MRO. `validate_input` chiamato
# da `execute()` interno (line 411 pre-extraction); `preview` entry
# point pubblico API. Vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A.
from app.core.arricchisci.enrichment_validation_preview import (
    EnrichmentValidationPreviewMixin,
)

# Phase 5 B3-A.6 cutover: `_call_api_with_retry` (HTTP per-row retry
# chain con 429+Retry-After + exponential backoff + path params URL
# substitution + ArcGIS token injection) spostato in
# `app.core.arricchisci.enrichment_retry_http.EnrichmentRetryHttpMixin`.
# `EnrichmentExecutor` eredita dal mixin (vedi class declaration sotto).
# I 3 auth helper (`_prepare_headers`, `_get_oauth2_token`,
# `_get_oauth2_client_credentials_token`) restano qui core (3-line
# stub di delega, non chiamati dal retry path).
# Le call-site `self._call_api_with_retry` da `_execute_internal` /
# `_execute_parallel` continuano a risolvere via MRO senza modifiche.
# Vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A.
from app.core.arricchisci.enrichment_retry_http import (
    EnrichmentRetryHttpMixin,
)

# Phase 5 B3-A.7 cutover: cluster EXECUTION_ENGINES
# (`_execute_parallel`, `_execute_parallel_async`, `_execute_internal`)
# spostato in
# `app.core.arricchisci.enrichment_execution_engines.EnrichmentExecutionEnginesMixin`.
# `EnrichmentExecutor` eredita dal mixin (vedi class declaration sotto).
# I 3 metodi runtime engine restano accessibili via MRO. Le call-site
# `self._execute_parallel / _execute_parallel_async / _execute_internal`
# da `execute()` dispatcher continuano a risolvere via MRO. Vedi
# `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A.
from app.core.arricchisci.enrichment_execution_engines import (
    EnrichmentExecutionEnginesMixin,
)

# Phase 5 B3-A.8 cutover: cluster GROUPING (`_group_by_params`,
# `_count_unique_combinations`) spostato in
# `app.core.arricchisci.enrichment_grouping.EnrichmentGroupingMixin`.
# `EnrichmentExecutor` eredita dal mixin (vedi class declaration sotto).
# I 2 metodi restano accessibili via MRO. Le call-site
# `self._group_by_params` da `_execute_*` (ExecutionEngines mixin) +
# `self._count_unique_combinations` da `preview` (ValidationPreview
# mixin) continuano a risolvere via MRO. Vedi
# `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A.
from app.core.arricchisci.enrichment_grouping import (
    EnrichmentGroupingMixin,
)

logger = get_logger(__name__)


# Phase 5 B3-A.8 cleanup: il wrapper module-level `_geojson_to_wkt`
# era dead code post-B3-A.3: `EnrichmentResponseProcessingMixin` ha
# la sua copia locale (`enrichment_response_processing.py:137`) per
# evitare circular import; nessun consumer esterno verificato (grep
# `from enrichment_service import _geojson_to_wkt` → 0 match). Rimosso.


# Phase 5 B3-A.1 cutover: `class RateLimiter` spostato in
# `app.core.arricchisci._rate_limiter`. Re-import in testa al file
# (vedi block sopra) per preservare i path simbolici esistenti.


class EnrichmentExecutor(
    EnrichmentTemplateParamsMixin,
    EnrichmentResponseProcessingMixin,
    EnrichmentBatchModeMixin,
    EnrichmentValidationPreviewMixin,
    EnrichmentRetryHttpMixin,
    EnrichmentExecutionEnginesMixin,
    EnrichmentGroupingMixin,
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
    
    # Phase 5 B3-A.5 cutover: cluster VALIDATION_PREVIEW
    # (`validate_input`, `preview`) spostato in
    # `EnrichmentValidationPreviewMixin`. I 2 metodi restano accessibili
    # via MRO. `validate_input` chiamato da `execute()` sotto;
    # `preview` entry point pubblico API. Vedi class declaration +
    # import block in testa. Logger non definito nel mixin: i 2 metodi
    # non emettono log (verificato pre-extraction).

    
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

        # Feature flag: routing tra sync legacy e async nuovo path.
        # BE-CONFIG-001D: default tipato `bool` in
        # `Settings.DEFAULTS["ENABLE_ASYNC_ENRICHMENT"]=False`. Override
        # env via `Settings._converti_tipo_env` (coercion bool su
        # `1/true/yes`).
        from app.config import settings as _settings
        _async_enabled = bool(_settings.get("ENABLE_ASYNC_ENRICHMENT"))
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
    # ESECUZIONE — RUNTIME ENGINE (post-Phase-5-B3-A.7)
    # Phase 5 B3-A.7 cutover: cluster EXECUTION_ENGINES
    # (`_execute_parallel`, `_execute_parallel_async`, `_execute_internal`)
    # spostato in `EnrichmentExecutionEnginesMixin`. I 3 path runtime
    # engine restano accessibili via MRO. Le call-site `self._execute_*`
    # da `execute()` dispatcher continuano a risolvere via MRO.
    # Vedi class declaration + import block in testa.
    # =========================================================================


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

    # Phase 5 B3-A.6 cutover: `_call_api_with_retry` (HTTP per-row
    # retry chain) spostato in `EnrichmentRetryHttpMixin`. Risolve via
    # MRO. Le call-site `self._call_api_with_retry(params, idx)` da
    # `_execute_internal` / `_execute_parallel` continuano a risolvere
    # via MRO. Vedi class declaration + import block in testa.

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

    # Phase 5 B3-A.8 cutover: cluster GROUPING (`_group_by_params`,
    # `_count_unique_combinations`) spostato in
    # `EnrichmentGroupingMixin`. Risolvono via MRO. Vedi class
    # declaration + import block in testa.

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