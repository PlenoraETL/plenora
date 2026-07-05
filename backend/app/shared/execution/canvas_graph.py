"""
CanvasGraph - parsed intake object for canvas node/edge graphs.

"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, List, Optional

from app.shared.contracts.payloads import JsonObject, JsonObjectById, JsonRows
from app.shared.execution.canvas_node import CanvasNode


__all__ = ["CanvasGraph"]


def _plain_dict(value: Any) -> Optional[JsonObject]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        if isinstance(dumped, dict):
            return dumped
    return None


class CanvasGraph:
    """Parsed intake object for a canvas node/edge graph.

    Built once at the entry boundary of an execution loop. Exposes:

    - ``raw(node_id)`` - raw dict for compatibility callers (e.g.
      ``execute_parallel_enrichers``, ``_find_final_result``);
    - ``get(node_id)`` - typed ``CanvasNode`` for field access;
    - ``result_node`` - first node whose ``type == "result"``;
    - ``raw_map`` - the full raw dict-keyed map, for callers
      that need to pass the entire map (e.g. ``execute_parallel_enrichers``);
    - ``edges`` - the original edge list.
    """

    __slots__ = ("_raw_nodes", "_raw_map", "_typed_map", "_edges")

    def __init__(
        self,
        raw_nodes: JsonRows,
        raw_map: JsonObjectById,
        typed_map: Mapping[str, CanvasNode],
        edges: JsonRows,
    ) -> None:
        self._raw_nodes = raw_nodes
        self._raw_map = raw_map
        self._typed_map = typed_map
        self._edges = edges

    @classmethod
    def from_canvas(cls, canvas: Any) -> "CanvasGraph":
        """Build a CanvasGraph from a full canvas object or mapping.

        This is the preferred intake boundary for orchestrators. It accepts
        plain-dict canvas shapes and Pydantic-like canvas objects
        with ``nodes`` / ``edges`` attributes. Node normalization still
        delegates to ``from_nodes_edges`` so accepted node shapes stay aligned.
        """
        if isinstance(canvas, Mapping):
            nodes = canvas.get("nodes", [])
            edges = canvas.get("edges", [])
        elif hasattr(canvas, "nodes") or hasattr(canvas, "edges"):
            nodes = getattr(canvas, "nodes", [])
            edges = getattr(canvas, "edges", [])
        else:
            raise TypeError(
                f"CanvasGraph expects a canvas mapping or object, "
                f"got {type(canvas).__name__}"
            )
        return cls.from_nodes_edges(nodes or [], edges or [])

    @classmethod
    def from_nodes_edges(
        cls,
        nodes: Sequence[Any],
        edges: Sequence[Any],
    ) -> "CanvasGraph":
        """Build a CanvasGraph from canvas ``nodes`` and ``edges`` lists.

        Accepts both plain dicts (Arricchisci canvas shape) and Pydantic
        model instances (Manipola CanvasState shape). Pydantic values are
        converted once via ``model_dump`` so the rest of the graph is
        always plain-dict based. Nodes that are neither are silently skipped.
        """
        raw_nodes: JsonRows = []
        raw_map: JsonObjectById = {}
        typed_map = {}
        for n in nodes:
            raw_dict = _plain_dict(n)
            if raw_dict is None:
                continue
            nid = raw_dict.get("id")
            if nid is None:
                continue
            raw_nodes.append(raw_dict)
            raw_map[nid] = raw_dict
            typed_map[nid] = CanvasNode(raw_dict)
        raw_edges = [
            raw_edge
            for edge in edges or []
            if (raw_edge := _plain_dict(edge)) is not None
        ]
        return cls(raw_nodes, raw_map, typed_map, raw_edges)

    # lookups

    def raw(self, node_id: str) -> Optional[JsonObject]:
        """Return the raw node dict for *node_id*, or None."""
        return self._raw_map.get(node_id)

    def get(self, node_id: str) -> Optional[CanvasNode]:
        """Return the typed ``CanvasNode`` for *node_id*, or None."""
        return self._typed_map.get(node_id)

    def result_node(self) -> Optional[CanvasNode]:
        """Return the first node whose ``type == 'result'``, or None."""
        for cn in self._typed_map.values():
            if cn.type == "result":
                return cn
        return None

    # bulk access

    @property
    def nodes(self) -> List[CanvasNode]:
        """All nodes as typed CanvasNode objects, in intake order."""
        return list(self._typed_map.values())

    @property
    def raw_nodes(self) -> JsonRows:
        """All accepted raw node dicts, in intake order."""
        return list(self._raw_nodes)

    @property
    def raw_map(self) -> JsonObjectById:
        """Raw dict map ``{node_id: raw_dict}`` for compatibility callers."""
        return self._raw_map

    @property
    def edges(self) -> JsonRows:
        """The original edge list."""
        return self._edges
