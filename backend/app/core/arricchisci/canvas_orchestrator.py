"""
Canvas-based enrichment orchestrator.

"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

import pandas as pd

import app.db.db_service as db_svc  # noqa: F401 (test-monkeypatch surface preserved
from app.core.arricchisci.execution_context import (
    ArricchisciExecutionContext,
    build_execution_context,
)
from app.shared.execution.canvas_graph import CanvasGraph
from app.shared.execution.canvas_node import CanvasNode
from app.shared.execution.graph import kahn_topological_order
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
    "ArricchisciExecutionContext",
    "build_execution_context",
]


# Pure / orchestration helpers


def get_execution_order(
    nodes: List[Dict],
    edges: List[Dict],
) -> List[str]:
    """Calcola ordine topologico (algoritmo di Kahn).

    Pure function: non legge state del servizio. Estratta da
    `ApiConnectionService._get_execution_order`. Behavior preservato
    byte-per-byte.

    Kahn implementation moved to the shared leaf
    ``app.shared.execution.graph.kahn_topological_order``. This wrapper
    is the canvas-arricchisci adapter: it pulls ``id`` /
    ``source`` / ``target`` from the canvas dict shape and
    forwards to the pure utility with ``on_cycle='truncate'``
    (legacy shape: cycle nodes silently omitted, no exception).
    Behavior identical byte-for-byte to the pre-graph-engine
    implementation; locked by
    `tests2/arricchisci/unit/test_canvas_orchestrator_contract.py`.
    """
    node_ids = [n["id"] for n in nodes]
    edge_pairs = [
        (edge.get("source"), edge.get("target")) for edge in edges
    ]
    return kahn_topological_order(
        node_ids, edge_pairs, on_cycle="truncate",
    )


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
        if not target_node:
            continue
        _tcn = CanvasNode(target_node)
        if _tcn.type != "api_enrich":
            continue
        target_api_id = _tcn.get_field("api_id")
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

    Storia:

      l'implementazione era spezzata in due step espliciti:
      `build_execution_context(...)` (costruzione del typed
      immutabile) + `apply_to_legacy_state(svc, ctx)` (mirror
      KEEP_COMPAT su `svc._current_group_id` /
      `svc._preview_limit` per i mixin che ancora leggevano dal
      singleton).

      026/030B + ORCHESTRATION-ENGINE-009/010**: tutti i mixin
      reader site sono stati migrati a CTX-STRICT
      (`_extract_file_source_group_id`,
      `_extract_project_source_group_id`,
      `_group_id_from_ctx`, `_preview_limit_from_ctx`). Nessuna
      funzione di produzione legge piu' `svc._current_group_id`
      / `svc._preview_limit` (verificato da AST scan: zero read
      sites in `app/core/arricchisci/`).

    - ** (this cut)**: il
      mirror `apply_to_legacy_state(svc, ctx)` in questa
      funzione era diventato dead-write: scriveva attributi che
      nessuno leggeva. Removed. Il `svc` parametro non e' piu'
      mutato; `init_execution_context` e' ora una thin wrapper
      che delega a `build_execution_context` + ritorna il typed
      ctx. La firma con `svc` resta per KEEP_COMPAT con il
      wrapper `ApiConnectionService._init_execution_context` che
      lo passa esplicitamente.

    Behavior post-011:
    - Pure delegation: nessun side effect su `svc`. Il typed
      `ctx` e' l'unico veicolo del tenant binding per la chain
      di mixin.
    - Fail-closed identico: errori `psycopg2.Error` propagati
      come `ExecutionContextError` dal builder (chain
      preservata via `from e`).
    - Stale singleton state: se `svc._current_group_id` era
      pre-set da un run precedente, NON viene piu' azzerato qui.
      E' irrilevante perche' i mixin readers sono ctx-strict; il
      typed ctx fresco vince sempre. Il regression test
      `test_init_execution_context_does_not_write_singleton_state`
      lock-a questo invariant.

    Returns:
        Il `ArricchisciExecutionContext` costruito. Ogni
        esecuzione produce un context fresco immutabile.
    """
    return build_execution_context(project_id, preview_limit)


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
    _ncn = CanvasNode(node_data)
    api_id = _ncn.get_field("api_id")
    api_config = api_conns.get(api_id)
    if not api_config:
        raise ValueError(f"API '{api_id}' non trovata")
    if api_config.get("api_type", "enricher") == "generator":
        return svc._execute_api_generator(api_config, in_df)
    param_mapping = _ncn.get_field("param_mapping", {})
    return svc._execute_api_enricher(in_df, api_config, param_mapping)


# Top-level orchestrator


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
    `ApiConnectionService.execute_enrichment` (BE-portable-
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
        graph = CanvasGraph.from_canvas(canvas)
        nodes = graph.raw_nodes
        edges = graph.edges

        if not nodes:
            return {"success": False, "error": "Nessun nodo nel canvas"}

        ctx = init_execution_context(svc, project_id, preview_limit)

        # Carica tutte le API connections con credenziali decifrate (1 query
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

        node_results: Dict[str, Any] = {}

        for step_type, step_node_ids in groups:
            if step_type == "single":
                # ── Nodo singolo (sorgente, result, o enricher isolato) ──
                node_id = step_node_ids[0]
                node = graph.raw(node_id)
                if not node:
                    continue
                node_type = graph.get(node_id).type
                node_data = node
                logger.debug(f"Esecuzione nodo: {node_id} ({node_type})")

                if node_type == "project_source":
                    node_results[node_id] = svc._load_source_project(
                        node_data, force_recalculate, ctx=ctx,
                    )

                elif node_type == "file_source":
                    if is_file_source_for_file_processor(
                        node_id, edges, graph.raw_map, api_connections
                    ):
                        node_results[node_id] = {
                            "_passthrough_file_source": True,
                            "file_source_id": graph.get(node_id).get_field("file_source_id"),
                        }
                    else:
                        node_results[node_id] = svc._load_file_source(
                            node_data, ctx=ctx,
                        )

                elif node_type == "staticValues":
                    node_results[node_id] = svc._load_static_values(
                        node_data, ctx=ctx,
                    )

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
                    # di copertura del fix RB.#15 group_error
                    # `run_single_enricher` fa il dispatch su
                    # api_type=generator vs enricher e propaga
                    # ValueError sollevato da `_call_generator_endpoint`
                    # su HTTP error. Catturato dal boundary qui sotto
                    # come {"success": False, "user_error": True, ...}
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
                    # Propaga dict di errore senza convertirlo in success
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

                # Filtra nodi file-processor (rest_file) dal gruppo
                # non sono parallelizzabili col meccanismo degli enricher
                parallel_ids = []
                for nid in step_node_ids:
                    nd = graph.raw(nid)
                    api_id = graph.get(nid).get_field("api_id") if nd else None
                    api_cfg = api_connections.get(api_id, {})
                    ctype = api_cfg.get("connection_type")
                    if ctype == "rest_file":
                        # Esegui in sequenza prima del gruppo parallelo
                        if should_execute_node(nd, input_df):
                            # typed ctx threaded through il rest-file
                            # pipeline (group_id letto da `ctx` invece
                            # di singleton
                            result = svc._execute_rest_file(
                                nd, api_cfg, nodes, edges, nid, project_id,
                                node_results=node_results, ctx=ctx,
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
                        node_map=graph.raw_map,
                        api_connections=api_connections,
                        execute_single_fn=_execute_single,
                    )
                    node_results.update(group_results)

                    # Se il gruppo ha prodotto un errore, la pipeline
                    # fallisce: inghiottire il group_error portava a
                    # task "success" con output passthrough del nodo
                    # upstream (es. solo la colonna seed di staticValues
                    # mascherando errori upstream come HTTP 422 sul
                    # generator API. Non esiste un caso legittimo in cui
                    # un'eccezione dentro run_one debba essere ignorata
                    # le skip via execute_if ritornano (None, None
                    # non (None, errore
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

        _result_cn = graph.result_node()
        if _result_cn:
            output_format = _result_cn.get_field("format", format) or format
            _fn = _result_cn.get_field("fileName")
            output_name = _fn if _fn is not None else _result_cn.get_field("name", "output")
        else:
            output_format = format
            output_name = "output"

        result_node_raw = graph.raw(_result_cn.id) if _result_cn else None
        final_result = svc._find_final_result(
            node_results, result_node_raw, execution_order
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
        # messaggi gia' user-safe (es. "Accesso negato:", "URL bloccato:"
        # "Contesto di sicurezza ..."). Li propaghiamo come user_error
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
