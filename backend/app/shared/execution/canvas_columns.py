"""
canvas_columns.py - Computazione autorevole delle colonne del ResultNode (C9).

Motivazione
"""

from __future__ import annotations

from collections.abc import Callable
from app.shared.execution.output_column_refs import unique_column_names

from app.shared.contracts.payloads import JsonObjectById, JsonRows
from app.utils.logger import get_logger


logger = get_logger(__name__)


def _existing_by_name(result_node: object) -> JsonObjectById:
    """Estrae le personalizzazioni salvate, mantenendo nome e identità."""
    columns = []
    if hasattr(result_node, "columns") and result_node.columns:
        columns = result_node.columns
    elif hasattr(result_node, "data") and result_node.data:
        columns = result_node.data.get("columns", []) or []

    out: JsonObjectById = {}
    for c in columns:
        if isinstance(c, dict):
            name = c.get("name", "")
            if not name:
                continue
            out[name] = {
                "alias": c.get("alias"),
                "visible": c.get("visible", True),
                "id": c.get("id"),
                "source_name": c.get("source_name"),
            }
        elif hasattr(c, "name"):
            name = c.name or ""
            if not name:
                continue
            out[name] = {
                "alias": getattr(c, "alias", None),
                "visible": getattr(c, "visible", True),
                "id": getattr(c, "id", None),
                "source_name": getattr(c, "source_name", None),
            }
    return out


def compute_result_columns(
    canvas: object,
    result_id: str,
    project_id: str,
    project_service: object,
    schema_inference_service_factory: Callable[[object], object],
) -> JsonRows:
    """Computa le colonne autorevoli del result_node via BFS backward.

    Delega la logica per-node-type a `SchemaInferenceService`.

    Args:
        canvas: CanvasState con .nodes e .edges (in-memory, NON caricato
                da DB - utile al PUT dove il canvas e' il body della
                request, non ancora persistito).
        result_id: id del nodo 'result' per cui calcolare le colonne.
        project_id: id progetto corrente (serve a project_source per
                    risolvere riferimenti ad altri progetti).
        project_service: istanza di ProjectService (per SchemaInferenceService).

    Returns:
        Lista di dict {name, type, alias, visible} compatibile con
        `canvas.nodes[result].columns` (Column model di app.core.projects.canvas_models).
        Le customizzazioni utente (alias, visible) presenti nel
        result_node corrente sono preservate per identità.
    """
    service = schema_inference_service_factory(project_service)
    service._schema_cache = {}

    nodes_by_id = {n.id: n for n in canvas.nodes}
    edges_by_target = service._group_edges_by_target(canvas.edges)

    result_node = nodes_by_id.get(result_id)
    if not result_node:
        logger.warning(f"compute_result_columns: result_id={result_id} non trovato nel canvas")
        return []

    try:
        schema = service._infer_node_schema(
            node=result_node,
            nodes_by_id=nodes_by_id,
            edges_by_target=edges_by_target,
            project_id=project_id,
        )
    except Exception as e:
        # Best-effort: se l'inferenza esplode (canvas malformato, node
        # type sconosciuto, ecc.), logga ma non rompiamo la persistenza
        # del canvas. Il chiamante puo' decidere di non sovrascrivere
        # i valori attuali
        logger.warning(
            f"compute_result_columns: inferenza fallita per result_id={result_id}: {e}",
            exc_info=True,
        )
        return []

    # L'identità ha precedenza sul nome storico salvato nel canvas.
    user_overrides = _existing_by_name(result_node)
    valid_ids = unique_column_names([{"id": getattr(col, "id", None), "name": col.name} for col in schema])
    saved_ids = unique_column_names([dict(value, name=name) for name, value in user_overrides.items()])
    overrides_by_id = {identity: user_overrides[name] for identity, name in saved_ids.items()
                       if identity in valid_ids}

    out: JsonRows = []
    for col in schema:
        override = overrides_by_id.get(str(getattr(col, "id", None)), user_overrides.get(col.name, {}))
        item = {
            "name": col.name,
            "type": col.type or "str",
            "alias": override.get("alias"),
            "visible": bool(override.get("visible", True)),
        }
        col_id = getattr(col, "id", None) or override.get("id")
        if col_id:
            item["id"] = col_id
        source_name = getattr(col, "source_name", None) or override.get("source_name")
        if source_name:
            item["source_name"] = source_name
        out.append(item)
    return out


def find_result_node(canvas: object) -> object | None:
    """Helper: localizza il nodo 'result' nel canvas. Return None se
    non esiste (canvas senza terminale, come una tappa intermedia o un
    canvas non-result)."""
    for n in canvas.nodes:
        if n.type == "result":
            return n
    return None
