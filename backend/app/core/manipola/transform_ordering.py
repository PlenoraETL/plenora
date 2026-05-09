"""Input ordering helpers for Manipola transform execution."""

import re
from typing import List, Optional, Sequence, Tuple

from app.shared.execution.canvas_graph import CanvasGraph
from app.core.projects.canvas_models import Edge


def get_target_handle(edge: Edge) -> Optional[str]:
    """Return the React Flow target handle from the edge field or legacy id."""
    handle = getattr(edge, "targetHandle", None)
    if handle:
        return handle

    edge_id = getattr(edge, "id", "") or ""
    match = re.search(r"(input-\d+)$", edge_id)
    if match:
        return match.group(1)
    return None


def incoming_edge_sort_key(edge: Edge, graph: CanvasGraph) -> Tuple[int, object]:
    """Sort key that keeps left/right transform inputs deterministic."""
    handle = get_target_handle(edge)
    if handle:
        return (0, handle)

    raw_source = graph.raw(edge.source)
    if raw_source:
        position = raw_source.get("position") or {}
        if isinstance(position, dict):
            return (1, position.get("y", 0))
    return (2, edge.source)


def sorted_incoming_edges(
    edges: Sequence[Edge],
    target_node_id: str,
    graph: CanvasGraph,
) -> List[Edge]:
    """Return incoming edges for a node in stable transform input order."""
    incoming = [edge for edge in edges if edge.target == target_node_id]
    incoming.sort(key=lambda edge: incoming_edge_sort_key(edge, graph))
    return incoming
