"""Canvas-based enrichment orchestrator.

BE-GO-READY-TRANSFORMATION-ENGINE-011-API-CONNECTOR-ORCHESTRATOR-EXTRACTION.

Estrae da `app.core.arricchisci.api_connector.ApiConnectionService` la
logica di esecuzione del canvas-graph arricchisci (`execute_enrichment`)
+ i 5 helper strettamente coupled: `_init_execution_context`,
`_get_execution_order` (Kahn), `_run_single_enricher`,
`_is_file_source_for_file_processor`. Quei 5 metodi non parlano di
"come fare una API call" — descrivono "come orchestrare un canvas
arricchisci" (BFS topologica, dispatch per node_type, parallel
groups, error boundary, final result shaping).

Boundary contract
=================
Il modulo orchestrator NON conosce:
- come parlare HTTP / OAuth / pagination (vivono in
  `app.core.arricchisci.api_connection_generator_enricher` mixin via
  `svc._execute_api_*`);
- la shape di una `api_connection` row (vive nel mixin management);
- come risolvere un `file_source_id` su FS/DB/S3 (vive nel mixin
  rest_file);
- come materializzare un DataFrame in CSV/GeoJSON/etc. (vive nel
  mixin support → `_save_and_build_dataframe_response`).

Il modulo orchestrator riceve un `svc: ApiConnectionService` come
parametro e ne usa i metodi pubblici/protected come capability
boundary (DI per dipendenza, anche se classe esistente). Lo state
`svc._current_group_id` / `svc._preview_limit` viene scritto qui
(orchestrator owns the execution context) e letto dai mixin
SOURCE_LOADING / REST_FILE per il tenant binding.

KEEP_COMPAT
===========
`ApiConnectionService.execute_enrichment(...)` resta come metodo
sulla classe (wrapper thin che delega a
`canvas_orchestrator.execute_canvas_enrichment(self, ...)`).
Ragioni: ~5 consumer esterni esistenti
(`executor.py`, `router.py`, `eseguibili/scanner.py`, test legacy
`test_api_connector_execute_enrichment_lock.py` patchano via
`svc.execute_enrichment(...)`). Senza il wrapper il refactor
romperebbe il contratto pubblico.

Test esistenti: il fixture `env` in
`tests2/arricchisci/unit/test_api_connector_execute_enrichment_lock.py`
fa `patch.object(api_service, "_load_source_project", ...)` ecc. —
i 5 metodi mockati restano sulla classe via mixin; il dispatch
`svc._load_source_project` continua a funzionare.

Go-ready mapping
================
    type CanvasOrchestrator struct {
        Svc *ApiConnectionService
    }
    func (o *CanvasOrchestrator) Execute(ctx, projectID, canvas, ...)
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import pandas as pd

import app.db.db_service as db_svc  # noqa: F401  (test-monkeypatch surface preserved)
from app.core.arricchisci._api_connection_helpers import _nd
from app.core.arricchisci.execution_context import (
    ArricchisciExecutionContext,
    apply_to_legacy_state,
    build_execution_context,
)
from app.utils.logger import get_logger


if TYPE_CHECKING:
    from app.core.arricchisci.api_connector import ApiConnectionService


logger = get_logger(__name__)


__all__ = [
    "execute_canvas_enrichment",
    "init_execution_context",
    "get_execution_order",
    "run_single_enricher",
    "is_file_source_for_file_processor",
    # BE-GO-READY-TRANSFORMATION-ENGINE-015: re-export per consumer
    # esterni che vogliono il typed boundary senza dover importare
    # il modulo `execution_context` separatamente.
    "ArricchisciExecutionContext",
    "build_execution_context",
]


# ---------------------------------------------------------------------------
# Pure / orchestration helpers
# ---------------------------------------------------------------------------


def get_execution_order(
    nodes: List[Dict],
    edges: List[Dict],
) -> List[str]:
    """Calcola ordine topologico (algoritmo di Kahn).

    Pure function: non legge state del servizio. Estratta da
    `ApiConnectionService._get_execution_order`. Behavior preservato
    byte-per-byte.
    """
    graph = {n["id"]: [] for n in nodes}
    in_degree = {n["id"]: 0 for n in nodes}

    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
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


def is_file_source_for_file_processor(
    node_id: str,
    edges: List[Dict],
    node_map: Dict[str, Dict],
    api_connections: Dict[str, Dict],
) -> bool:
    """True se il file_source e' input diretto di un nodo api_enrich
    di tipo file-processor (`rest_file`).

    Pure function. Estratta da
    `ApiConnectionService._is_file_source_for_file_processor`.
    """
    for de in edges:
        if de.get("source") != node_id:
            continue
        target_node = node_map.get(de.get("target"))
        if not target_node or target_node.get("type") != "api_enrich":
            continue
        target_api_id = _nd(target_node, "api_id")
        target_api_config = api_connections.get(target_api_id, {})
        if target_api_config.get("connection_type") == "rest_file":
            return True
    return False


def init_execution_context(
    svc: "ApiConnectionService",
    project_id: str,
    preview_limit: Optional[int],
) -> ArricchisciExecutionContext:
    """Inizializza il contesto di esecuzione per `execute_canvas_enrichment`.

    BE-GO-READY-TRANSFORMATION-ENGINE-015: l'implementazione e'
    stata spezzata in due step espliciti:

    1. `build_execution_context(project_id, preview_limit)` —
       costruisce un `ArricchisciExecutionContext` immutabile
       (frozen dataclass) leggendo `db_svc.get_project(...)`.
       Pure-ish: niente side effect su `svc`.
    2. `apply_to_legacy_state(svc, ctx)` — KEEP_COMPAT phase 1:
       rispecchia `ctx.group_id` / `ctx.preview_limit` su
       `svc._current_group_id` / `svc._preview_limit` perche' i
       mixin downstream (`api_connection_source_loading`,
       `api_connection_rest_file`) leggono ancora gli attributi
       sul singleton. Phase 2 (TE15-R1) migrera' i mixin a ricevere
       `ctx` esplicitamente.

    Fail-closed: errori `psycopg2.Error` propagati come
    `ExecutionContextError` (chain preservata) dal builder
    sopra. Il boundary di `execute_canvas_enrichment` cattura nel
    ramo `except Exception` → response safe per il caller.

    Su progetto inesistente (`_proj_row is None`):
    `ctx.group_id = None` (ramo dati), `_current_group_id = None`
    via mirror; i check downstream sollevano "Contesto di
    sicurezza non determinabile".

    Returns:
        Il `ArricchisciExecutionContext` costruito. La chiave
        e' che ogni esecuzione produce un context fresco
        immutabile invece di mutare lo stato globale del
        singleton in-place.
    """
    # Invariant lock-test (`test_init_execution_context_fail_closed::
    # test_init_execution_context_resets_stale_group_id_before_db_call`):
    # azzerare il group_id sul singleton PRIMA del path che puo'
    # fallire (DB lookup). Se `build_execution_context` solleva
    # `ExecutionContextError`, lo stato stale dal run precedente
    # non sopravvive. Il preview_limit viene impostato subito per
    # preservare il comportamento legacy anche sul path di errore.
    svc._preview_limit = preview_limit
    svc._current_group_id = None
    ctx = build_execution_context(project_id, preview_limit)
    apply_to_legacy_state(svc, ctx)
    return ctx


def run_single_enricher(
    svc: "ApiConnectionService",
    node_data: Dict,
    api_conns: Dict,
    in_df: Any,
) -> Any:
    """Esegue un singolo nodo enricher.

    Dispatch su `api_type`: generator (1 call → df intero) vs
    enricher (1 call per riga). Usato sia direttamente che come
    callback da `execute_parallel_enrichers`. Solleva `ValueError`
    se l'api_id non e' nelle connections decifrate.
    """
    api_id = _nd(node_data, "api_id")
    api_config = api_conns.get(api_id)
    if not api_config:
        raise ValueError(f"API '{api_id}' non trovata")
    if api_config.get("api_type", "enricher") == "generator":
        return svc._execute_api_generator(api_config, in_df)
    param_mapping = _nd(node_data, "param_mapping", {})
    return svc._execute_api_enricher(in_df, api_config, param_mapping)


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def execute_canvas_enrichment(
    svc: "ApiConnectionService",
    project_id: str,
    canvas: Dict,
    format: str = "csv",
    force_recalculate: bool = False,
    preview_limit: Optional[int] = None,
    run_id: Optional[str] = None,
    intermediate_mode_override: Optional[str] = None,
) -> Dict:
    """Orchestra l'esecuzione del canvas arricchisci.

    Estratta byte-per-byte da
    `ApiConnectionService.execute_enrichment` (BE-GO-READY-
    TRANSFORMATION-ENGINE-011). Il metodo originale resta sulla
    classe come wrapper thin per KEEP_COMPAT.

    Flusso:
    1. Carica tutte le API connections configurate (decifrate).
    2. Calcola ordine topologico dei nodi (Kahn BFS).
    3. Raggruppa per esecuzione parallela
       (`parallel.compute_parallel_groups`).
    4. Per ogni step: dispatch per `node_type`
       (`project_source / file_source / staticValues / api_enrich /
       result`).
    5. Salva DataFrame finale nel formato richiesto.

    Error boundary:
    - `ValueError` → `{"success": False, "user_error": True,
      "error": str(e)}` (messaggi gia' user-safe da `disclosure` /
      cross-tenant / SSRF / ecc.).
    - `Exception` generico → `{"success": False, "user_error":
      False, "error": "Errore interno durante l'arricchimento"}`.

    `intermediate_mode_override` (R-25 PR-B2.b): override per-call
    propagato a `_write_intermediate_snapshot`. None = bit-compat.
    """
    try:
        init_execution_context(svc, project_id, preview_limit)

        nodes = canvas.get("nodes", [])
        edges = canvas.get("edges", [])

        if not nodes:
            return {"success": False, "error": "Nessun nodo nel canvas"}

        # Carica tutte le API connections con credenziali decifrate (1 query)
        api_connections = svc.list_api_connections_decrypted()

        # Ordine di esecuzione topologico
        execution_order = get_execution_order(nodes, edges)
        logger.info(f"Ordine esecuzione: {execution_order}")

        # ── Raggruppamento nodi per esecuzione parallela ──
        from app.core.arricchisci.parallel import (
            compute_parallel_groups,
            execute_parallel_enrichers,
            should_execute_node,
        )
        groups = compute_parallel_groups(
            execution_order, nodes, edges,
            api_connections=api_connections,
        )
        logger.info(f"Gruppi di esecuzione: {[(t, len(ids)) for t, ids in groups]}")

        node_map = {n["id"]: n for n in nodes}
        node_results: Dict[str, Any] = {}

        for step_type, step_node_ids in groups:
            if step_type == "single":
                # ── Nodo singolo (sorgente, result, o enricher isolato) ──
                node_id = step_node_ids[0]
                node = node_map.get(node_id)
                if not node:
                    continue
                node_type = node.get("type")
                node_data = node
                logger.debug(f"Esecuzione nodo: {node_id} ({node_type})")

                if node_type == "project_source":
                    node_results[node_id] = svc._load_source_project(
                        node_data, force_recalculate
                    )

                elif node_type == "file_source":
                    if is_file_source_for_file_processor(
                        node_id, edges, node_map, api_connections
                    ):
                        node_results[node_id] = {
                            "_passthrough_file_source": True,
                            "file_source_id": _nd(node, "file_source_id"),
                        }
                    else:
                        node_results[node_id] = svc._load_file_source(node_data)

                elif node_type == "staticValues":
                    node_results[node_id] = svc._load_static_values(node_data)

                elif node_type == "result":
                    input_edge = next(
                        (e for e in edges if e["target"] == node_id), None
                    )
                    if input_edge:
                        input_df = node_results.get(input_edge["source"])
                        if input_df is not None:
                            node_results[node_id] = input_df

                elif node_type == "api_enrich":
                    # PR #22: api_enrich emesso come step "single" da
                    # `compute_parallel_groups` quando RB.#15 flagga il
                    # nodo come cardinality-changing (output_mapping con
                    # `[]`/`[*]`) o quando e' un nodo isolato. Senza
                    # questo branch il nodo veniva silenziosamente
                    # saltato: `node_results[node_id]` non era mai
                    # settato → `_find_final_result` cadeva su un
                    # fallback upstream → task ritornava success=True
                    # mascherando errori HTTP del generator (regression
                    # di copertura del fix RB.#15 group_error).
                    #
                    # `run_single_enricher` fa il dispatch su
                    # api_type=generator vs enricher e propaga
                    # ValueError sollevato da `_call_generator_endpoint`
                    # su HTTP error. Catturato dal boundary qui sotto
                    # come {"success": False, "user_error": True, ...}.
                    input_edge = next(
                        (e for e in edges if e["target"] == node_id), None
                    )
                    input_df = (
                        node_results.get(input_edge["source"])
                        if input_edge else None
                    )
                    result = run_single_enricher(
                        svc, node_data, api_connections, input_df
                    )
                    # Propaga dict di errore senza convertirlo in success.
                    if isinstance(result, dict) and not result.get("success", True):
                        return result
                    node_results[node_id] = result

            else:
                # ── Gruppo parallelo di api_enrich ──
                # Trova l'input_df comune: output del predecessore del
                # primo nodo del gruppo
                first_node_id = step_node_ids[0]
                input_edge = next(
                    (e for e in edges if e["target"] == first_node_id), None
                )
                input_df = (
                    node_results.get(input_edge["source"])
                    if input_edge else None
                )

                if input_df is None:
                    # Fallback: prova a trovare l'ultimo risultato disponibile
                    for prev_id in reversed(execution_order):
                        if prev_id in node_results and isinstance(
                            node_results[prev_id], pd.DataFrame
                        ):
                            input_df = node_results[prev_id]
                            break

                if input_df is None:
                    return {
                        "success": False,
                        "error": (
                            f"Input non trovato per gruppo parallelo "
                            f"{step_node_ids}"
                        ),
                    }

                # Filtra nodi file-processor (rest_file) dal gruppo:
                # non sono parallelizzabili col meccanismo degli enricher.
                parallel_ids = []
                for nid in step_node_ids:
                    nd = node_map.get(nid)
                    api_id = _nd(nd, "api_id")
                    api_cfg = api_connections.get(api_id, {})
                    ctype = api_cfg.get("connection_type")
                    if ctype == "rest_file":
                        # Esegui in sequenza prima del gruppo parallelo
                        if should_execute_node(nd, input_df):
                            result = svc._execute_rest_file(
                                nd, api_cfg, nodes, edges, nid, project_id,
                                node_results=node_results,
                            )
                            if isinstance(result, dict) and not result.get(
                                "success", True
                            ):
                                return result
                            node_results[nid] = result
                        else:
                            node_results[nid] = input_df
                    else:
                        parallel_ids.append(nid)

                if parallel_ids:
                    logger.info(
                        f"Esecuzione parallela: {len(parallel_ids)} nodi enricher"
                    )

                    def _execute_single(node_data, api_conns, in_df):
                        return run_single_enricher(svc, node_data, api_conns, in_df)

                    group_results, group_error = execute_parallel_enrichers(
                        node_ids=parallel_ids,
                        input_df=input_df,
                        node_map=node_map,
                        api_connections=api_connections,
                        execute_single_fn=_execute_single,
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
                            "error": (
                                f"Errore chiamata API upstream: {group_error}"
                            ),
                            "error_node_id": (
                                parallel_ids[0] if parallel_ids else None
                            ),
                            "error_node_type": "api_enrich",
                        }

                if run_id:
                    for nid in step_node_ids:
                        svc._write_intermediate_snapshot(
                            run_id, nid, "api_enrich",
                            node_results.get(nid),
                            mode_override=intermediate_mode_override,
                        )

        result_node = next(
            (n for n in nodes if n.get("type") == "result"), None
        )
        output_format = (
            _nd(result_node, "format", format) or format
            if result_node else format
        )
        output_name = (
            _nd(result_node, "fileName", _nd(result_node, "name", "output"))
            if result_node else "output"
        )

        final_result = svc._find_final_result(
            node_results, result_node, execution_order
        )
        if final_result is None:
            return {"success": False, "error": "Nessun risultato generato"}

        if isinstance(final_result, dict) and final_result.get(
            "is_file_result"
        ):
            return svc._build_file_result_response(final_result, project_id)

        if not isinstance(final_result, pd.DataFrame):
            return {"success": False, "error": "Nessun DataFrame generato"}

        return svc._save_and_build_dataframe_response(
            final_result, output_name, output_format, project_id
        )

    except ValueError as e:
        # boundary: i ValueError sollevati dentro api_connector portano
        # messaggi gia' user-safe (es. "Accesso negato:", "URL bloccato:",
        # "Contesto di sicurezza ..."). Li propaghiamo come user_error.
        logger.error(
            f"[GESTITO] Errore esecuzione enrichment {project_id}: {e}",
            exc_info=True,
        )
        return {"success": False, "user_error": True, "error": str(e)}
    except Exception as e:
        # boundary: messaggio safe al caller, dettaglio tecnico solo nei log
        logger.error(
            f"[GESTITO] Errore esecuzione enrichment {project_id}: {e}",
            exc_info=True,
        )
        return {
            "success": False,
            "user_error": False,
            "error": "Errore interno durante l'arricchimento",
        }
