"""
Canvas-based enrichment orchestrator.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, MutableMapping, Optional, TYPE_CHECKING

from app.core.dataframes.values import is_dataframe
from app.core.arricchisci.execution_context import (
    ArricchisciExecutionContext,
    build_execution_context,
)
from app.core.arricchisci.parallel import (
    compute_parallel_groups,
    execute_parallel_enrichers,
    should_execute_node,
)
from app.core.arricchisci.enrichment_service import build_enrichment_executor
from app.core.arricchisci.types import JsonObject, JsonObjectById, JsonRows
from app.shared.execution.canvas_graph import CanvasGraph
from app.shared.execution.canvas_node import CanvasNode
from app.shared.execution.graph import kahn_topological_order
from app.utils.logger import get_logger


if TYPE_CHECKING:
    from app.core.arricchisci.api_connector import ApiConnectionService


logger = get_logger(__name__)


__all__ = [
    "execute_canvas_enrichment",
    "run_enrichment_canvas",
    "CanvasRunError",
    "CanvasRunHooks",
    "init_execution_context",
    "get_execution_order",
    "run_single_enricher",
    "is_file_source_for_file_processor",
    "ArricchisciExecutionContext",
    "build_execution_context",
]


class CanvasRunError(Exception):
    """Errore bloccante durante l'esecuzione del loop canvas enrichment.

    Porta il `payload` dict gia' formato per il caller (shape
    `{"success": False, ...}`). Sostituisce i return-dict mid-loop con
    un'unica via d'uscita, cosi' il loop vive in `run_enrichment_canvas`
    e i caller (canonical / task) decidono come surfacearlo.
    """

    def __init__(self, payload: JsonObject) -> None:
        super().__init__(str(payload.get("error", "canvas run error")))
        self.payload = payload


@dataclass
class CanvasRunHooks:
    """Hook di lifecycle opzionali per `run_enrichment_canvas`.

    Default tutti `None` → no-op (path canonico/preview). Il task standalone
    li fornisce per progress su Redis e snapshot intermedi. Niente hook deve
    frugare negli interni del loop: ricevono solo identita' del nodo e il
    risultato prodotto.
    """

    before_node: Optional[Callable[[str, str], None]] = None
    after_node: Optional[Callable[[str, str, Any], None]] = None


# Pure / orchestration helpers


def get_execution_order(
    nodes: JsonRows,
    edges: JsonRows,
) -> List[str]:
    """Calcola ordine topologico (algoritmo di Kahn).

    Pure function: non legge state del servizio. Estratta da
    `ApiConnectionService._get_execution_order`. Behavior preservato
    byte-per-byte.

    Kahn implementation moved to the shared leaf
    ``app.shared.execution.graph.kahn_topological_order``. This wrapper
    is the canvas-arricchisci adapter: it pulls ``id`` /
    ``source`` / ``target`` from the canvas dict shape and
    forwards to the pure utility with ``on_cycle='raise'``. Anche se il
    validator del canvas blocca gia' i cicli, il runtime resta fail-closed
    contro dati legacy/corrotti o chiamanti che saltano il gate.
    """
    node_ids = [n["id"] for n in nodes]
    edge_pairs = [
        (edge.get("source"), edge.get("target")) for edge in edges
    ]
    return kahn_topological_order(
        node_ids, edge_pairs, on_cycle="raise",
    )


def is_file_source_for_file_processor(
    node_id: str,
    edges: JsonRows,
    node_map: JsonObjectById,
    api_connections: JsonObjectById,
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

    Contratto attuale:
      Il `svc` parametro non viene mutato; `init_execution_context`
      delega a `build_execution_context` e ritorna il typed ctx. La
      firma con `svc` e' mantenuta come API esplicita dell'orchestratore
      per i caller che hanno gia' il servizio in scope.

    Behavior post-011:
    - Pure delegation: nessun side effect su `svc`. Il typed
      `ctx` e' l'unico veicolo del tenant binding per la chain
      di mixin.
    - Fail-closed identico: errori `psycopg2.Error` propagati
      come `ExecutionContextError` dal builder (chain
      preservata via `from e`).
    Returns:
        Il `ArricchisciExecutionContext` costruito. Ogni
        esecuzione produce un context fresco immutabile.
    """
    return build_execution_context(project_id, preview_limit)


def _merge_node_data(node: Any) -> JsonObject:
    """Merge dei campi root del nodo (esclusi i campi identita') + node['data'].

    Mirror del merge usato da `execute_enrichment_task._run_single_node`: i
    campi del nodo canvas hanno priorita' sui default della connection.
    """
    if isinstance(node, dict):
        flat = {
            k: v
            for k, v in node.items()
            if k not in ("id", "type", "position", "data", "config")
        }
        return {**flat, **(node.get("data") or {})}
    return dict(getattr(node, "data", None) or {})


def run_single_enricher(
    svc: "ApiConnectionService",
    node_data: JsonObject,
    api_conns: JsonObjectById,
    in_df: Any,
) -> Any:
    """Esegue un singolo nodo enricher con il motore avanzato condiviso.

    Dispatch su `api_type`: generator (1 call → df intero) vs enricher
    (`EnrichmentExecutor`: parallel/dedup/retry/cache, lo stesso engine del
    task standalone). Usato sia direttamente che come callback da
    `execute_parallel_enrichers`. Solleva `ValueError` se l'api_id non e'
    nelle connections decifrate.
    """
    _ncn = CanvasNode(node_data)
    api_id = _ncn.get_field("api_id")
    api_config = api_conns.get(api_id)
    if not api_config:
        raise ValueError(f"API '{api_id}' non trovata")
    if api_config.get("api_type", "enricher") == "generator":
        return svc._execute_api_generator(api_config, in_df)
    if in_df is None:
        # Enricher senza input (nessun parametro da colonna): chiamata singola.
        return svc._execute_api_generator(api_config, None)
    executor = build_enrichment_executor(_merge_node_data(node_data), api_config)
    result_df, _enrichment_result = executor.execute(in_df)
    return result_df


# Shared canvas run loop


def run_enrichment_canvas(
    svc: "ApiConnectionService",
    project_id: str,
    *,
    graph: CanvasGraph,
    ctx: ArricchisciExecutionContext,
    api_connections: JsonObjectById,
    execution_order: List[str],
    groups,
    force_recalculate: bool = False,
    enrich_fn: Optional[Callable[[JsonObject, JsonObjectById, Any], Any]] = None,
    hooks: Optional[CanvasRunHooks] = None,
) -> JsonObject:
    """Esegue il loop canvas (gruppi single/parallel) e ritorna `node_results`.

    Scheletro condiviso fra il path canonico e il task standalone.

    - `enrich_fn(node_data, api_conns, in_df) -> df` esegue un singolo nodo
      api_enrich. Default `run_single_enricher` (motore avanzato); il task ne
      passa una versione con stats/soglie/error-report.
    - `hooks` aggiunge progress e snapshot per-nodo (default no-op). Lo snapshot
      intermedio e' interamente delegato a `hooks.after_node` (il canonico passa
      `_write_intermediate_snapshot`, il task `record_checkpoint_snapshot`).
    - Errori bloccanti → `CanvasRunError(payload)`; il finalize resta nei caller.
    """
    hooks = hooks or CanvasRunHooks()

    def _enrich(node_data, api_conns, in_df):
        if enrich_fn is not None:
            return enrich_fn(node_data, api_conns, in_df)
        return run_single_enricher(svc, node_data, api_conns, in_df)

    edges = graph.edges
    nodes = graph.raw_nodes
    node_results: JsonObject = {}

    # #37 — reference-counting per liberare la RAM dei nodi intermedi appena
    # TUTTI i loro consumatori topologici hanno girato: il picco scende da
    # Sum(tutti i DataFrame della run) a Sum(quelli ancora "vivi"). I nodi
    # terminali (out-degree 0, es. `result`) non vengono mai evictati perche'
    # nessun consumatore ne decrementa il contatore → restano per il caller
    # (`_find_final_result`).
    inputs_of: MutableMapping[str, List[str]] = {}
    consumers_remaining: MutableMapping[str, int] = {}
    for _e in edges:
        _s, _t = _e.get("source"), _e.get("target")
        if _s is None or _t is None:
            continue
        inputs_of.setdefault(_t, []).append(_s)
        consumers_remaining[_s] = consumers_remaining.get(_s, 0) + 1

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
            if hooks.before_node is not None:
                hooks.before_node(node_id, node_type)

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
                # `result` propaga l'output del predecessore (passthrough nel
                # DAG). Il publish ArcGIS — anche IMDF — e' un nodo carica_arcgis
                # downstream, eseguito in-pipeline come ogni altro sink.
                input_edge = next(
                    (e for e in edges if e["target"] == node_id), None
                )
                if input_edge:
                    input_df = node_results.get(input_edge["source"])
                    if input_df is not None:
                        node_results[node_id] = input_df

            elif node_type == "api_enrich":
                # api_enrich emesso come step "single" quando
                # cardinality-changing o isolato. rest_file ed
                # execute_if sono gestiti qui (serve il contesto loop:
                # nodes/edges/node_results), in modo uniforme al branch
                # parallelo; `enrich_fn` resta puro enricher/generator.
                input_edge = next(
                    (e for e in edges if e["target"] == node_id), None
                )
                input_df = (
                    node_results.get(input_edge["source"])
                    if input_edge else None
                )
                api_id = graph.get(node_id).get_field("api_id")
                api_cfg = api_connections.get(api_id, {})
                if api_cfg.get("connection_type") == "rest_file":
                    if should_execute_node(node_data, input_df):
                        result = svc._execute_rest_file(
                            node_data, api_cfg, nodes, edges, node_id,
                            project_id, node_results=node_results, ctx=ctx,
                        )
                        if isinstance(result, dict) and not result.get(
                            "success", True
                        ):
                            raise CanvasRunError(result)
                        node_results[node_id] = result
                    else:
                        node_results[node_id] = input_df
                elif not should_execute_node(node_data, input_df):
                    node_results[node_id] = input_df
                else:
                    result = _enrich(node_data, api_connections, input_df)
                    if isinstance(result, dict) and not result.get(
                        "success", True
                    ):
                        raise CanvasRunError(result)
                    node_results[node_id] = result

            if hooks.after_node is not None:
                hooks.after_node(node_id, node_type, node_results.get(node_id))

        else:
            # ── Gruppo parallelo di api_enrich ──
            first_node_id = step_node_ids[0]
            input_edge = next(
                (e for e in edges if e["target"] == first_node_id), None
            )
            input_df = (
                node_results.get(input_edge["source"])
                if input_edge else None
            )

            if input_df is None:
                for prev_id in reversed(execution_order):
                    if prev_id in node_results and is_dataframe(node_results[prev_id]):
                        input_df = node_results[prev_id]
                        break

            if input_df is None:
                raise CanvasRunError({
                    "success": False,
                    "error": (
                        f"Input non trovato per gruppo parallelo {step_node_ids}"
                    ),
                })

            # Filtra nodi file-processor (rest_file): non parallelizzabili
            parallel_ids = []
            for nid in step_node_ids:
                nd = graph.raw(nid)
                api_id = graph.get(nid).get_field("api_id") if nd else None
                api_cfg = api_connections.get(api_id, {})
                if api_cfg.get("connection_type") == "rest_file":
                    if should_execute_node(nd, input_df):
                        result = svc._execute_rest_file(
                            nd, api_cfg, nodes, edges, nid, project_id,
                            node_results=node_results, ctx=ctx,
                        )
                        if isinstance(result, dict) and not result.get(
                            "success", True
                        ):
                            raise CanvasRunError(result)
                        node_results[nid] = result
                    else:
                        node_results[nid] = input_df
                else:
                    parallel_ids.append(nid)

            if parallel_ids:
                logger.info(
                    f"Esecuzione parallela: {len(parallel_ids)} nodi enricher"
                )

                group_results, group_error = execute_parallel_enrichers(
                    node_ids=parallel_ids,
                    input_df=input_df,
                    node_map=graph.raw_map,
                    api_connections=api_connections,
                    execute_single_fn=_enrich,
                )
                node_results.update(group_results)

                if group_error:
                    logger.error(
                        f"[GESTITO] Errore api_enrich in gruppo "
                        f"{parallel_ids}: {group_error}",
                        op="execute", project_id=project_id,
                    )
                    raise CanvasRunError({
                        "success": False,
                        "user_error": True,
                        "error": (
                            f"Errore chiamata API upstream: {group_error}"
                        ),
                        "error_node_id": (
                            parallel_ids[0] if parallel_ids else None
                        ),
                        "error_node_type": "api_enrich",
                    })

            if hooks.after_node is not None:
                for nid in step_node_ids:
                    hooks.after_node(nid, "api_enrich", node_results.get(nid))

        # #37 — eviction: i nodi appena prodotti hanno consumato i loro input;
        # decrementa il contatore di ciascun input e libera dalla RAM quelli i
        # cui consumatori sono tutti esauriti (solo DataFrame: i marker sono
        # piccoli e il caller potrebbe ancora leggerli).
        for _produced in step_node_ids:
            for _src in inputs_of.get(_produced, ()):
                consumers_remaining[_src] = consumers_remaining.get(_src, 0) - 1
                if consumers_remaining[_src] <= 0:
                    _val = node_results.get(_src)
                    if _val is not None and is_dataframe(_val):
                        del node_results[_src]
                        logger.debug(
                            f"RAM evict: nodo intermedio '{_src}' liberato "
                            f"(tutti i consumatori hanno girato)"
                        )

    return node_results


# Top-level orchestrator


def execute_canvas_enrichment(
    svc: "ApiConnectionService",
    project_id: str,
    canvas: JsonObject,
    format: str = "csv",
    force_recalculate: bool = False,
    preview_limit: Optional[int] = None,
    run_id: Optional[str] = None,
    intermediate_mode_override: Optional[str] = None,
    finalizer: Optional[Callable[[Any, str, str, str], JsonObject]] = None,
    execution_context: Optional[ArricchisciExecutionContext] = None,
) -> JsonObject:
    """Orchestra l'esecuzione del canvas arricchisci.

    `finalizer` (Step C unificazione preview): se fornito, viene chiamato
    al posto di `_save_and_build_dataframe_response` sul DataFrame finale,
    con la stessa firma `(final_df, output_name, output_format, project_id)`.
    Default `None` → comportamento invariato (salva il blob). La preview lo
    usa per costruire le righe in-memory senza persistere nulla, riusando
    questo loop canonico invece di reimplementarlo.

    Il modulo contiene l'algoritmo canonico di esecuzione canvas.
    `ApiConnectionService.execute_enrichment` resta la facade
    pubblica per i caller runtime service-oriented e delega qui.

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

        ctx = execution_context or init_execution_context(
            svc,
            project_id,
            preview_limit,
        )
        if ctx.project_id != project_id:
            raise ValueError("execution context project_id mismatch")

        # Carica tutte le API connections con credenziali decifrate (1 query
        api_connections = svc.list_api_connections_decrypted()

        # Ordine di esecuzione topologico
        execution_order = get_execution_order(nodes, edges)
        logger.info(f"Ordine esecuzione: {execution_order}")

        # ── Raggruppamento nodi per esecuzione parallela ──
        groups = compute_parallel_groups(
            execution_order, nodes, edges,
            api_connections=api_connections,
        )
        logger.info(f"Gruppi di esecuzione: {[(t, len(ids)) for t, ids in groups]}")

        def _canonical_after_node(node_id_, node_type_, result_):
            # Snapshot intermedio per gli api_enrich (era inline nel loop).
            if run_id and node_type_ == "api_enrich":
                svc._write_intermediate_snapshot(
                    run_id, node_id_, "api_enrich", result_,
                    mode_override=intermediate_mode_override,
                )

        node_results = run_enrichment_canvas(
            svc,
            project_id,
            graph=graph,
            ctx=ctx,
            api_connections=api_connections,
            execution_order=execution_order,
            groups=groups,
            force_recalculate=force_recalculate,
            hooks=CanvasRunHooks(after_node=_canonical_after_node),
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

        if not is_dataframe(final_result):
            return {"success": False, "error": "Nessun DataFrame generato"}

        if finalizer is not None:
            return finalizer(final_result, output_name, output_format, project_id)
        return svc._save_and_build_dataframe_response(
            final_result, output_name, output_format, project_id
        )

    except CanvasRunError as e:
        return e.payload

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
