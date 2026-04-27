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


logger = get_logger(__name__)


class ApiConnectionService(
    ApiConnectionManagementMixin,
    ApiConnectionSupportMixin,
    ApiConnectionSourceLoadingMixin,
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
    # EXECUTABLE: esecuzione processori locali (script Python, exe, shell, node)
    # =========================================================================
    # SSRF (`_validate_outbound_url`) extracted in B3-B.4b — vedi cutover
    # SOURCE_LOADING sopra. `_execute_rest_file` chiama
    # `self._validate_outbound_url` che risolve via MRO al mixin.

    @staticmethod
    def _parse_http_error_body(response) -> str:
        """Estrae il messaggio d'errore dal body di una risposta HTTP.

        Prova prima JSON (preferendo `detail` → `error` → `message` →
        l'intero dict se nessuno di questi e' presente). Se il body non
        e' JSON, fallback a `text[:500]`.
        """
        try:
            err_body = response.json()
            if isinstance(err_body, dict):
                return (
                    err_body.get('detail')
                    or err_body.get('error')
                    or err_body.get('message')
                    or err_body
                )
            return err_body
        except Exception:
            return response.text[:500] if response.text else ''

    @staticmethod
    def _download_binary_response(
        response, output_path: Path, start_time: float, timeout: int
    ) -> tuple:
        """Streaming download del body su file con watchdog wall-clock.

        Il parametro `timeout` di `requests.post` copre solo connect/
        first-byte, NON il download del body. Per un server che inizia
        a rispondere ma poi rallenta drammaticamente, il task rischia
        di durare molto oltre il timeout apparente. Il watchdog
        interrompe il download se il totale supera `timeout` secondi
        dall'avvio della chiamata (`start_time`).

        Ritorna `(total_bytes, download_timed_out)`. Chiude sempre
        la response in un `finally` interno (`response.close` best-effort).
        """
        import time as _time

        total_bytes = 0
        body_deadline = start_time + timeout
        download_timed_out = False
        try:
            with open(output_path, 'wb') as out_fh:
                for chunk in response.iter_content(chunk_size=65536):
                    if _time.time() > body_deadline:
                        download_timed_out = True
                        break
                    if chunk:
                        out_fh.write(chunk)
                        total_bytes += len(chunk)
        finally:
            try:
                response.close()
            except Exception as _close_err:
                logger.debug(
                    f"_download_binary_response: response.close() best-effort "
                    f"fallito: {_close_err}"
                )

        return total_bytes, download_timed_out

    def _build_rest_file_multipart(
        self,
        *,
        file_fields: List[Dict],
        static_fields: List[Dict],
        input_path: Optional[str],
        extra_files_cfg: Dict,
        opened_handles: List,
    ) -> tuple:
        """Costruisce i dict `files` e `data` per `requests.post(multipart)`.

        Ritorna `(files, data, error_or_None)`.

        Regole:
        - `file_fields` con `source='node_input'` leggono `input_path`
          (risolto dall'edge in ingresso al nodo); se required e assente
          → error, altrimenti skip silenzioso.
        - `file_fields` con `source='extra_input'` leggono da
          `extra_files_cfg[name]` (dict con `file_source_id` o id nudo)
          via `_resolve_file_source_id_path`. Stessa logica required/skip.
        - `static_fields` con `content_type` sono inviati come file
          in-memory (per JSON inline tipo styles/mapping); altrimenti
          come form-data semplice.

        `opened_handles` e' appended in-place: il caller e' responsabile
        del close nel finally, indipendentemente da success/error.
        """
        files: Dict = {}
        data: Dict = {}

        for ff in file_fields:
            name = ff.get('name')
            source = ff.get('source', 'node_input')
            required = bool(ff.get('required', False))
            if not name:
                continue

            if source == 'node_input':
                if not input_path:
                    if required:
                        return files, data, ApiConnectionService._rest_file_error(
                            "internal",
                            f"Campo file '{name}' richiesto ma nessun file di input collegato al nodo",
                        )
                    continue
                # FS-5: input_path puo' essere str (path su disco) OPPURE
                # tuple(bytes, filename) dal materialize in-memory.
                if isinstance(input_path, tuple):
                    data_bytes, filename = input_path
                    fh = io.BytesIO(data_bytes)
                    # Nessun opened_handle: BytesIO viene GC'd al return,
                    # non serve close esplicito (diversamente da file handle).
                    files[name] = (filename, fh, 'application/octet-stream')
                else:
                    ip = Path(input_path)
                    if not ip.exists():
                        if required:
                            return files, data, ApiConnectionService._rest_file_error(
                                "file_source_not_found",
                                f"File di input {ip} non trovato",
                            )
                        continue
                    fh = open(ip, 'rb')
                    opened_handles.append(fh)
                    files[name] = (ip.name, fh, 'application/octet-stream')

            elif source == 'extra_input':
                entry = extra_files_cfg.get(name)
                fs_id = entry.get('file_source_id') if isinstance(entry, dict) else entry
                if not fs_id:
                    if required:
                        return files, data, ApiConnectionService._rest_file_error(
                            "internal",
                            f"Campo file extra '{name}' richiesto ma non configurato sul nodo",
                        )
                    continue
                extra_path = self._resolve_file_source_id_path(fs_id)
                if not extra_path:
                    if required:
                        return files, data, ApiConnectionService._rest_file_error(
                            "file_source_not_found",
                            f"Campo file extra '{name}': file_source '{fs_id}' non trovato "
                            f"(verifica che il file esista ancora nel catalogo e sia nel tuo gruppo)",
                        )
                    logger.warning(
                        f"rest_file extra_input '{name}': file_source {fs_id} "
                        f"non risolvibile, saltato"
                    )
                    continue
                ep = Path(extra_path)
                fh = open(ep, 'rb')
                opened_handles.append(fh)
                files[name] = (ep.name, fh, 'application/octet-stream')

            else:
                logger.warning(
                    f"rest_file: source '{source}' non gestita per campo '{name}', saltato"
                )

        for sf in static_fields:
            name = sf.get('name')
            value = sf.get('value', '')
            ct = sf.get('content_type') or None
            if not name:
                continue
            if ct:
                payload = (
                    value
                    if isinstance(value, (bytes, bytearray))
                    else str(value).encode('utf-8')
                )
                files[name] = (name, payload, ct)
            else:
                data[name] = '' if value is None else str(value)

        return files, data, None

    def _parse_rest_file_config(
        self, api_config: Dict
    ) -> tuple:
        """Valida e normalizza la config di un rest_file.

        Ritorna `(parsed_dict, None)` se OK, `(None, error_dict)` se
        invalida. `error_dict` ha il formato `{success: False, error: ...}`
        atteso dai caller di `_execute_rest_file`.

        Controlli:
        - `endpoint.url` obbligatorio
        - `endpoint.method` deve essere POST (unico supportato per rest_file)
        - `_validate_outbound_url(url)` per SSRF guard
        - `response.format` deve essere `binary_file` (unico supportato)
        """
        endpoint = api_config.get('endpoint', {}) or {}
        url = endpoint.get('url')
        method = (endpoint.get('method') or 'POST').upper()
        if not url:
            return None, ApiConnectionService._rest_file_error(
                "internal", "Endpoint URL mancante nella configurazione",
            )
        if method != 'POST':
            return None, ApiConnectionService._rest_file_error(
                "internal",
                f"Metodo {method} non supportato per rest_file (usare POST)",
            )

        ssrf_error = self._validate_outbound_url(url)
        if ssrf_error:
            logger.warning(f"rest_file SSRF block: {url} → {ssrf_error}")
            return None, ApiConnectionService._rest_file_error(
                "remote_processor_error",
                f"URL non consentito: {ssrf_error}",
            )

        response_cfg = api_config.get('response', {}) or {}
        response_format = response_cfg.get('format', 'binary_file')
        if response_format != 'binary_file':
            return None, ApiConnectionService._rest_file_error(
                "internal",
                f"response.format '{response_format}' non supportato (usare 'binary_file')",
            )
        output_ext = response_cfg.get('output_extension') or '.bin'
        if not output_ext.startswith('.'):
            output_ext = '.' + output_ext

        request_cfg = api_config.get('request', {}) or {}
        # IMPORTANTE: NON fare fallback su `options.timeout` qui. Quel
        # campo è il timeout "richieste API generiche" (default 30s nel
        # form frontend) e per rest_file è fuorviante: un'operazione
        # file-to-file può durare minuti (es. imdf.plenora.it/api/process
        # che genera IMDF da un DXF). Il canonico è `request.timeout`;
        # se assente, default 600s (10 min).
        timeout = int(request_cfg.get('timeout') or 600)

        return {
            'url': url,
            'method': method,
            'file_fields': request_cfg.get('file_fields') or [],
            'static_fields': request_cfg.get('static_fields') or [],
            'timeout': timeout,
            'output_ext': output_ext,
        }, None

    @staticmethod
    def _rest_file_error(error_kind: str, message: str, *, user_error: bool = True) -> Dict:
        """Return dict standard per errori _execute_rest_file con taxonomy
        `error_kind` (F5 primo batch, 2026-04-24).

        error_kind validi:
          - 'file_source_not_found'   — file_source_id referenziato non esiste in DB
          - 'file_source_forbidden'   — cross-tenant access denied
          - 'remote_processor_error'  — HTTP 4xx/5xx, timeout, connessione upstream
          - 'internal'                — bug o catch-all inatteso (user_error=False)
        """
        return {
            "success": False,
            "user_error": user_error,
            "error_kind": error_kind,
            "error": message,
        }

    def _execute_rest_file(self, node_data: Dict, api_config: Dict,
                           nodes: List[Dict], edges: List[Dict],
                           node_id: str, project_id: str,
                           node_results: Optional[Dict] = None) -> Dict:
        """
        Esegue un connector REST di tipo "Processore File" (connection_type='rest_file').

        Pattern: invia un file (o piu') in multipart/form-data a un servizio HTTP
        remoto e riceve un file binario come risposta. A differenza di
        enricher/generator che lavorano su DataFrame riga per riga, questo pattern
        lavora file->file via HTTP.

        Config attesa in ``api_config``:
            endpoint: { url, method }                        # POST
            auth: {...}                                      # usa _prepare_headers
            request:
              body_type: "multipart"                         # fisso per ora
              file_fields: [ { name, source, required } ]    # source='node_input'
              static_fields: [ { name, value, content_type } ]
              timeout: 600                                   # override opzionale
            response:
              format: "binary_file"                          # fisso per ora
              output_extension: ".zip"                       # estensione file output

        Il campo file con ``source='node_input'`` riceve il file dal file_source
        collegato in input al nodo del canvas (``_resolve_file_source_path``).

        I campi ``static_fields`` con ``content_type`` sono inviati come file
        in-memory (utile per JSON inline tipo ``styles`` e ``mapping``). Senza
        content_type sono inviati come form-data semplice.

        Ritorna un dict con ``is_file_result=True``, ``output_path``,
        ``metadata`` cosi' il caller del canvas arricchisci lo tratta come
        un generico risultato file.
        """
        import time
        import tempfile
        import requests

        start_time = time.time()

        parsed, err = self._parse_rest_file_config(api_config)
        if err is not None:
            return err
        url = parsed['url']
        method = parsed['method']
        file_fields = parsed['file_fields']
        static_fields = parsed['static_fields']
        timeout = parsed['timeout']
        output_ext = parsed['output_ext']

        # Fix-D narrow #20: clausola ristretta alle classi domain-safe
        # sollevate da `_prepare_headers`:
        #  - KeyError: campo auth mancante (es. api_key, client_secret)
        #  - ValueError: formato auth invalido / auth_type non riconosciuto
        #  - requests.RequestException: fallimento del fetch OAuth2 token
        # Eccezioni fuori tupla (bug interno) fluiscono al safety-net
        # outer di _execute_rest_file (catch #22c, ancora broad in
        # questo commit).
        try:
            headers = self._prepare_headers(api_config.get('auth', {}) or {}, {})
        except (KeyError, ValueError, requests.RequestException) as e:
            return self._rest_file_error(
                "internal",
                f"Preparazione auth fallita: {e}",
            )

        # Formato di materializzazione per input da upstream DataFrame.
        # Default xlsx; override via node_data.materialize_format o
        # api_config.request.materialize_format.
        _req_cfg = api_config.get('request', {}) or {}
        materialize_format = (
            node_data.get('materialize_format')
            or _req_cfg.get('materialize_format')
            or 'xlsx'
        )
        # Fix-D narrow #21: clausola ristretta alle classi domain-safe
        # prodotte dalla catena di risoluzione input:
        #  - ValueError: messaggi domain gia' safe ("Accesso negato:",
        #    "Contesto di sicurezza...", "Errore progetto sorgente...")
        #  - SourceLoadingError: errore dal SourceLoader cross-tenant
        #    (tecnicamente gia' wrappato in ValueError da
        #    _resolve_file_source_path, incluso come belt-and-suspenders)
        #  - FileSourceResolutionError: da Fix-A (lookup DB o scrittura
        #    cache fallita), messaggio safe-by-construction
        # Eccezioni fuori tupla fluiscono al safety-net outer (#22c).
        from app.core.artifacts import SourceLoadingError
        try:
            input_path, _owns_materialized_input = self._resolve_input_path_for_file_processor(
                nodes, edges, node_id,
                node_results=node_results,
                materialize_format=materialize_format,
            )
        except (ValueError, SourceLoadingError, FileSourceResolutionError) as e:
            msg = str(e)
            if "Accesso negato" in msg or "non appartiene al tuo gruppo" in msg:
                return self._rest_file_error("file_source_forbidden", msg)
            return self._rest_file_error(
                "internal",
                f"Risoluzione input fallita: {msg}",
            )

        # `extra_files` e' il dict {field_name: file_source_id} salvato sul
        # nodo api_enrich quando l'utente carica inline file aggiuntivi
        # (es. Excel di arricchimento per il servizio IMDF). Separato dal
        # file principale che arriva via edge.
        extra_files_cfg = node_data.get('extra_files') or {}
        opened_handles: List = []

        # Pre-dichiarazione per il finally: se l'eccezione capita prima
        # del mkdtemp queste variabili devono comunque esistere.
        tmp_exec_dir: Optional[Path] = None
        _cleanup_tmp = True

        try:
            files, data, build_err = self._build_rest_file_multipart(
                file_fields=file_fields,
                static_fields=static_fields,
                input_path=input_path,
                extra_files_cfg=extra_files_cfg,
                opened_handles=opened_handles,
            )
            if build_err is not None:
                return build_err

            # ── 5. Directory temp output ──────────────────────────────────
            # tmp_exec_dir viene cleanup dal `finally` se _cleanup_tmp
            # resta True. Sul ramo success lo settiamo a False perche'
            # restituiamo un output_path al suo interno e il chiamante
            # e' responsabile del cleanup downstream.
            tmp_exec_dir = Path(tempfile.mkdtemp(prefix="plenora_exec_"))
            _cleanup_tmp = True
            output_filename = f"output{output_ext}"
            output_path = tmp_exec_dir / output_filename

            logger.info(
                f"REST File Processor: POST {url} "
                f"files={list(files.keys())} data={list(data.keys())} timeout={timeout}s"
            )

            # ── 6. Chiamata HTTP ──────────────────────────────────────────
            response = requests.post(
                url,
                headers=headers,
                files=files,
                data=data,
                timeout=timeout,
                stream=True,
            )

            if response.status_code >= 400:
                return self._rest_file_error(
                    "remote_processor_error",
                    f"HTTP {response.status_code}: {self._parse_http_error_body(response)}",
                )

            total_bytes, download_timed_out = self._download_binary_response(
                response, output_path, start_time, timeout
            )

            if download_timed_out:
                return self._rest_file_error(
                    "remote_processor_error",
                    f"Timeout download response dopo {timeout}s "
                    f"(ricevuti {total_bytes // 1024} KB parziali) da {url}",
                )

            if total_bytes == 0:
                return self._rest_file_error(
                    "remote_processor_error",
                    "Risposta vuota dal servizio remoto",
                )

            elapsed_ms = (time.time() - start_time) * 1000
            logger.info(f"REST File Processor: output {output_path} ({total_bytes // 1024} KB, {elapsed_ms:.0f}ms)")

            _cleanup_tmp = False  # ramo success: caller gestisce tmp_exec_dir
            return {
                "is_file_result": True,
                "success": True,
                "output_path": str(output_path),
                "execution_time_ms": round(elapsed_ms, 1),
                "metadata": {
                    "method": method,
                    "url": url,
                    "status_code": response.status_code,
                    "content_length": total_bytes,
                },
            }

        except requests.Timeout:
            return self._rest_file_error(
                "remote_processor_error",
                f"Timeout dopo {timeout}s chiamando {url}",
            )
        except requests.RequestException as e:
            return self._rest_file_error(
                "remote_processor_error",
                f"Errore HTTP: {e}",
            )
        except ValueError as e:
            # F5 primo batch (2026-04-24): un ValueError con prefisso
            # "Accesso negato" arriva da `_assert_file_source_group_access`
            # (chiamato da `_resolve_file_source_id_path` in
            # `_build_rest_file_multipart` per extra_input). Senza questo
            # catch specifico cadrebbe nel safety-net sotto e risulterebbe
            # "internal" opaco — ma e' un denial cross-tenant semantico.
            msg = str(e)
            if "Accesso negato" in msg or "non appartiene al tuo gruppo" in msg:
                return self._rest_file_error("file_source_forbidden", msg)
            # Altri ValueError imprevisti fluiscono al safety-net
            logger.error(f"[CRASH] rest_file ValueError inatteso: {e}", exc_info=True)
            return self._rest_file_error(
                "internal",
                "Errore interno nel processore file remoto",
                user_error=False,
            )
        except Exception as e:
            # Fix-D safety-net #22c: copre eccezioni inattese dentro il
            # main try (build multipart, mkdtemp, download response,
            # HTTP path oltre Timeout/RequestException). Messaggio safe
            # generico per il FE; il dettaglio tecnico resta nel log
            # (logger.error + exc_info=True).
            # NOTA: i catch narrow #20 (auth prep) e #21 (input resolve)
            # sono FUORI da questo try. Eccezioni inattese in quei due
            # step propagano fuori dalla funzione e vengono raccolte
            # dal boundary top-level di `execute_enrichment` (Fix-C,
            # commit a10774f) con messaggio "Errore interno durante
            # l'arricchimento". Un'eventuale estensione della safety-net
            # locale a #20/#21 e' debito separato (vedi checkpoint).
            logger.error(f"[CRASH] rest_file: {e}", exc_info=True)
            return self._rest_file_error(
                "internal",
                "Errore interno nel processore file remoto",
                user_error=False,
            )
        finally:
            for fh in opened_handles:
                try:
                    fh.close()
                except Exception as _fh_err:
                    logger.debug(
                        f"rest_file: fh.close() best-effort fallito: {_fh_err}"
                    )
            # Cleanup tmp_exec_dir su tutti i rami errore: senza questo
            # ogni exception lascerebbe una dir `plenora_exec_*` su disco,
            # con leak progressivo nei worker Celery long-running.
            try:
                if _cleanup_tmp and tmp_exec_dir is not None and tmp_exec_dir.exists():
                    import shutil as _shutil_cleanup
                    _shutil_cleanup.rmtree(tmp_exec_dir, ignore_errors=True)
            except Exception as _rm_err:
                logger.debug(
                    f"rest_file: cleanup tmp_exec_dir best-effort fallito: {_rm_err}"
                )
            # Cleanup del file temporaneo creato per materializzare un
            # DataFrame upstream (branch api_enrich → rest_file). Solo se
            # e' stato creato qui; non tocchiamo path provenienti da
            # file_source o da un altro file-processor a monte.
            # FS-5: per il branch in-memory (input_path e' tuple bytes+filename)
            # _owns_materialized_input e' sempre False. Guardia contro
            # un unlink su tuple (che fallirebbe) in caso di future evoluzioni.
            try:
                if _owns_materialized_input and input_path and not isinstance(input_path, tuple):
                    Path(input_path).unlink(missing_ok=True)
            except Exception as _unlink_err:
                logger.debug(
                    f"rest_file: unlink materialized input best-effort "
                    f"fallito: {_unlink_err}"
                )

    def _assert_file_source_group_access(self, file_source_id: str) -> None:
        """Defense-in-depth: verifica che `file_source_id` sia visibile al
        gruppo del progetto corrente (stored in `self._current_group_id`
        da `execute_enrichment`). Fail-closed se il contesto non e' noto.

        Raises:
            ValueError con 403-like message se cross-tenant.
        """
        current_group_id = getattr(self, "_current_group_id", None)
        if not current_group_id:
            # Se non conosciamo il gruppo corrente meglio rifiutare che
            # rischiare un leak (schedule/task context mancante e' gia' un
            # segnale sospetto di per se').
            raise ValueError(
                "Contesto di sicurezza non determinabile per file_source"
            )
        try:
            from app.shared.sharing import is_resource_visible
            if not is_resource_visible(
                "file_sources", file_source_id, current_group_id,
                is_superadmin=False,
            ):
                logger.error(
                    f"[SECURITY] Cross-tenant access blocked: file_source "
                    f"{file_source_id} non visibile al gruppo {current_group_id}",
                )
                raise ValueError(
                    f"Accesso negato: il file sorgente '{file_source_id}' "
                    f"non appartiene al tuo gruppo"
                )
        except ValueError:
            raise
        except Exception as _sec_err:
            logger.warning(
                f"Cross-tenant check file_source fallito: {_sec_err}"
            )
            raise ValueError(
                f"Errore verifica accesso file_source '{file_source_id}'"
            ) from _sec_err

    def _resolve_file_source_id_path(self, file_source_id: str) -> Optional[str]:
        """Materializza il blob di un file_source su filesystem come cache.

        DB e' sempre source of truth. Questa funzione estrae il blob dal
        DB in una cache locale (`{FILES_DIR}/{file_source_id}/{filename}`)
        perche' fiona/GDAL lavorano con path fisici. Il file in cache
        puo' essere eliminato in qualsiasi momento — viene ricreato al
        prossimo accesso.

        Flusso:
          1. Check cache: se il file esiste in cache con il nome atteso
             (da DB metadata), usalo
          2. Altrimenti estrai il blob dal DB, scrivi in modo atomico
             (.tmp + os.replace) per evitare race tra worker concorrenti

        SECURITY: cross-tenant group check prima di qualsiasi lookup.
        Senza questo, un canvas con `file_source_id` di un altro tenant
        verrebbe materializzato su disco / estratto dal blob.

        Restituisce None se il file_source non esiste o e' vuoto.
        """
        if not file_source_id:
            return None

        self._assert_file_source_group_access(file_source_id)

        import os as _os
        from app.config import settings

        # Fix-A: il broad `except Exception -> warning + return None`
        # outer e' eliminato. Errori tecnici (DB / FS) sono ora
        # classificati e sollevati come `FileSourceResolutionError`
        # con chain preservata. `return None` resta solo sui rami
        # dati legittimi (metadata assente / blob vuoto).
        try:
            row_meta = db_svc.get_file_source(file_source_id, include_data=False)
        except psycopg2.Error as e:
            raise FileSourceResolutionError(
                file_source_id=file_source_id,
                cause="lookup",
                reason="errore lettura metadata file_source dal DB",
                original_error=e,
            ) from e

        if not row_meta:
            return None
        expected_name = (
            row_meta.get("original_filename")
            or (row_meta.get("metadata") or {}).get("original_filename")
            or "input.bin"
        )

        source_dir = settings.get_file_source_dir(file_source_id)

        # Cache hit: se il file gia' materializzato esiste con il
        # nome atteso, riuso (evita I/O ripetuto tra task che usano
        # lo stesso file_source nello stesso nodo).
        target = source_dir / expected_name
        if target.is_file():
            return str(target.resolve())

        # Cache miss: estrai blob da DB e scrivi atomico
        try:
            row_full = db_svc.get_file_source(file_source_id, include_data=True)
        except psycopg2.Error as e:
            raise FileSourceResolutionError(
                file_source_id=file_source_id,
                cause="lookup",
                reason="errore lettura blob file_source dal DB",
                original_error=e,
            ) from e

        if not row_full or not row_full.get("data"):
            return None
        raw = row_full["data"]

        try:
            source_dir.mkdir(parents=True, exist_ok=True)
            out_path = source_dir / expected_name
            tmp_path = source_dir / f".{expected_name}.{_os.getpid()}.tmp"
            try:
                tmp_path.write_bytes(raw if isinstance(raw, bytes) else bytes(raw))
                _os.replace(str(tmp_path), str(out_path))
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception as _tmp_err:
                        logger.debug(
                            f"_resolve_file_source_id_path: unlink tmp_path "
                            f"best-effort fallito: {_tmp_err}"
                        )
        except OSError as e:
            raise FileSourceResolutionError(
                file_source_id=file_source_id,
                cause="materialization",
                reason="scrittura cache file_source su filesystem fallita",
                original_error=e,
            ) from e

        logger.debug(
            f"file_source {file_source_id}: blob materializzato in cache → {out_path}"
        )
        return str(out_path.resolve())

    def _resolve_file_source_path(self, nodes: List[Dict], edges: List[Dict],
                                   target_node_id: str) -> Optional[str]:
        """
        Risolve il path originale del file dal nodo file_source collegato.

        Cerca il nodo sorgente collegato via edge al target_node_id.
        Se è un file_source, trova il file originale nel file system.
        Se è un project_source, trova l'ultimo output del progetto.
        """
        # Trova edge in ingresso
        input_edge = next((e for e in edges if e['target'] == target_node_id), None)
        if not input_edge:
            return None

        source_node = next((n for n in nodes if n['id'] == input_edge['source']), None)
        if not source_node:
            return None

        if source_node.get('type') == 'file_source':
            file_source_id = _nd(source_node, 'file_source_id')
            return self._resolve_file_source_id_path(file_source_id)
        
        elif source_node.get('type') == 'project_source':
            source_project_id = _nd(source_node, 'source_project_id')
            if not source_project_id:
                return None

            # Tenant binding fail-closed via SourceLoader: tutto il
            # source loading di api_connector passa per il loader unico,
            # niente accesso diretto ad ArtifactResolver. Senza
            # _current_group_id rifiutiamo esplicitamente: meglio
            # errore visibile che leak silente.
            executing_group_id = getattr(self, '_current_group_id', None)
            if not executing_group_id:
                logger.error(
                    f"[SECURITY] _resolve_file_source_path: group_id "
                    f"corrente sconosciuto, rifiuto risoluzione "
                    f"project_source {source_project_id} (fail-closed)"
                )
                raise ValueError(
                    "Contesto di sicurezza non determinabile (group_id mancante)"
                )

            from app.core.artifacts import (
                LatestProjectArtifactRef,
                SourceLoader,
                SourceLoadingError,
            )
            try:
                return SourceLoader().resolve_project_path(
                    LatestProjectArtifactRef(project_id=source_project_id),
                    expected_group_id=executing_group_id,
                )
            except SourceLoadingError as e:
                # Cross-tenant block o progetto inesistente: hard fail
                # con messaggio che preserva il dettaglio originale del
                # loader, coerente con _load_source_project.
                logger.error(
                    f"[SECURITY] _resolve_file_source_path cross-tenant: "
                    f"{source_project_id} per group={executing_group_id}: {e}"
                )
                raise ValueError(
                    f"Accesso negato: progetto sorgente "
                    f"'{source_project_id}': {e}"
                ) from e

        return None

    def _resolve_input_path_for_file_processor(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        target_node_id: str,
        node_results: Optional[Dict] = None,
        materialize_format: str = "xlsx",
    ) -> tuple:
        """Risolve il path di input per un nodo file-processor (rest_file)
        accettando come predecessore anche un api_enrich o un altro file-processor.

        Estende `_resolve_file_source_path` con due branch aggiuntivi quando
        `node_results` è disponibile:

        1. Upstream è un altro file-processor (`is_file_result=True`): riusa
           direttamente il suo `output_path`, così rest_file→rest_file
           funziona senza duplicare file su disco.
        2. Upstream è un api_enrich che ha prodotto un pandas.DataFrame:
           serializza il DataFrame in un file temporaneo (formato scelto dal
           nodo, default xlsx) e ritorna quel path. Il caller è responsabile
           del cleanup.

        Ritorna `(input_ref, owns_disk_file)` dove `input_ref` e' una delle seguenti:
          - `str` (path): file su disco pre-esistente (file_source / upstream
            file-processor output). NON owned: il caller non deve cancellarlo.
          - `tuple(bytes, filename)`: DataFrame upstream serializzato
            in-memory (no disk touch). No cleanup necessario (GC naturale).
          - `None`: nessun input risolvibile (il caller gestisce required/skip).

        `owns_disk_file`: True solo in casi legacy dove un tempfile e'
        stato creato e va cancellato. Dopo FS-5 (in-memory refactor del
        materialize), il branch DataFrame ritorna sempre bytes/False —
        owns=True resta solo per path backward-compat non usati oggi.

        Se non c'è un upstream utilizzabile via node_results, fa fallback al
        comportamento classico di `_resolve_file_source_path` (file_source
        / project_source), con `owns_disk_file=False`.
        """
        if node_results is not None:
            input_edge = next((e for e in edges if e.get('target') == target_node_id), None)
            if input_edge is not None:
                source_id = input_edge.get('source')
                source_node = next((n for n in nodes if n.get('id') == source_id), None)
                upstream_type = source_node.get('type') if source_node else None
                upstream_result = node_results.get(source_id)

                # Branch 1: upstream e' un altro file-processor (rest_file)
                # il cui risultato e' gia' un file su disco.
                if isinstance(upstream_result, dict) and upstream_result.get('is_file_result'):
                    out_path = upstream_result.get('output_path')
                    if out_path and Path(out_path).exists():
                        logger.info(
                            f"file-processor input: riuso output file del nodo upstream "
                            f"{source_id} ({out_path})"
                        )
                        return str(out_path), False

                # Branch 2: upstream ha prodotto un DataFrame (api_enrich o simili).
                # FS-5: serializzazione in-memory → bytes. Nessun tempfile.
                # Il caller (`_build_rest_file_multipart`) wrappa i bytes in
                # io.BytesIO per requests multipart — requests accetta ogni
                # file-like object che implementa read().
                if isinstance(upstream_result, pd.DataFrame) and upstream_type != 'file_source':
                    from app.utils.dataframe import save_dataframe_to_bytes
                    fmt = (materialize_format or 'xlsx').lower()
                    supported = {'xlsx', 'csv', 'json', 'parquet'}
                    if fmt not in supported:
                        logger.warning(
                            f"file-processor input: materialize_format {fmt!r} non supportato "
                            f"in-memory (supportati: {supported}), uso xlsx"
                        )
                        fmt = 'xlsx'
                    try:
                        data_bytes = save_dataframe_to_bytes(upstream_result, fmt)
                    except Exception as e:
                        logger.error(
                            f"file-processor input: materializzazione DataFrame upstream "
                            f"fallita per nodo {target_node_id} (fmt={fmt}): {e}"
                        )
                        raise
                    filename = f"input.{fmt}"
                    logger.info(
                        f"file-processor input: DataFrame upstream {source_id} "
                        f"materializzato in-memory (fmt={fmt}, {len(upstream_result)} righe, "
                        f"{len(data_bytes)} bytes)"
                    )
                    return (data_bytes, filename), False

        # Fallback: comportamento classico (file_source / project_source)
        return self._resolve_file_source_path(nodes, edges, target_node_id), False

    def _paginate_generator_results(
        self, *, call_fn, pagination: Dict, base_params: Dict
    ) -> List[Dict]:
        """Esegue la paginazione del generator dispatchando su `pagination['type']`.

        - `''` (vuoto): singola chiamata, ritorna tutto.
        - `'offset'`: incrementa `offset_param` di `page_size` ogni
          pagina, ferma su empty / `len < page_size` / `>= max_rows`.
        - `'cursor'`: estrae `cursor_path` dal body e lo passa come
          `cursor_param` alla pagina successiva.
        - `'link'`: segue URL `next` dal body (`link_path`) o
          dall'header `Link` RFC 5988. Dopo la prima pagina svuota
          `base_params` (l'URL e' gia' completo).
        - tipo sconosciuto: warn + singola chiamata fallback.

        `MAX_PAGES = max_rows // page_size + 1` come safety cap globale.
        """
        pagination_type = pagination.get('type', '')

        if not pagination_type:
            rows, _, _ = call_fn(base_params)
            logger.info(f"Generator completato: {len(rows)} righe (no paginazione)")
            return rows or []

        page_size = int(pagination.get('page_size', 100))
        max_rows = int(pagination.get('max_rows', 10000))
        all_rows: List[Dict] = []
        page_num = 0
        MAX_PAGES = max(1, max_rows // max(1, page_size)) + 1

        if pagination_type == 'offset':
            offset_param = pagination.get('offset_param', 'offset')
            limit_param = pagination.get('limit_param', 'limit')
            start = int(pagination.get('start_page', 0))

            for page_num in range(MAX_PAGES):
                call_params = {
                    **base_params,
                    offset_param: start + page_num * page_size,
                    limit_param: page_size,
                }
                rows, _, _ = call_fn(call_params)
                if not rows:
                    break
                all_rows.extend(rows)
                logger.info(
                    f"Generator offset pag {page_num+1}: +{len(rows)} righe "
                    f"(tot {len(all_rows)})"
                )
                if len(all_rows) >= max_rows:
                    all_rows = all_rows[:max_rows]
                    break
                if len(rows) < page_size:
                    break  # ultima pagina parziale

        elif pagination_type == 'cursor':
            cursor_param = pagination.get('cursor_param', 'cursor')
            cursor_path = pagination.get('cursor_path', 'next_cursor')
            cursor_value = None

            for page_num in range(MAX_PAGES):
                call_params = {**base_params}
                if cursor_value:
                    call_params[cursor_param] = cursor_value
                rows, data, _ = call_fn(call_params)
                if not rows:
                    break
                all_rows.extend(rows)
                logger.info(
                    f"Generator cursor pag {page_num+1}: +{len(rows)} righe "
                    f"(tot {len(all_rows)})"
                )
                cursor_value = self._extract_json_value(data, cursor_path)
                if not cursor_value:
                    break
                if len(all_rows) >= max_rows:
                    all_rows = all_rows[:max_rows]
                    break

        elif pagination_type == 'link':
            link_path = pagination.get('link_path', 'next')
            next_url = None
            link_base_params = base_params  # mutiamo la copy locale

            for page_num in range(MAX_PAGES):
                rows, data, resp = call_fn(link_base_params, call_url=next_url)
                if not rows:
                    break
                all_rows.extend(rows)
                logger.info(
                    f"Generator link pag {page_num+1}: +{len(rows)} righe "
                    f"(tot {len(all_rows)})"
                )
                next_url = self._extract_json_value(data, link_path)
                if not next_url:
                    link_header = resp.headers.get('Link', '')
                    match = re.search(r'<([^>]+)>;\s*rel=["\']next["\']', link_header)
                    next_url = match.group(1) if match else None
                if not next_url:
                    break
                if len(all_rows) >= max_rows:
                    all_rows = all_rows[:max_rows]
                    break
                # Dopo prima pagina, non rimandare base_params (URL gia' completo)
                link_base_params = {}

        else:
            logger.warning(
                f"Tipo paginazione sconosciuto: '{pagination_type}', "
                f"eseguo singola chiamata"
            )
            rows, _, _ = call_fn(base_params)
            all_rows = rows or []

        logger.info(
            f"Generator completato: {len(all_rows)} righe totali in "
            f"{page_num+1} pagine"
        )
        return all_rows

    @staticmethod
    def _substitute_url_path_params(
        url: str, params: Dict, *, safe: str = "-._~:@!$&'()*+,=;/"
    ) -> tuple:
        """Sostituisce placeholder `{name}` nel URL con i valori da `params`.

        Ritorna `(updated_url, query_params)` dove `query_params` contiene
        solo le chiavi che NON erano placeholder nel URL (vanno in
        querystring). I valori sostituiti nel path sono URL-encoded.

        `safe` controlla quali caratteri NON vengono percent-encodati:
        - default `"-._~:@!$&'()*+,=;/"`: permissivo, preserva slash/virgola
          (usato da `_execute_api_enricher` e `test_api_connection` —
          alcuni endpoint si aspettano `lat,lon` non encoded nel path).
        - `""`: stretto, encoda tutto (usato da `_execute_api_generator`).

        Usato da `_execute_api_generator`, `_execute_api_enricher` e
        `test_api_connection`.
        """
        target_url = url
        query_params: Dict = {}
        for pname, pvalue in params.items():
            placeholder = f'{{{pname}}}'
            if placeholder in target_url:
                encoded = requests.utils.quote(str(pvalue), safe=safe)
                target_url = target_url.replace(placeholder, encoded)
            else:
                query_params[pname] = pvalue
        return target_url, query_params

    def _call_generator_endpoint(
        self,
        *,
        url: str,
        method: str,
        headers: Dict,
        call_params: Dict,
        call_url: Optional[str],
        response_preset: str,
        response_config: Dict,
        output_mapping: List[Dict],
    ) -> tuple:
        """Esegue una singola chiamata del generator e parsa la response.

        Path param substitution → SSRF guard → GET/POST → status check
        → dispatch parser (preset > response_config no-wildcard >
        output_mapping). Ritorna `(rows, raw_data, response)`.

        Solleva `ValueError` su HTTP error.
        """
        from app.shared.api_utils import (
            get_shared_session,
            validate_external_url,
        )

        target_url = call_url or url
        # Generator: encoding stretto (safe='') — i path param vengono
        # encoded interamente, niente eccezioni per slash/virgola.
        target_url, query_params = self._substitute_url_path_params(
            target_url, call_params, safe=''
        )

        validate_external_url(target_url)

        logger.info(f"Generator API call: {method} {target_url}")
        session = get_shared_session()
        if method == 'GET':
            resp = session.get(
                target_url, params=query_params, headers=headers, timeout=60
            )
        else:
            resp = session.post(
                target_url, json=call_params, headers=headers, timeout=60
            )

        if resp.status_code != 200:
            err = resp.text[:500] if resp.text else 'nessuna risposta'
            raise ValueError(f"API error: HTTP {resp.status_code} — {err}")

        data = resp.json()

        if response_preset:
            preset_cfg = get_preset_config(response_preset)
            if preset_cfg:
                return ResponseParser(preset_cfg).parse(data), data, resp

        # `response_config` viene usato solo se ha mappings configurati
        # e senza wildcard `[*]` — i path con wildcard sono gestiti da
        # `_extract_rows_from_response` (specializzato per gli array
        # generati dal wizard).
        rc_mappings = response_config.get('mappings', []) if response_config else []
        has_wildcard = any('[*]' in m.get('path', '') for m in rc_mappings)
        if response_config and rc_mappings and not has_wildcard:
            return ResponseParser(response_config).parse(data), data, resp

        return self._extract_rows_from_response(data, output_mapping), data, resp

    def _build_generator_base_params(
        self, api_config: Dict, input_df: Optional[pd.DataFrame]
    ) -> Dict:
        """Costruisce i parametri base per `_execute_api_generator`.

        Strategie:
        - Se `input_df` non e' vuoto: prima riga → dict di parametri
          stringificati (ignora None/blank).
        - Altrimenti: `static_parameters` con template processing
          (`{{NOW}}`, `{{TODAY}}`, ecc.) per i valori string.

        Aggiunge `api_key` come query param se l'auth e' di tipo
        `api_key` con `location='query'`.
        """
        auth = api_config.get('auth', {}) or {}
        if input_df is not None and not input_df.empty:
            row = input_df.iloc[0].to_dict()
            base_params = {
                k: str(v).strip()
                for k, v in row.items()
                if v is not None and str(v).strip()
            }
            logger.info(
                f"Generator usando parametri da input: "
                f"{sanitize_for_log(base_params, auth)}"
            )
        else:
            base_params = {}
            static_params = api_config.get('static_parameters', {}) or {}
            for k, v in static_params.items():
                if isinstance(v, str):
                    fields = [{'name': k, 'value': v}]
                    processed = process_static_fields(fields)
                    base_params[k] = processed.get(k, v)
                else:
                    base_params[k] = v
            logger.info(
                f"Generator usando static_parameters (processed): "
                f"{sanitize_for_log(base_params, auth)}"
            )

        if auth.get('type') == 'api_key' and auth.get('location') == 'query':
            base_params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')

        return base_params

    def _execute_api_generator(self, api_config: Dict, input_df: pd.DataFrame = None) -> pd.DataFrame:
        """
        Esegue API di tipo 'generator' — genera un DataFrame dalla risposta.

        Supporta paginazione automatica con tre strategie:
            - offset:  aggiunge offset/limit ad ogni chiamata fino a risultati vuoti o max_rows
            - cursor:  usa un token/cursor dalla risposta per la chiamata successiva
            - link:    segue un URL 'next' nel body o nell'header Link della risposta

        Configurazione paginazione (in api_config['pagination']):
            type:            'offset' | 'cursor' | 'link'   (default: nessuna)
            page_size:       righe per pagina               (default: 100)
            max_rows:        limite totale righe             (default: 10000)
            offset_param:    nome parametro offset          (default: 'offset')
            limit_param:     nome parametro limit/page_size (default: 'limit')
            cursor_param:    nome parametro cursor          (default: 'cursor')
            cursor_path:     path JSON del cursor nella risposta (default: 'next_cursor')
            link_path:       path JSON URL prossima pagina  (default: 'next')
            start_page:      indice prima pagina (offset)   (default: 0)

        Strategie di parsing risposta (in ordine di priorità):
            1. response_preset: preset predefinito (italiameteo_observations, geojson_features)
            2. response_config: configurazione custom per strutture JSON annidate
            3. output_mapping:  mapping semplice path→colonna (retrocompatibilità)
        """
        endpoint = api_config.get('endpoint', {})
        url = endpoint.get('url', '')
        method = endpoint.get('method', 'GET').upper()
        auth = api_config.get('auth', {})
        custom_headers = api_config.get('custom_headers', {})
        output_mapping = api_config.get('output_mapping', [])
        response_config = api_config.get('response_config', {})
        response_preset = api_config.get('response_preset', '')
        pagination = api_config.get('pagination', {})

        if not url:
            raise ValueError("URL API non configurato")

        headers = self._prepare_headers(auth, custom_headers)
        base_params = self._build_generator_base_params(api_config, input_df)

        def _call_and_parse(call_params, call_url=None):
            return self._call_generator_endpoint(
                url=url,
                method=method,
                headers=headers,
                call_params=call_params,
                call_url=call_url,
                response_preset=response_preset,
                response_config=response_config,
                output_mapping=output_mapping,
            )

        all_rows = self._paginate_generator_results(
            call_fn=_call_and_parse,
            pagination=pagination,
            base_params=base_params,
        )
        return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()
    
    def _extract_rows_from_response(self, data: Any, output_mapping: List[Dict]) -> List[Dict]:
        """
        Estrae righe dalla response API usando output_mapping semplice.
        
        Per strutture annidate complesse, usare response_config o response_preset.
        Gestisce path con wildcard [*] tipo data[*].id generati dal wizard.
        """
        rows = []

        # Path con wildcard `[*]` (es. `data[*].id`): estrai il root
        # array e itera con path relativo all'item.
        wildcard_paths = [m.get('path', '') for m in output_mapping if '[*]' in m.get('path', '')]
        if wildcard_paths:
            # Trova il prefisso comune prima di [*] (es. "data")
            first_path = wildcard_paths[0]
            array_root = first_path.split('[*]')[0].rstrip('.')  # es. "data"
            # Naviga all'array root
            array_data = self._extract_json_value(data, array_root) if array_root else data
            if not isinstance(array_data, list):
                array_data = [array_data] if array_data is not None else []
            for item in array_data:
                row = {}
                for m in output_mapping:
                    path = m.get('path', '')
                    col = m.get('column', path)
                    if '[*]' in path:
                        # path relativo all'item (dopo [*].)
                        rel = path.split('[*]', 1)[1].lstrip('.')
                        row[col] = self._extract_json_value(item, rel) if rel else item
                    else:
                        row[col] = self._extract_json_value(data, path)
                rows.append(row)
            return rows

        # GeoJSON FeatureCollection
        if isinstance(data, dict) and data.get('type') == 'FeatureCollection':
            for feature in data.get('features', []):
                row = {}
                for mapping in output_mapping:
                    path = mapping.get('path', '')
                    col_name = mapping.get('column', path)
                    if path.startswith('features['):
                        parts = path.split('.', 1)
                        path = parts[1] if len(parts) > 1 else ''
                    row[col_name] = self._extract_json_value(feature, path)
                rows.append(row)
        
        # Array — controlla se i path sono assoluti sulla lista (es. [0].name.common)
        # In questo caso produce UNA sola riga estraendo i valori dalla lista intera
        elif isinstance(data, list):
            all_absolute = output_mapping and all(
                re.match(r'^\[\d+\]', m.get('path', '')) for m in output_mapping
            )
            if all_absolute:
                row = {}
                for m in output_mapping:
                    col = m.get('column', m.get('path'))
                    row[col] = self._extract_json_value(data, m.get('path', ''))
                rows.append(row)
            else:
                for item in data:
                    row = {m.get('column', m.get('path')): self._extract_json_value(item, m.get('path', ''))
                           for m in output_mapping}
                    rows.append(row)
        
        # Oggetto singolo con array "data" (auto-detect)
        elif isinstance(data, dict) and 'data' in data and isinstance(data.get('data'), list):
            logger.info("Auto-detect: usando array 'data' come root")
            for item in data['data']:
                row = {m.get('column', m.get('path')): self._extract_json_value(item, m.get('path', '')) 
                       for m in output_mapping}
                rows.append(row)
        
        # Oggetto singolo
        elif isinstance(data, dict):
            row = {m.get('column', m.get('path')): self._extract_json_value(data, m.get('path', '')) 
                   for m in output_mapping}
            rows.append(row)
        
        return rows
    
    def _enrich_one_row(
        self,
        *,
        row,
        api_config: Dict,
        headers: Dict,
        param_mapping: Dict,
        output_mapping: List[Dict],
        method: str,
        url: str,
        idx,
    ) -> Optional[Dict]:
        """Esegue l'enrichment per una singola riga del DataFrame.

        Costruisce parametri (static + mappati da colonne + api_key in
        query), sostituisce path params nell'URL, fa la chiamata HTTP,
        estrae i valori per le colonne output.

        Ritorna `dict[col_name → value]` su success (200), `None` su
        errore HTTP (lascia il caller incrementare l'error counter senza
        propagare l'eccezione). Le eccezioni network/parse risalgono al
        caller (che le cattura nel try/except del loop).
        """
        from app.shared.api_utils import (
            get_shared_session,
            validate_external_url,
        )

        auth = api_config.get('auth', {}) or {}
        params = dict(api_config.get('static_parameters', {}) or {})

        for api_param, df_column in param_mapping.items():
            if df_column and df_column in row:
                value = row[df_column]
                params[api_param] = str(value) if pd.notna(value) else None

        if auth.get('type') == 'api_key' and auth.get('location') == 'query':
            params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')

        call_url, call_params = self._substitute_url_path_params(url, params)

        validate_external_url(call_url)
        session = get_shared_session()
        if method == 'GET':
            response = session.get(
                call_url, params=call_params, headers=headers, timeout=30
            )
        else:
            response = session.post(
                call_url, json=call_params, headers=headers, timeout=30
            )

        if response.status_code != 200:
            logger.warning(
                f"Riga {idx}: HTTP {response.status_code} - "
                f"{response.text[:200]}"
            )
            return None

        data = response.json()
        extracted: Dict[str, Any] = {}
        for mapping in output_mapping:
            path = mapping.get('path', '')
            col_name = mapping.get('column', path)
            extracted[col_name] = self._extract_json_value(data, path)
        return extracted

    def _execute_api_enricher(self, df: pd.DataFrame, api_config: Dict, param_mapping: Dict) -> pd.DataFrame:
        """
        Esegue API di tipo 'enricher' — arricchisce ogni riga del DataFrame.
        
        Per ogni riga: costruisce parametri (fissi + mappati da colonne),
        chiama l'API, estrae i campi dalla risposta JSON, e li scrive
        nelle colonne output. Rispetta il rate limiting tra le chiamate.
        
        Questa è la versione semplificata (sincrona, senza deduplicazione).
        Per l'esecuzione avanzata (parallela, con cache, batch) vedere
        enrichment_service.py / EnrichmentExecutor.
        """
        import time
        
        result_df = df.copy()
        
        endpoint = api_config.get('endpoint', {})
        url = endpoint.get('url', '')
        method = endpoint.get('method', 'GET').upper()
        auth = api_config.get('auth', {})
        custom_headers = api_config.get('custom_headers', {})
        output_mapping = api_config.get('output_mapping', [])
        
        if not url:
            raise ValueError("URL API non configurato")
        
        # Prepara colonne output
        for mapping in output_mapping:
            col_name = mapping.get('column', mapping.get('path'))
            if col_name not in result_df.columns:
                result_df[col_name] = None
        
        # Token OAuth2 gestito via cache con expires_at (vedi api_utils.get_cached_oauth2_token).
        # Il refresh avviene automaticamente solo quando il token sta per scadere — non serve
        # piu il vecchio pattern "rinnova ogni N righe" basato su un counter arbitrario.
        _is_oauth2 = auth.get('type') in ('oauth2_password', 'oauth2_client_credentials')

        def _get_fresh_headers():
            return self._prepare_headers(auth, custom_headers)

        headers = _get_fresh_headers()

        rate_limit = api_config.get('options', {}).get('rate_limit', 5)
        delay = 1.0 / rate_limit if rate_limit > 0 else 0

        success_count = 0
        error_count = 0

        for idx, row in df.iterrows():
            try:
                if _is_oauth2:
                    headers = _get_fresh_headers()

                extracted_values = self._enrich_one_row(
                    row=row,
                    api_config=api_config,
                    headers=headers,
                    param_mapping=param_mapping,
                    output_mapping=output_mapping,
                    method=method,
                    url=url,
                    idx=idx,
                )
                if extracted_values is not None:
                    for col_name, value in extracted_values.items():
                        result_df.at[idx, col_name] = value
                    success_count += 1
                else:
                    error_count += 1

                if delay > 0:
                    time.sleep(delay)

            except Exception as _row_err:
                # skip-on-row-error per design (vedi docstring
                # `_enrich_one_row`): il loop continua sul batch.
                # Log a `debug` per evitare flood su dataset grandi;
                # il riassunto finale `info` mostra il counter aggregato.
                logger.debug(
                    f"Riga {idx}: eccezione skippata "
                    f"({type(_row_err).__name__}: {_row_err})"
                )
                error_count += 1

            if (idx + 1) % 100 == 0:
                logger.info(f"Progresso: {idx + 1}/{len(df)} (OK: {success_count}, ERR: {error_count})")
        
        logger.info(f"Completato: {success_count}/{len(df)} righe, {error_count} errori")
        return result_df
    
    def _extract_json_value(self, data: Any, path: str) -> Any:
        """Estrae valore da JSON usando dot notation. Delegato a shared/api_utils."""
        return _shared_extract_json_value(data, path)


# --- Singleton globale ---
# Usato da api/enrichment.py e main.py per accedere al servizio
api_connection_service = ApiConnectionService()