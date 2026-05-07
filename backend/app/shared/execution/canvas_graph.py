"""CanvasGraph - parsed intake object for canvas node/edge graphs.

BE-GO-READY-ARCH-058-W3-CANVASGRAPH-INTAKE-ARRICCHISCI.

Consolidates the two parallel local dicts that execution loops
previously built at entry: a raw ``Dict[str, Dict]`` for legacy
callers and a ``Dict[str, CanvasNode]`` for typed field access.

``CanvasGraph.from_nodes_edges(nodes, edges)`` builds both from a
single pass; the graph is then used for all lookups within the same
call frame instead of re-wrapping nodes on every iteration.

Boundary
========
Leaf contract module. Must NOT import:

- ``fastapi`` / ``starlette`` (no HTTP);
- ``app.db.*`` / ``psycopg2`` / ``redis`` (no I/O);
- ``pandas`` / ``geopandas`` / ``polars`` (no DataFrame);
- ``app.core.*`` (consumed by core, not vice-versa).

Go-ready mapping
================
::

    type CanvasGraph struct {
        RawMap  map[string]map[string]any
        Nodes   map[string]CanvasNode
        Edges   []map[string]any
    }
    func (g *CanvasGraph) Raw(nodeID string) map[string]any
    func (g *CanvasGraph) Get(nodeID string) *CanvasNode
    func (g *CanvasGraph) ResultNode() *CanvasNode
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.shared.execution.canvas_node import CanvasNode


__all__ = ["CanvasGraph"]


class CanvasGraph:
    """Parsed intake object for a canvas node/edge graph.

    Built once at the entry boundary of an execution loop.  Exposes:

    - ``raw(node_id)``      - raw dict for legacy callers (e.g.
      ``execute_parallel_enrichers``, ``_find_final_result``);
    - ``get(node_id)``      - typed ``CanvasNode`` for field access;
    - ``result_node()``     - first node whose ``type == "result"``;
    - ``raw_map``           - the full raw dict-keyed map, for callers
      that need to pass the entire map (e.g. ``execute_parallel_enrichers``);
    - ``edges``             - the original edge list.
    """

    __slots__ = ("_raw_map", "_typed_map", "_edges")

    def __init__(
        self,
        raw_map: Dict[str, Dict],
        typed_map: Dict[str, CanvasNode],
        edges: List[Dict],
    ) -> None:
        self._raw_map = raw_map
        self._typed_map = typed_map
        self._edges = edges

    @classmethod
    def from_nodes_edges(
        cls,
        nodes: List,
        edges: List,
    ) -> "CanvasGraph":
        """Build a CanvasGraph from canvas ``nodes`` and ``edges`` lists.

        Accepts both plain dicts (Arricchisci canvas shape) and Pydantic
        model instances (Manipola CanvasState shape). Pydantic nodes are
        converted once via ``model_dump()`` so the rest of the graph is
        always plain-dict based. Nodes that are neither are silently skipped.
        """
        raw_map: Dict[str, Dict] = {}
        typed_map: Dict[str, CanvasNode] = {}
        for n in nodes:
            if isinstance(n, dict):
                raw_dict = n
            elif hasattr(n, "model_dump"):
                # BE-GO-READY-ARCH-059-W2: Pydantic v2 model - convert once
                # at the intake boundary so all downstream lookups are plain
                # dicts (position, data, etc.) without dual isinstance checks.
                raw_dict = n.model_dump()
            else:
                continue
            nid = raw_dict.get("id")
            if nid is None:
                continue
            raw_map[nid] = raw_dict
            typed_map[nid] = CanvasNode(raw_dict)
        return cls(raw_map, typed_map, list(edges) if edges else [])

    # -- lookups ---------------------------------------------------------------

    def raw(self, node_id: str) -> Optional[Dict]:
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

    # -- bulk access -----------------------------------------------------------

    @property
    def raw_map(self) -> Dict[str, Dict]:
        """Raw dict map ``{node_id: raw_dict}`` for legacy callers."""
        return self._raw_map

    @property
    def edges(self) -> List[Dict]:
        """The original edge list."""
        return self._edges
