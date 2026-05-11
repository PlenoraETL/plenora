"""
Servizio di esecuzione arricchimento dati via API esterne.

Keeps API enrichment behavior isolated from orchestration callers.
"""

import csv

import json
import pandas as pd
import threading
import urllib3
from typing import Dict, Any, Optional, List, Tuple, Callable
from datetime import datetime, timezone
from collections import deque

# Disabilita SSL warnings (fix Windows
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from app.core.arricchisci.models import (
    RowError, EnrichmentStats, EnrichmentOptions,
    EnrichmentResult, BatchConfig
)
from app.utils.logger import get_logger
from app.core.arricchisci._rate_limiter import RateLimiter as _RateLimiter
from app.shared.connections.oauth import (
    prepare_headers as _shared_prepare_headers,
)
from app.observability.log_sanitization import (
    sanitize_for_log,
)

from app.core.arricchisci.enrichment_template_params import (
    EnrichmentTemplateParamsMixin,
)

from app.core.arricchisci.enrichment_response_processing import (
    EnrichmentResponseProcessingMixin,
)
from app.core.arricchisci.response_type_detector import ResponseTypeDetector

from app.core.arricchisci.enrichment_batch_mode import (
    EnrichmentBatchModeMixin,
)

from app.core.arricchisci.enrichment_validation_preview import (
    EnrichmentValidationPreviewMixin,
)

# B3-A.6 boundary: `_call_api_with_retry` (HTTP per-row retry
# chain con 429+Retry-After + exponential backoff + path params URL
# substitution + ArcGIS token injection) spostato in
# `app.core.arricchisci.enrichment_retry_http.EnrichmentRetryHttpMixin`
# `EnrichmentExecutor` eredita dal mixin (vedi class declaration sotto
# Auth header construction stays as a narrow delegator to shared OAuth helpers.
# Le call-site `self._call_api_with_retry` da `_execute_internal` /
# `_execute_parallel` continuano a risolvere via MRO senza modifiche
from app.core.arricchisci.enrichment_retry_http import (
    EnrichmentRetryHttpMixin,
)

from app.core.arricchisci.enrichment_execution_engines import (
    EnrichmentExecutionEnginesMixin,
)

from app.core.arricchisci.enrichment_grouping import (
    EnrichmentGroupingMixin,
)

logger = get_logger(__name__)


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
        # API parameter defaults are used unless the node overrides them.
        derived_mapping: Dict[str, Any] = {}
        for pdef in (api_config.get("parameters") or []):
            if not isinstance(pdef, dict):
                continue
            name = pdef.get("name")
            col = pdef.get("column")
            src = pdef.get("source")
            if name and col and (src in (None, "", "column")):
                derived_mapping[name] = col
        if param_mapping:
            derived_mapping.update(param_mapping)
        self.param_mapping = derived_mapping
        self.options = options or EnrichmentOptions()
        self.progress_callback = progress_callback
        
        # Cache per deduplicazione (thread-safe, bounded LRU-style
        self._cache: Dict[str, Dict] = {}
        self._cache_lock = threading.Lock()
        self._cache_hits = 0
        self._CACHE_MAX_SIZE = 5000
        
        # Statistiche (thread-safe). deque(maxlen=1000) auto-trunca i
        # response times piu' vecchi invece di accumulare indefinitamente
        # tra arricchimenti successivi eseguiti dalla stessa istanza (memory
        # leak lento con executor pool riusato dai worker Celery
        self._response_times: deque = deque(maxlen=1000)
        self._stats_lock = threading.Lock()
        self._rate_limit_hits_observed = 0
        
        # Estrai config API
        endpoint = api_config.get("endpoint", {})
        self.url = endpoint.get("url", "")
        self.method = endpoint.get("method", "GET").upper()
        # Accept both OpenAPI-style and runtime api_key field names.
        raw_auth = dict(api_config.get("auth") or {})
        if raw_auth.get("type") == "api_key":
            if "in" in raw_auth and "location" not in raw_auth:
                raw_auth["location"] = raw_auth["in"]
            if "name" in raw_auth and "key_name" not in raw_auth:
                raw_auth["key_name"] = raw_auth["name"]
            if "value" in raw_auth and "key_value" not in raw_auth:
                raw_auth["key_value"] = raw_auth["value"]
        self.auth = raw_auth
        self.output_mapping = api_config.get("output_mapping", [])
        self.api_name = api_config.get("name", "API")

        # Set di parametri "required": serve a _has_null_params per skippare
        # solo quando manca un parametro davvero obbligatorio (gli opzionali null
        # vengono semplicemente omessi dalla chiamata
        # Se l'info non è disponibile (parameters vuoto), fallback al comportamento
        # NOTE: tutti i parametri sono trattati come required
        params_def = api_config.get("parameters") or []
        self._required_param_names = {
            p.get("name") for p in params_def
            if isinstance(p, dict) and p.get("required") and p.get("name")
        }
        self._has_param_definitions = bool(params_def)
        
        # Headers preparati
        self._headers = self._prepare_headers()

        # Tipi colonna rilevati durante auto-flatten (usato per aggiornare schema_fields
        self.detected_column_types: Dict[str, str] = {}
        self._response_type_detector_collaborator = ResponseTypeDetector(self.detected_column_types)
        
        # Rate limiter
        self._rate_limiter = _RateLimiter(self.options.rate_limit)
        
        batch_data = api_config.get("batch", {})
        self.batch_config = BatchConfig.from_dict(batch_data)
    
    # API PUBBLICA
    # Metodi chiamabili dall'esterno: validate_input, preview, execute
    # L'ordine tipico è: validate -> preview (opzionale) -> execute
    

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

        if self.batch_config.enabled:
            result_df, stats, errors = self._execute_batch_native(df)
        elif self._should_use_parallel_engine():
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
    

    def _prepare_headers(self) -> Dict:
        """Prepara headers HTTP. Delegato a shared/connections/oauth."""
        return _shared_prepare_headers(self.auth)

    def _update_progress(self, processed: int, total: int, stats: EnrichmentStats):
        """Aggiorna progress callback."""
        if self.progress_callback:
            percent = int((processed / total) * 100) if total > 0 else 0
            message = (
                f"{self.api_name}: {processed}/{total} "
                f"({stats.success} OK, {stats.cached} cache, {stats.errors} errori)"
            )
            self.progress_callback(percent, message)


# FUNZIONI HELPER GLOBALI

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

    `input_params` viene redatto via `sanitize_for_log`
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
