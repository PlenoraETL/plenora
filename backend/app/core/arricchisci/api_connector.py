"""
Servizio gestione connessioni API esterne per arricchimento dati.

Architettura
============
Gestisce il ciclo di vita delle API connections usate dal modulo
ARRICCHISCI: creazione, configurazione, test, esecuzione, salvataggio
risultati. Ogni connection vive come row in `plenora.api_connections`
(colonna `config jsonb`); i campi sensibili (password, client_secret,
token, ...) sono cifrati at-rest via `app.shared.crypto`.

Componenti:
- ApiConnectionService: classe principale con operazioni CRUD + esecuzione

Sezioni principali:
- CRUD: list, get, create, update, delete connessioni API
- TEST API: test singola chiamata con parsing risposta JSON per validazione
- EXECUTE ENRICHMENT: esecuzione completa su DataFrame (riga per riga)

Flusso di esecuzione enrichment:
1. Carica DataFrame sorgente (da progetto ESTRAI/MANIPOLA o file)
2. Per ogni riga: costruisci URL con variabili template → chiama API → parsa risposta
3. Arricchisci DataFrame con colonne output mappate dalla risposta JSON
4. Salva risultato come file (CSV/GeoJSON/etc.)
5. Aggiorna tracking progresso in tempo reale

Supporto autenticazione:
- API key (header o query string)
- OAuth2 password grant (token_url + username/password)
- Headers custom

Integrazione con altri moduli:
- utils/dataframe.py: caricamento/salvataggio DataFrame
- utils/template_variables.py: sostituzione variabili nei parametri
- utils/response_parser.py: parsing strutturato risposte JSON
"""

from typing import Dict, List, Optional

from app.config import settings
from app.utils.logger import get_logger
from app.shared.api_utils import (
    prepare_headers as _shared_prepare_headers,
)

# KEEP_COMPAT: i lock test
# `tests2/arricchisci/unit/test_init_execution_context_fail_closed.py` e
# `tests2/arricchisci/unit/test_enrichment_task_tenant_binding_lock.py`
# fanno `monkeypatch.setattr(api_connector_mod.db_svc, "get_project",
# ...)`. Il body reale di `init_execution_context` vive in
# `canvas_orchestrator.py` ma legge `db_svc.get_project` dal medesimo
# modulo Python globale (`sys.modules['app.db.db_service']`), quindi
# il patch via `api_connector_mod.db_svc` propaga (entrambi gli import
# referenziano lo stesso oggetto modulo).
import app.db.db_service as db_svc  # noqa: F401  (test-monkeypatch surface)

# B3-B.9 cleanup (2026-04-27): rimossi 3 import back-compat fragili
# (`save_dataframe`, `save_result_blob`, `get_temp_output_path`) dopo
# modernization delle 3 patch dead in
# `tests2/arricchisci/unit/test_api_connector_execute_enrichment_lock.py`
# (linee 137, 141, 156 pre-B3-B.9). Le patch erano inattive post-B3-B
# extraction (`_save_and_build_dataframe_response` +
# `_build_file_result_response` vivono nel SupportMixin con import
# lazy diversi). Test ora patchano solo il source canonico
# `app.utils.result_store.save_result_blob_from_bytes` per il code path
# in-memory; eventuali test path-based futuri devono patchare il
# canonical `app.utils.result_store.save_result_blob`, NON l'alias
# api_connector ormai rimosso.

# Phase 5 B3-B.1 cutover: cluster MODULE-LEVEL HELPERS
# (`_AUTH_SENSITIVE_FIELDS` constant + `_encrypt_auth_in_config` +
# `_decrypt_auth_in_config` + `_nd` node-data accessor) spostato in
# `app.core.arricchisci._api_connection_helpers`. Direct callers
# import dal modulo canonico; il back-compat re-export e' stato
# rimosso da `BE-GO-READY-CONNECTION-ENGINE-008-API-CONNECTOR-ALIAS-CLUSTER-CUT`
# dopo aver migrato i 2 test contract diretti (decrypt_policy, contract)
# al canonical path. Vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-B.


# Phase 5 B3-B.2b cutover: cluster API_CONNECTION_MANAGEMENT
# (CRUD lifecycle + TEST_CONNECTION, 11 metodi: `_mask_credentials`,
# `list_api_connections`, `list_api_connections_decrypted`,
# `get_api_connection`, `create_api_connection`,
# `update_api_connection`, `delete_api_connection`,
# `_build_test_call_params`, `_inject_auth_query_params`,
# `_parse_test_response_json`, `test_api_connection`) spostato in
# `app.core.arricchisci.api_connection_management.ApiConnectionManagementMixin`.
# Pre-extraction B3-B.2a ha modernizzato
# `tests2/arricchisci/unit/test_api_connector_test_api_connection_lock.py`
# da `patch("app.core.arricchisci.api_connector.logger")` source-bound a
# handler stdlib agganciato al logger canonico per nome — il mixin usa
# `get_logger("app.core.arricchisci.api_connector")` (singleton stdlib
# per nome) → record indistinguibili lato consumer. Pattern mixin
# ratificato in B2 (carica/writer) + B3-A (arricchisci/enrichment). Vedi
# `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-B.
from app.core.arricchisci.api_connection_management import (
    ApiConnectionManagementMixin,
)


# Phase 5 B3-B.3 cutover: cluster SUPPORT (FIELD_INTROSPECTION 5
# metodi: `_extract_available_fields`, `_extract_geojson_fields`,
# `_is_geometry`, `_get_field_type`, `_get_sample_value` +
# RESULT_BUILDING 4 metodi: `_find_final_result`,
# `_build_file_result_response`, `_save_and_build_dataframe_response`,
# `_write_intermediate_snapshot`) spostato in
# `app.core.arricchisci.api_connection_support.ApiConnectionSupportMixin`.
# Sono leaf utilities (zero `self.<state>`, zero cross-cluster calls
# fra FIELD e RESULT). Logger canonico
# `get_logger("app.core.arricchisci.api_connector")` (singleton stdlib).
# I 4 thin AUTH/UTILITY delegates (`_prepare_headers`,
# `_get_oauth2_client_credentials_token`, `_get_oauth2_token`,
# `_extract_json_value`) restano in core: `_prepare_headers` ha
# source-bound patch via `_shared_prepare_headers` in test_temp_lifecycle_core,
# `_get_oauth2_*` sono dead-code candidate (0 self-call sites in
# api_connector + tests), `_extract_json_value` denso nei consumer
# API_GENERATOR/ENRICHER non ancora estratti — extraction posticipata
# a batch dedicato. Vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-B.
# Aggiornamento B3-B.7: `_get_oauth2_*` rimossi come dead code (prova
# forte verificata). `_extract_json_value` migrato in B3-B.6 nel
# GeneratorEnricherMixin. Resta in core solo `_prepare_headers` (3
# linee) per source-bound patch in test_temp_lifecycle_core.
from app.core.arricchisci.api_connection_support import (
    ApiConnectionSupportMixin,
)


# Phase 5 B3-B.4b cutover: cluster SOURCE_LOADING / TENANCY (6 metodi:
# `_assert_source_project_in_tenant`, `_recompute_upstream_project`,
# `_filesystem_fallback_load_project`, `_load_source_project`,
# `_load_file_source`, `_load_static_values`) + SSRF (1 metodo:
# `_validate_outbound_url`, `@staticmethod`) spostato in
# `app.core.arricchisci.api_connection_source_loading.ApiConnectionSourceLoadingMixin`.
# Pre-extraction B3-B.4a ha modernizzato
# `tests2/security/unit/test_api_connector_source_loading_core.py`
# da `patch("app.core.arricchisci.api_connector.filter_visible_columns", ...)`
# source-bound a `patch("app.utils.dataframe.filter_visible_columns", ...)`
# canonical. Logger canonico
# `get_logger("app.core.arricchisci.api_connector")` (StructuredLogger
# wrapper supporta kwarg `op=` usato in `_assert_source_project_in_tenant`).
# State refs `self.results_dir` / `self._current_group_id` /
# `self._preview_limit` accessibili via MRO. Cross-cluster forward refs:
# `_validate_outbound_url` chiamato da `_execute_rest_file` core (futuro
# B3-B.5/B3-B.6) → MRO trasparente. `_load_*` chiamati da
# `executor.py:170,173,176` instance-level → MRO trasparente. Pattern
# mixin ratificato in B2 + B3-A + B3-B.2b + B3-B.3. Vedi
# `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-B.
from app.core.arricchisci.api_connection_source_loading import (
    ApiConnectionSourceLoadingMixin,
)


# Phase 5 B3-B.5b cutover: cluster REST_FILE PIPELINE (6 metodi:
# `_parse_http_error_body`, `_download_binary_response`,
# `_build_rest_file_multipart`, `_parse_rest_file_config`,
# `_rest_file_error`, `_execute_rest_file`) + FILE_SOURCE RESOLUTION
# (4 metodi: `_assert_file_source_group_access`,
# `_resolve_file_source_id_path`, `_resolve_file_source_path`,
# `_resolve_input_path_for_file_processor`) spostato in
# `app.core.arricchisci.api_connection_rest_file.ApiConnectionRestFileMixin`.
# Pre-extraction B3-B.5a ha modernizzato 3 patch sites
# `patch("app.core.arricchisci.api_connector.requests.post", ...)` →
# `patch("requests.post", ...)` canonical (in
# `tests2/arricchisci/unit/test_api_connector_rest_file_error_taxonomy.py`
# e `tests2/security/unit/test_temp_lifecycle_core.py`).
# Behavior-equivalent rewrite dichiarato: 8 forward-ref classe
# `ApiConnectionService._rest_file_error(...)` → `self._rest_file_error(...)`
# in `_build_rest_file_multipart` (×4) e `_parse_rest_file_config` (×4).
# Necessario per evitare import circolare nel mixin module; runtime-
# identico per staticmethod (descriptor protocol). Vedi docstring del
# mixin per dettagli completi. 3 `@staticmethod` preservati. Logger
# canonico `get_logger("app.core.arricchisci.api_connector")`. Cross-
# cluster forward refs risolti via MRO: `_validate_outbound_url` ←
# `_parse_rest_file_config` (B3-B.4b mixin), `_prepare_headers` ←
# `_execute_rest_file` (core). Pattern mixin ratificato in B2 + B3-A +
# B3-B.2b + B3-B.3 + B3-B.4b. Vedi
# `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-B.
from app.core.arricchisci.api_connection_rest_file import (
    ApiConnectionRestFileMixin,
)


# Phase 5 B3-B.6 cutover: cluster API GENERATOR (5 metodi:
# `_paginate_generator_results`, `_substitute_url_path_params`,
# `_call_generator_endpoint`, `_build_generator_base_params`,
# `_execute_api_generator`) + API ENRICHER (3 metodi:
# `_extract_rows_from_response`, `_enrich_one_row`,
# `_execute_api_enricher`) + JSON PATH UTIL (1 metodo:
# `_extract_json_value`) spostato in
# `app.core.arricchisci.api_connection_generator_enricher.ApiConnectionGeneratorEnricherMixin`.
# B3-B.6 = single commit (0 source-bound patches sui 9 metodi del
# cluster — i 3 patches `requests.post` di REST_FILE erano gia'
# stati canonicalizzati in B3-B.5a verso `patch("requests.post", ...)`
# che intercetta ogni consumer del modulo `requests`).
# `_extract_json_value` migrato nel mixin: 13/13 self-call sites tutti
# dentro il cluster Generator/Enricher (le references in
# `enrichment_*` mixin sono per la classe distinta `EnrichmentService`
# con sua propria copia). Body bit-per-bit. 1 `@staticmethod`
# preservato (`_substitute_url_path_params`). Logger canonico
# `get_logger("app.core.arricchisci.api_connector")` (StructuredLogger
# wrapper). Cross-cluster forward refs via MRO: `_prepare_headers` ←
# `_execute_api_generator` + `_execute_api_enricher` (core),
# `_substitute_url_path_params` ← `test_api_connection` (B3-B.2b
# mixin, cross-mixin INVERSO documentato). Pattern mixin ratificato in
# B2 + B3-A + B3-B.2b + B3-B.3 + B3-B.4b + B3-B.5b. Vedi
# `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-B.
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

    # =========================================================================
    # CRUD CONNESSIONI API + TEST_CONNECTION — extracted in B3-B.2b
    # -------------------------------------------------------------------------
    # I 11 metodi (CRUD: `_mask_credentials`, `list_api_connections`,
    # `list_api_connections_decrypted`, `get_api_connection`,
    # `create_api_connection`, `update_api_connection`,
    # `delete_api_connection` + TEST_CONNECTION: `_build_test_call_params`,
    # `_inject_auth_query_params`, `_parse_test_response_json`,
    # `test_api_connection`) sono stati spostati in
    # `app.core.arricchisci.api_connection_management.ApiConnectionManagementMixin`
    # ed ereditati via MRO da `ApiConnectionService`. Behavior bit-per-bit
    # identico al pre-extraction. Vedi B3-B.2b cutover comment in testa al
    # file (sopra il `logger = get_logger(__name__)`).
    # =========================================================================

    # =========================================================================
    # SUPPORT (FIELD_INTROSPECTION + RESULT_BUILDING) — extracted in B3-B.3
    # -------------------------------------------------------------------------
    # I 9 metodi (FIELD_INTROSPECTION 5: `_extract_available_fields`,
    # `_extract_geojson_fields`, `_is_geometry`, `_get_field_type`,
    # `_get_sample_value`; RESULT_BUILDING 4: `_find_final_result`,
    # `_build_file_result_response`, `_save_and_build_dataframe_response`,
    # `_write_intermediate_snapshot`) sono stati spostati in
    # `app.core.arricchisci.api_connection_support.ApiConnectionSupportMixin`
    # ed ereditati via MRO da `ApiConnectionService`. Behavior bit-per-bit
    # identico al pre-extraction. Vedi B3-B.3 cutover comment in testa al
    # file (sopra il `logger = get_logger(__name__)`).
    # =========================================================================

    # =========================================================================
    # ESECUZIONE ARRICCHIMENTO — extracted in BE-GO-READY-TRANSFORMATION-ENGINE-011
    # -------------------------------------------------------------------------
    # `execute_enrichment` + 5 helper coupled (`_init_execution_context`,
    # `_get_execution_order`, `_run_single_enricher`,
    # `_is_file_source_for_file_processor`) sono stati spostati in
    # `app.core.arricchisci.canvas_orchestrator`. Il metodo qui resta
    # come wrapper KEEP_COMPAT: ~5 consumer esterni dipendono da
    # `api_connection_service.execute_enrichment(...)` (executor.py,
    # router.py, eseguibili/scanner.py + lock test).
    #
    # `_get_execution_order` e' anche re-esposto come metodo di istanza
    # KEEP_COMPAT perche' `executor.py:153` lo chiama via
    # `api_connection_service._get_execution_order(...)`. Rimossione di
    # quel call site e' tracked in residuo R-011-A.
    # =========================================================================

    def execute_enrichment(self, project_id: str, canvas: Dict, format: str = 'csv',
                          force_recalculate: bool = False, preview_limit: int = None,
                          run_id: str = None,
                          intermediate_mode_override: Optional[str] = None) -> Dict:
        """KEEP_COMPAT wrapper: delega a
        `canvas_orchestrator.execute_canvas_enrichment(self, ...)`.

        L'implementazione reale vive in
        `app.core.arricchisci.canvas_orchestrator`. Stesso contratto
        observable byte-per-byte (return shape, error boundary,
        side-effects sul canvas).
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

    def _get_execution_order(self, nodes, edges):
        """KEEP_COMPAT wrapper: delega a
        `canvas_orchestrator.get_execution_order(...)`.

        Mantenuto come metodo di istanza per compat con
        `executor.py:153` (`api_connection_service._get_execution_order(...)`)
        e con i lock test che non lo monkeypatch-ano (deterministic
        Kahn pure function).
        """
        from app.core.arricchisci.canvas_orchestrator import (
            get_execution_order,
        )
        return get_execution_order(nodes, edges)

    def _init_execution_context(self, project_id: str,
                                preview_limit: Optional[int]) -> None:
        """KEEP_COMPAT wrapper: delega a
        `canvas_orchestrator.init_execution_context(self, ...)`.

        Lock test
        (`tests2/arricchisci/unit/test_init_execution_context_fail_closed.py`,
        `tests2/arricchisci/unit/test_enrichment_task_tenant_binding_lock.py`)
        chiamano `api_service._init_execution_context(...)` direttamente.
        Behavior preservato byte-per-byte: `_preview_limit` settato,
        `_current_group_id` azzerato prima del path che puo' fallire,
        `psycopg2.Error` re-raised come `ExecutionContextError`.
        """
        from app.core.arricchisci.canvas_orchestrator import (
            init_execution_context,
        )
        init_execution_context(self, project_id, preview_limit)

    def _prepare_headers(self, auth: Dict, custom_headers: Dict = None) -> Dict:
        """Prepara headers HTTP per chiamata API. Delegato a shared/api_utils."""
        return _shared_prepare_headers(auth, custom_headers)

    # B3-B.7 cleanup: rimossi `_get_oauth2_client_credentials_token` e
    # `_get_oauth2_token` come dead code (prova forte: 0 self-call sites
    # in api_connector, 0 external refs in app/, 0 test patches, 0 direct
    # imports, EnrichmentService class ha le sue proprie copie separate
    # in `enrichment_service.py:424,428`). Erano shim 3-line verso
    # `_shared_get_oauth2_token` / `_shared_get_oauth2_cc_token` di
    # `app.shared.api_utils`, importati ma mai chiamati dal namespace
    # `ApiConnectionService`. Vedi B3-B.3 mini-scan `_get_oauth2_*`
    # dead-code candidate flag.

    # =========================================================================
    # SOURCE_LOADING / TENANCY + SSRF — extracted in B3-B.4b
    # -------------------------------------------------------------------------
    # SOURCE_LOADING / TENANCY (6 metodi: `_assert_source_project_in_tenant`,
    # `_recompute_upstream_project`, `_filesystem_fallback_load_project`,
    # `_load_source_project`, `_load_file_source`, `_load_static_values`)
    # + SSRF (1 metodo: `_validate_outbound_url`, `@staticmethod`) sono
    # stati spostati in
    # `app.core.arricchisci.api_connection_source_loading.ApiConnectionSourceLoadingMixin`
    # ed ereditati via MRO da `ApiConnectionService`. Behavior bit-per-bit
    # identico al pre-extraction. Vedi B3-B.4b cutover comment in testa al
    # file (sopra il `logger = get_logger(__name__)`).
    # =========================================================================

    # =========================================================================
    # REST_FILE PIPELINE + FILE_SOURCE RESOLUTION — extracted in B3-B.5b
    # -------------------------------------------------------------------------
    # REST_FILE PIPELINE (6 metodi: `_parse_http_error_body`,
    # `_download_binary_response`, `_build_rest_file_multipart`,
    # `_parse_rest_file_config`, `_rest_file_error`, `_execute_rest_file`)
    # + FILE_SOURCE RESOLUTION (4 metodi: `_assert_file_source_group_access`,
    # `_resolve_file_source_id_path`, `_resolve_file_source_path`,
    # `_resolve_input_path_for_file_processor`) sono stati spostati in
    # `app.core.arricchisci.api_connection_rest_file.ApiConnectionRestFileMixin`
    # ed ereditati via MRO da `ApiConnectionService`. Behavior bit-per-bit
    # identico al pre-extraction ECCETTO 8 forward-ref class-explicit
    # `ApiConnectionService._rest_file_error(...)` riscritti come
    # `self._rest_file_error(...)` in `_build_rest_file_multipart` (×4) e
    # `_parse_rest_file_config` (×4) — runtime-identici per staticmethod
    # via descriptor protocol, dichiarati esplicitamente nel docstring del
    # mixin. Vedi B3-B.5b cutover comment in testa al file (sopra il
    # `logger = get_logger(__name__)`). 3 `@staticmethod` preservati.
    # =========================================================================

    # =========================================================================
    # API GENERATOR + API ENRICHER + JSON PATH UTIL — extracted in B3-B.6
    # -------------------------------------------------------------------------
    # API GENERATOR (5 metodi: `_paginate_generator_results`,
    # `_substitute_url_path_params`, `_call_generator_endpoint`,
    # `_build_generator_base_params`, `_execute_api_generator`) +
    # API ENRICHER (3 metodi: `_extract_rows_from_response`,
    # `_enrich_one_row`, `_execute_api_enricher`) + JSON PATH UTIL
    # (1 metodo: `_extract_json_value`) sono stati spostati in
    # `app.core.arricchisci.api_connection_generator_enricher.ApiConnectionGeneratorEnricherMixin`
    # ed ereditati via MRO da `ApiConnectionService`. Behavior bit-per-bit
    # identico al pre-extraction. `_extract_json_value` migrato qui (era
    # in core ma 13/13 self-call sites tutti dentro il cluster Generator/
    # Enricher). 1 `@staticmethod` preservato
    # (`_substitute_url_path_params`). Vedi B3-B.6 cutover comment in
    # testa al file (sopra il `logger = get_logger(__name__)`).
    # =========================================================================


# --- Singleton globale ---
# Usato da api/enrichment.py e main.py per accedere al servizio
api_connection_service = ApiConnectionService()
