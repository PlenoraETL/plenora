"""
Servizio gestione connessioni API esterne per arricchimento dati.

Keeps API enrichment behavior isolated from orchestration callers.
"""

from typing import Any, Callable, Optional

from app.utils.logger import get_logger
from app.core.arricchisci.types import JsonObject
from app.core.arricchisci.execution_context import ArricchisciExecutionContext
from app.shared.contracts.payloads import HeadersMap
from app.shared.connections.oauth import (
    prepare_headers as _shared_prepare_headers,
)

# Import mantenuti stretti: questo modulo non deve trascinare helper di
# persistenza non usati. I test patchano i source canonici in
# `app.core.results.blob_writer`.


from app.core.arricchisci.api_connection_management import (
    ApiConnectionManagementMixin,
)


from app.core.arricchisci.api_connection_support import (
    ApiConnectionSupportMixin,
)


# B3-B.4b boundary: cluster SOURCE_LOADING / TENANCY (5 metodi
# `_assert_source_project_in_tenant`, `_recompute_upstream_project`
# `_load_source_project`, `_load_file_source`, `_load_static_values`) + SSRF (1 metodo
# `_validate_outbound_url`, `@staticmethod`) spostato in
# `app.core.arricchisci.api_connection_source_loading.ApiConnectionSourceLoadingMixin`
# previous implementation B3-B.4a ha modernizzato
# `tests2/security/unit/test_api_connector_source_loading_core.py`
# da `patch("app.core.arricchisci.api_connector.filter_visible_columns", ...)`
# canonical. Logger canonico
# `get_logger("app.core.arricchisci.api_connector")` (StructuredLogger
# wrapper supporta kwarg `op=` usato in `_assert_source_project_in_tenant`
# Cross-cluster forward refs
# `_validate_outbound_url` chiamato da `_execute_rest_file` core (futuro
# B3-B.5/B3-B.6) → MRO trasparente. `_load_*` chiamati da
# `executor.py:170,173,176` instance-level → MRO trasparente. Pattern
# mixin ratificato in B2 + B3-A + B3-B.2b + B3-B.3. Vedi
from app.core.arricchisci.api_connection_source_loading import (
    ApiConnectionSourceLoadingMixin,
)


# B3-B.5b boundary: cluster REST_FILE PIPELINE (6 metodi
# `_parse_http_error_body`, `_download_binary_response`
# `_build_rest_file_multipart`, `_parse_rest_file_config`
# `_rest_file_error`, `_execute_rest_file`) + FILE_SOURCE RESOLUTION
# 4 metodi: `_assert_file_source_group_access`
# `_resolve_file_source_id_bytes`, `_resolve_file_source_path`
# `_resolve_input_path_for_file_processor`) spostato in
# `app.core.arricchisci.api_connection_rest_file.ApiConnectionRestFileMixin`
# previous implementation B3-B.5a ha modernizzato 3 patch sites
# `patch("app.core.arricchisci.api_connector.requests.post", ...)` →
# `patch("requests.post", ...)` canonical (in
# `tests2/arricchisci/unit/test_api_connector_rest_file_error_taxonomy.py`
# e `tests2/security/unit/test_temp_lifecycle_core.py`
# Behavior-equivalent rewrite dichiarato: 8 forward-ref classe
# `ApiConnectionService._rest_file_error(...)` → `self._rest_file_error(...)`
# in `_build_rest_file_multipart` (×4) e `_parse_rest_file_config` (×4
# Necessario per evitare import circolare nel mixin module; runtime
# identico per staticmethod (descriptor protocol). Vedi docstring del
# mixin per dettagli completi. 3 `@staticmethod` preservati. Logger
# canonico `get_logger("app.core.arricchisci.api_connector")`. Cross
# cluster forward refs risolti via MRO: `_validate_outbound_url` ←
# `_parse_rest_file_config` (B3-B.4b mixin), `_prepare_headers` ←
# `_execute_rest_file` (core). Pattern mixin ratificato in B2 + B3-A +
# B3-B.2b + B3-B.3 + B3-B.4b. Vedi
from app.core.arricchisci.api_connection_rest_file import (
    ApiConnectionRestFileMixin,
)


from app.core.arricchisci.api_connection_generator_enricher import (
    ApiConnectionGeneratorEnricherMixin,
)


logger = get_logger(__name__)

class ApiConnectionService(
    ApiConnectionManagementMixin,
    ApiConnectionSupportMixin,
    ApiConnectionSourceLoadingMixin,
    ApiConnectionRestFileMixin,
    ApiConnectionGeneratorEnricherMixin,
):
    """
    Servizio per gestire le connessioni API esterne.
    
    Ciclo di vita di una connessione:
    1. create → l'utente configura endpoint, auth, parametri, output mapping
    2. test → chiamata di prova con parametri test per verificare funzionamento
    3. execute → esecuzione completa su DataFrame (riga per riga o batch)
    
    Tipi API supportati:
    - enricher: arricchisce righe esistenti (1 chiamata per riga)
    - generator: genera un DataFrame dalla risposta (1 sola chiamata)
    
    Tipi connessione:
    - manual: configurazione manuale endpoint + parametri
    - template: basata su template preconfigurato (Nominatim, ORS, etc.)
    - datasource: sorgente dati esterna (WFS, REST catalog)
    """
    
    def execute_enrichment(self, project_id: str, canvas: JsonObject, format: str = 'csv',
                          force_recalculate: bool = False, preview_limit: int = None,
                          run_id: str = None,
                          intermediate_mode_override: Optional[str] = None,
                          execution_context: Optional[ArricchisciExecutionContext] = None,
                          finalizer: Optional[Callable[[Any, str, str, str], JsonObject]] = None) -> JsonObject:
        """Public service facade for canvas enrichment execution.

        The module-level orchestrator contains the execution algorithm;
        this service method is the stable entry point used by runtime
        callers that already depend on `ApiConnectionService`.
        """
        from app.core.arricchisci.canvas_orchestrator import (
            execute_canvas_enrichment,
        )
        return execute_canvas_enrichment(
            self,
            project_id=project_id,
            canvas=canvas,
            format=format,
            force_recalculate=force_recalculate,
            preview_limit=preview_limit,
            run_id=run_id,
            intermediate_mode_override=intermediate_mode_override,
            execution_context=execution_context,
            **({"finalizer": finalizer} if finalizer is not None else {}),
        )


    def _prepare_headers(
        self,
        auth: JsonObject,
        custom_headers: Optional[HeadersMap] = None,
    ) -> HeadersMap:
        """Prepara headers HTTP per chiamata API. Delegato a shared/api_utils."""
        return _shared_prepare_headers(auth, custom_headers)

    # B3-B.7 cleanup: rimossi `_get_oauth2_client_credentials_token` e
    # `_get_oauth2_token` come dead code (prova forte: 0 self-call sites
    # in api_connector, 0 external refs in app/, 0 test patches, 0 direct
    # imports. EnrichmentExecutor mantiene helper propri, ma ora delegano
    # direttamente a `app.shared.connections.oauth.get_cached_oauth2_token`.
    # Vedi B3-B.3 mini-scan `_get_oauth2_*` dead-code candidate flag.


# Usato da api/enrichment.py e main.py per accedere al servizio
api_connection_service = ApiConnectionService()
