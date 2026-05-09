"""
Servizio gestione connessioni API esterne per arricchimento dati.

Keeps API enrichment behavior isolated from orchestration callers.
"""

from typing import Dict, List, Optional

from app.config import settings
from app.utils.logger import get_logger
from app.shared.connections.oauth import (
    prepare_headers as _shared_prepare_headers,
)

# B3-B.9 cleanup (2026-04-27): rimossi 3 import back-compat fragili
# `save_dataframe`, `save_result_blob`, `get_temp_output_path`) dopo
# modernization delle 3 patch dead in
# `tests2/arricchisci/unit/test_api_connector_execute_enrichment_lock.py`
# linee 137, 141, 156 pre-B3-B.9). Le patch erano inattive post-B3-B
# separation (`_save_and_build_dataframe_response` +
# `_build_file_result_response` vivono nel SupportMixin con import
# lazy diversi). Test ora patchano solo il source canonico
# `app.utils.result_store.save_result_blob_from_bytes` per il code path
# in-memory; eventuali test path-based futuri devono patchare il
# canonical `app.utils.result_store.save_result_blob`, NON l'alias
# api_connector ormai rimosso


from app.core.arricchisci.api_connection_management import (
    ApiConnectionManagementMixin,
)


from app.core.arricchisci.api_connection_support import (
    ApiConnectionSupportMixin,
)


# B3-B.4b boundary: cluster SOURCE_LOADING / TENANCY (6 metodi
# `_assert_source_project_in_tenant`, `_recompute_upstream_project`
# `_filesystem_fallback_load_project`, `_load_source_project`
# `_load_file_source`, `_load_static_values`) + SSRF (1 metodo
# `_validate_outbound_url`, `@staticmethod`) spostato in
# `app.core.arricchisci.api_connection_source_loading.ApiConnectionSourceLoadingMixin`
# previous implementation B3-B.4a ha modernizzato
# `tests2/security/unit/test_api_connector_source_loading_core.py`
# da `patch("app.core.arricchisci.api_connector.filter_visible_columns", ...)`
# canonical. Logger canonico
# `get_logger("app.core.arricchisci.api_connector")` (StructuredLogger
# wrapper supporta kwarg `op=` usato in `_assert_source_project_in_tenant`
# State refs `self.results_dir` / `self._current_group_id` /
# `self._preview_limit` accessibili via MRO. Cross-cluster forward refs
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
# `_resolve_file_source_id_path`, `_resolve_file_source_path`
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
    
    def __init__(self):
        self.data_dir = settings.DATA_DIR
        self.results_dir = settings.RESULTS_DIR


    def execute_enrichment(self, project_id: str, canvas: Dict, format: str = 'csv',
                          force_recalculate: bool = False, preview_limit: int = None,
                          run_id: str = None,
                          intermediate_mode_override: Optional[str] = None) -> Dict:
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
        )


    def _prepare_headers(self, auth: Dict, custom_headers: Dict = None) -> Dict:
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
