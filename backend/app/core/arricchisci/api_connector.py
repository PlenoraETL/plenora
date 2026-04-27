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

import io
import os
import re
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any
import requests
import pandas as pd
import psycopg2

from app.config import settings
from app.utils.logger import get_logger
from app.shared.api_utils import (
    extract_json_value as _shared_extract_json_value,
    prepare_headers as _shared_prepare_headers,
    get_oauth2_token as _shared_get_oauth2_token,
    get_oauth2_client_credentials_token as _shared_get_oauth2_cc_token,
    sanitize_for_log,
)

# Import nuove utility centralizzate
from app.utils.dataframe import (
    load_dataframe_from_file,
    save_dataframe,
    filter_visible_columns,
    find_latest_output_file
)
from app.utils.result_store import get_temp_output_path, save_result_blob
from app.utils.template_variables import process_static_fields
from app.utils.response_parser import ResponseParser, get_preset_config
from app.shared.exceptions import ExecutionContextError, FileSourceResolutionError
import app.db.db_service as db_svc

# Phase 5 B3-B.1 cutover: cluster MODULE-LEVEL HELPERS
# (`_AUTH_SENSITIVE_FIELDS` constant + `_encrypt_auth_in_config` +
# `_decrypt_auth_in_config` + `_nd` node-data accessor) spostato in
# `app.core.arricchisci._api_connection_helpers`. Re-import in testa
# al file per preservare i path symbolici esistenti — il direct import
# di test contract:
#   - tests2/arricchisci/unit/test_api_connection_decrypt_policy.py
#     usa `from api_connector import _decrypt_auth_in_config`
#   - tests2/arricchisci/unit/test_api_connector_contract.py
#     usa `from api_connector import _nd`
# continua a funzionare via questo re-import (back-compat). Vedi
# `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-B.
from app.core.arricchisci._api_connection_helpers import (  # noqa: F401  (re-export)
    _AUTH_SENSITIVE_FIELDS,
    _encrypt_auth_in_config,
    _decrypt_auth_in_config,
    _nd,
)


# Phase 5 B3-B.1 cutover: cluster MODULE-LEVEL HELPERS
# (`_AUTH_SENSITIVE_FIELDS` + `_encrypt_auth_in_config` +
# `_decrypt_auth_in_config` + `_nd`) spostato in
# `app.core.arricchisci._api_connection_helpers`. I 4 simboli sono
# re-imported in testa al file (vedi block sopra) per preservare i
# path symbolici esistenti (test direct imports + back-compat).


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
    # ESECUZIONE ARRICCHIMENTO
    # Pipeline: carica sorgenti → esegui nodi in ordine topologico →
    # arricchisci via API → salva output finale.
    # Supporta nodi: project_source, file_source, staticValues, api_enrich, result
    # =========================================================================

    def _is_file_source_for_file_processor(
        self,
        node_id: str,
        edges: List[Dict],
        node_map: Dict[str, Dict],
        api_connections: Dict[str, Dict],
    ) -> bool:
        """True se il file_source e' input diretto di un nodo api_enrich
        di tipo file-processor (`rest_file`).

        Quando e' True il caller salta `_load_file_source` e tiene il
        file_source come marker passthrough: i file-processor leggono
        il file direttamente dal file_source_id, non dal DataFrame.
        """
        for de in edges:
            if de.get('source') != node_id:
                continue
            target_node = node_map.get(de.get('target'))
            if not target_node or target_node.get('type') != 'api_enrich':
                continue
            target_api_id = _nd(target_node, 'api_id')
            target_api_config = api_connections.get(target_api_id, {})
            if target_api_config.get('connection_type') == 'rest_file':
                return True
        return False

    def _run_single_enricher(self, node_data: Dict, api_conns: Dict, in_df) -> Any:
        """Esegue un singolo nodo enricher.

        Dispatch su `api_type`: generator (1 call produce df intero)
        vs enricher (1 call per riga). Usato sia direttamente che
        come callback da `execute_parallel_enrichers`. Solleva
        `ValueError` se l'api_id non e' nelle connections decifrate.
        """
        api_id = _nd(node_data, 'api_id')
        api_config = api_conns.get(api_id)
        if not api_config:
            raise ValueError(f"API '{api_id}' non trovata")
        if api_config.get('api_type', 'enricher') == 'generator':
            return self._execute_api_generator(api_config, in_df)
        param_mapping = _nd(node_data, 'param_mapping', {})
        return self._execute_api_enricher(in_df, api_config, param_mapping)

    def _init_execution_context(self, project_id: str, preview_limit: Optional[int]) -> None:
        """Inizializza il contesto di esecuzione per `execute_enrichment`.

        - `self._preview_limit`: usato da `_load_file_source` per
          troncare i DataFrame in modalita' preview.
        - `self._current_group_id`: tenant binding del progetto
          corrente, usato dai check cross-tenant in `_load_source_project`
          / `_load_file_source` / `_resolve_file_source_path` /
          `_assert_file_source_group_access`.

        Fail-closed su errori DB: se `db_svc.get_project` solleva
        `psycopg2.Error` (pool exhausted, DB down, deadlock, query
        error) questo metodo solleva `ExecutionContextError` con
        chain preservata via `from e`. Il boundary di
        `execute_enrichment` (Fix-C) la cattura nel ramo
        `except Exception` e la traduce in response safe per il
        caller.

        Comportamento su progetto inesistente (`_proj_row is None`):
        invariato -- `_current_group_id = None`, i check downstream
        sollevano "Contesto di sicurezza non determinabile". E'
        ramo dati legittimo, non errore.
        """
        self._preview_limit = preview_limit
        # Azzera prima del path che puo' fallire: evita stato stale
        # sul singleton se una chiamata precedente aveva settato
        # group_id e la nuova solleva ExecutionContextError.
        self._current_group_id = None
        try:
            _proj_row = db_svc.get_project(project_id)
        except psycopg2.Error as e:
            raise ExecutionContextError(
                project_id,
                reason="errore lettura progetto dal DB",
                original_error=e,
            ) from e
        self._current_group_id = _proj_row.get("group_id") if _proj_row else None

    def execute_enrichment(self, project_id: str, canvas: Dict, format: str = 'csv',
                          force_recalculate: bool = False, preview_limit: int = None,
                          run_id: str = None) -> Dict:
        """
        Esegue il progetto arricchisci dal canvas.
        
        Flusso:
        1. Carica tutte le API connections configurate
        2. Calcola ordine topologico dei nodi (BFS da sorgenti a risultato)
        3. Per ogni nodo: carica sorgente / esegui API / propaga risultato
        4. Salva DataFrame finale nel formato richiesto
        
        Tipi nodo gestiti:
        - project_source: carica output di un progetto ESTRAI/MANIPOLA
        - file_source: carica file sorgente (CSV, GeoJSON, etc.)
        - staticValues: genera DataFrame 1-riga con valori statici/template
        - api_enrich: arricchisce (enricher) o genera (generator) dati via API
        - result: nodo terminale, definisce formato e nome output
        """
        try:
            self._init_execution_context(project_id, preview_limit)

            nodes = canvas.get('nodes', [])
            edges = canvas.get('edges', [])
            
            if not nodes:
                return {"success": False, "error": "Nessun nodo nel canvas"}
            
            # Carica tutte le API connections con credenziali decifrate (1 query)
            api_connections = self.list_api_connections_decrypted()
            
            # Ordine di esecuzione topologico
            execution_order = self._get_execution_order(nodes, edges)
            logger.info(f"Ordine esecuzione: {execution_order}")

            # ── Raggruppamento nodi per esecuzione parallela ──
            from app.core.arricchisci.parallel import (
                compute_parallel_groups, execute_parallel_enrichers, should_execute_node,
            )
            groups = compute_parallel_groups(
                execution_order, nodes, edges,
                api_connections=api_connections,
            )
            logger.info(f"Gruppi di esecuzione: {[(t, len(ids)) for t, ids in groups]}")

            node_map = {n['id']: n for n in nodes}
            node_results = {}
            processed = set()

            for step_type, step_node_ids in groups:
                if step_type == "single":
                    # ── Nodo singolo (sorgente, result, o enricher isolato) ──
                    node_id = step_node_ids[0]
                    node = node_map.get(node_id)
                    if not node:
                        continue
                    node_type = node.get('type')
                    node_data = node
                    logger.debug(f"Esecuzione nodo: {node_id} ({node_type})")

                    if node_type == 'project_source':
                        node_results[node_id] = self._load_source_project(node_data, force_recalculate)

                    elif node_type == 'file_source':
                        if self._is_file_source_for_file_processor(
                            node_id, edges, node_map, api_connections
                        ):
                            node_results[node_id] = {
                                "_passthrough_file_source": True,
                                "file_source_id": _nd(node, 'file_source_id'),
                            }
                        else:
                            node_results[node_id] = self._load_file_source(node_data)

                    elif node_type == 'staticValues':
                        node_results[node_id] = self._load_static_values(node_data)

                    elif node_type == 'result':
                        input_edge = next((e for e in edges if e['target'] == node_id), None)
                        if input_edge:
                            input_df = node_results.get(input_edge['source'])
                            if input_df is not None:
                                node_results[node_id] = input_df

                else:
                    # ── Gruppo parallelo di api_enrich ──
                    # Trova l'input_df comune: output del predecessore del primo nodo del gruppo
                    first_node_id = step_node_ids[0]
                    input_edge = next((e for e in edges if e['target'] == first_node_id), None)
                    input_df = node_results.get(input_edge['source']) if input_edge else None

                    if input_df is None:
                        # Fallback: prova a trovare l'ultimo risultato disponibile
                        for prev_id in reversed(execution_order):
                            if prev_id in node_results and isinstance(node_results[prev_id], pd.DataFrame):
                                input_df = node_results[prev_id]
                                break

                    if input_df is None:
                        return {"success": False, "error": f"Input non trovato per gruppo parallelo {step_node_ids}"}

                    # Filtra nodi file-processor (rest_file) dal gruppo:
                    # non sono parallelizzabili col meccanismo degli enricher.
                    parallel_ids = []
                    for nid in step_node_ids:
                        nd = node_map.get(nid)
                        api_id = _nd(nd, 'api_id')
                        api_cfg = api_connections.get(api_id, {})
                        ctype = api_cfg.get('connection_type')
                        if ctype == 'rest_file':
                            # Esegui in sequenza prima del gruppo parallelo
                            if should_execute_node(nd, input_df):
                                result = self._execute_rest_file(
                                    nd, api_cfg, nodes, edges, nid, project_id,
                                    node_results=node_results,
                                )
                                if isinstance(result, dict) and not result.get('success', True):
                                    return result
                                node_results[nid] = result
                            else:
                                node_results[nid] = input_df
                        else:
                            parallel_ids.append(nid)

                    if parallel_ids:
                        logger.info(f"Esecuzione parallela: {len(parallel_ids)} nodi enricher")
                        group_results, group_error = execute_parallel_enrichers(
                            node_ids=parallel_ids,
                            input_df=input_df,
                            node_map=node_map,
                            api_connections=api_connections,
                            execute_single_fn=self._run_single_enricher,
                        )
                        node_results.update(group_results)

                        # Se il gruppo ha prodotto un errore, la pipeline
                        # fallisce: inghiottire il group_error portava a
                        # task "success" con output passthrough del nodo
                        # upstream (es. solo la colonna seed di staticValues),
                        # mascherando errori upstream come HTTP 422 sul
                        # generator API. Non esiste un caso legittimo in cui
                        # un'eccezione dentro run_one debba essere ignorata:
                        # le skip via execute_if ritornano (None, None),
                        # non (None, errore).
                        if group_error:
                            logger.error(
                                f"[GESTITO] Errore api_enrich in gruppo "
                                f"{parallel_ids}: {group_error}",
                                op="execute", project_id=project_id,
                            )
                            return {
                                "success": False,
                                "user_error": True,
                                "error": f"Errore chiamata API upstream: {group_error}",
                                "error_node_id": parallel_ids[0] if parallel_ids else None,
                                "error_node_type": "api_enrich",
                            }

                    if run_id:
                        for nid in step_node_ids:
                            self._write_intermediate_snapshot(
                                run_id, nid, "api_enrich", node_results.get(nid)
                            )
            
            result_node = next((n for n in nodes if n.get('type') == 'result'), None)
            output_format = _nd(result_node, 'format', format) or format if result_node else format
            output_name = _nd(result_node, 'fileName', _nd(result_node, 'name', 'output')) if result_node else 'output'

            final_result = self._find_final_result(
                node_results, result_node, execution_order
            )
            if final_result is None:
                return {"success": False, "error": "Nessun risultato generato"}

            if isinstance(final_result, dict) and final_result.get('is_file_result'):
                return self._build_file_result_response(final_result, project_id)

            if not isinstance(final_result, pd.DataFrame):
                return {"success": False, "error": "Nessun DataFrame generato"}

            return self._save_and_build_dataframe_response(
                final_result, output_name, output_format, project_id
            )

        except ValueError as e:
            # boundary: i ValueError sollevati dentro api_connector portano
            # messaggi gia' user-safe (es. "Accesso negato:", "URL bloccato:",
            # "Contesto di sicurezza ..."). Li propaghiamo come user_error.
            logger.error(f"[GESTITO] Errore esecuzione enrichment {project_id}: {e}", exc_info=True)
            return {"success": False, "user_error": True, "error": str(e)}
        except Exception as e:
            # boundary: messaggio safe al caller, dettaglio tecnico solo nei log
            logger.error(f"[GESTITO] Errore esecuzione enrichment {project_id}: {e}", exc_info=True)
            return {"success": False, "user_error": False, "error": "Errore interno durante l'arricchimento"}

    def _get_execution_order(self, nodes: List[Dict], edges: List[Dict]) -> List[str]:
        """
        Calcola ordine topologico per esecuzione (algoritmo di Kahn).
        
        Partendo dai nodi sorgente (in_degree=0), visita il grafo in ampiezza
        e restituisce l'ordine in cui i nodi devono essere eseguiti per
        rispettare le dipendenze (ogni nodo viene eseguito dopo i suoi input).
        """
        from collections import deque
        
        graph = {n['id']: [] for n in nodes}
        in_degree = {n['id']: 0 for n in nodes}
        
        for edge in edges:
            source = edge.get('source')
            target = edge.get('target')
            if source in graph and target in in_degree:
                graph[source].append(target)
                in_degree[target] += 1
        
        queue = deque([n for n, d in in_degree.items() if d == 0])
        order = []
        
        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        return order
    
    def _prepare_headers(self, auth: Dict, custom_headers: Dict = None) -> Dict:
        """Prepara headers HTTP per chiamata API. Delegato a shared/api_utils."""
        return _shared_prepare_headers(auth, custom_headers)

    def _get_oauth2_client_credentials_token(self, auth: Dict) -> Optional[str]:
        """Ottiene token OAuth2 con client_credentials flow. Delegato a shared/api_utils."""
        return _shared_get_oauth2_cc_token(auth)
    
    def _get_oauth2_token(self, auth: Dict) -> Optional[str]:
        """Ottiene token OAuth2 con password flow. Delegato a shared/api_utils."""
        return _shared_get_oauth2_token(auth)
    
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