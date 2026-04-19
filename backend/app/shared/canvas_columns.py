"""
canvas_columns.py — Computazione autorevole delle colonne del ResultNode (C9).

Motivazione
===========
Pre-C9, il flusso di persistenza columns era frammentato:

1. Frontend `canvasSerializer._computeResultColumns` guarda SOLO il parent
   diretto del result e legge `sourceNode.data.columns`. Se il parent e'
   un `api_enrich` senza `data.columns` popolato, ricade su `saved`
   (colonne del run precedente, potenzialmente vuote/stale).

2. Backend PUT `/projects/{id}/canvas` salva il canvas as-is e poi
   fa fallback su `api_enrich.output_mapping` SOLO per
   `columns_metadata` (la view), NON per `canvas.result_node.columns`
   persistita. Quindi al reload la UI mostra il canvas con result
   vuoto.

Questo modulo chiude il bug con una singola funzione autorevole:

    compute_result_columns(canvas, result_id, project_id, project_service)

Che usa `SchemaInferenceService._infer_node_schema` (logica gia' battaglia-
provata per tutti i node type: source, project_source, file_source,
transform, api_enrich, merge/join, result), e preserva le
customizzazioni utente (`alias`, `visible`) dal canvas corrente.

Il chiamante e' responsabile di:
- Mutare `canvas.nodes[i].columns = risultato` per l'i del result
- Persistere il canvas (update_canvas)
- Aggiornare `project.columns_metadata` per compat

Usabile sia dal PUT /canvas che da executor pre-run come single
source of truth.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.utils.logger import get_logger


logger = get_logger(__name__)


def _existing_by_name(result_node) -> Dict[str, Dict[str, Any]]:
    """Estrae le customizzazioni utente (alias, visible) dal
    result_node corrente, indicizzate per nome colonna originale.

    Il match avviene su `name` (NON `alias`) cosi' una colonna rinominata
    dall'utente resta mappata all'originale anche dopo rename upstream."""
    columns = []
    if hasattr(result_node, "columns") and result_node.columns:
        columns = result_node.columns
    elif hasattr(result_node, "data") and result_node.data:
        columns = result_node.data.get("columns", []) or []

    out: Dict[str, Dict[str, Any]] = {}
    for c in columns:
        if isinstance(c, dict):
            name = c.get("name", "")
            if not name:
                continue
            out[name] = {
                "alias": c.get("alias"),
                "visible": c.get("visible", True),
            }
        elif hasattr(c, "name"):
            name = c.name or ""
            if not name:
                continue
            out[name] = {
                "alias": getattr(c, "alias", None),
                "visible": getattr(c, "visible", True),
            }
    return out


def compute_result_columns(
    canvas,
    result_id: str,
    project_id: str,
    project_service,
) -> List[Dict[str, Any]]:
    """Computa le colonne autorevoli del result_node via BFS backward.

    Delega la logica per-node-type a `SchemaInferenceService` (gia'
    presente in app/services/schema_inference.py).

    Args:
        canvas: CanvasState con .nodes e .edges (in-memory, NON caricato
                da DB — utile al PUT dove il canvas e' il body della
                request, non ancora persistito).
        result_id: id del nodo 'result' per cui calcolare le colonne.
        project_id: id progetto corrente (serve a project_source per
                    risolvere riferimenti ad altri progetti).
        project_service: istanza di ProjectService (per SchemaInferenceService).

    Returns:
        Lista di dict {name, type, alias, visible} compatibile con
        `canvas.nodes[result].columns` (Column model di app.shared.models).
        Le customizzazioni utente (alias, visible) presenti nel
        result_node corrente sono preservate per nome.
    """
    # Lazy import per evitare cicli (schema_inference importa project_service
    # per infer_project_source_schema)
    from app.services.schema_inference import SchemaInferenceService

    service = SchemaInferenceService(project_service)
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
        # i valori attuali.
        logger.warning(
            f"compute_result_columns: inferenza fallita per result_id={result_id}: {e}",
            exc_info=True,
        )
        return []

    # Preserva alias + visible dell'utente per nome
    user_overrides = _existing_by_name(result_node)

    out: List[Dict[str, Any]] = []
    for col in schema:
        override = user_overrides.get(col.name, {})
        out.append({
            "name": col.name,
            "type": col.type or "str",
            "alias": override.get("alias"),
            "visible": bool(override.get("visible", True)),
        })
    return out


def find_result_node(canvas):
    """Helper: localizza il nodo 'result' nel canvas. Return None se
    non esiste (canvas senza terminale, come una tappa intermedia o un
    canvas non-result)."""
    for n in canvas.nodes:
        if n.type == "result":
            return n
    return None
