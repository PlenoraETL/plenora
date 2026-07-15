"""Input ordering helpers for Manipola transform execution."""

import re
from collections.abc import Mapping
from typing import Any, List, Optional, Sequence, Tuple

from app.shared.execution.canvas_graph import CanvasGraph
from app.core.projects.canvas_models import Edge


def get_target_handle(edge: Edge) -> Optional[str]:
    """Return the React Flow target handle from the edge field or compat id."""
    handle = getattr(edge, "targetHandle", None)
    if isinstance(handle, str) and handle:
        return handle

    edge_id = getattr(edge, "id", "") or ""
    if not isinstance(edge_id, str):
        edge_id = ""
    match = re.search(r"(input-\d+)$", edge_id)
    if match:
        return match.group(1)
    return None


def _handle_sort_value(handle: str) -> Tuple[int, object]:
    """Sort React Flow ``input-N`` handles numerically, then named handles."""
    match = re.fullmatch(r"input-(\d+)", handle)
    if match:
        return (0, int(match.group(1)))
    return (1, handle)


def _source_position_y(source: Any) -> object | None:
    if source is None:
        return None
    if isinstance(source, Mapping):
        position = source.get("position") or {}
    else:
        position = getattr(source, "position", None) or {}
    if isinstance(position, Mapping):
        return position.get("y", 0)
    return getattr(position, "y", 0)


def incoming_edge_sort_key_for_nodes(
    edge: Edge,
    nodes_by_id: Mapping[str, Any],
) -> Tuple[int, object]:
    """Schema-inference variant using its existing node-id mapping."""
    handle = get_target_handle(edge)
    if handle:
        return (0, _handle_sort_value(handle))

    position_y = _source_position_y(nodes_by_id.get(edge.source))
    if position_y is not None:
        return (1, position_y)
    return (2, edge.source)


def incoming_edge_sort_key(edge: Edge, graph: CanvasGraph) -> Tuple[int, object]:
    """Sort key that keeps left/right transform inputs deterministic."""
    handle = get_target_handle(edge)
    if handle:
        return (0, _handle_sort_value(handle))

    raw_source = graph.raw(edge.source)
    position_y = _source_position_y(raw_source)
    if position_y is not None:
        return (1, position_y)
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
