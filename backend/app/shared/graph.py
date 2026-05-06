"""Domain-agnostic graph utilities.

BE-GO-READY-GRAPH-ENGINE-001-SHARED-TOPOLOGICAL-SORT.

Tiny shared module: ONE primitive (Kahn topological sort with
optional cycle detection) plus its typed cycle exception. Pure
stdlib; zero domain imports. The Arricchisci and Manipola
canvas orchestrators each used to ship their own copy of
Kahn's algorithm with slightly different cycle-handling
shapes; this module unifies the implementation behind a
single deterministic API.

Boundary
========
This is a leaf utility. It MUST NOT import:

  - ``fastapi`` / ``starlette`` (no HTTP / Depends);
  - ``app.config`` (no settings dependency);
  - ``app.db.*`` / ``psycopg2`` / ``redis`` (no I/O);
  - ``pandas`` / ``geopandas`` / ``polars`` (no DataFrame work);
  - ``app.core.*`` (the utility is consumed by core modules,
    not the other way around).

Allowed dependencies:

  - stdlib only (``collections``, ``typing``).

Anti-god-engine note
====================
This module exposes ONE primitive: ``kahn_topological_order``.
Adding shortest-path / strongly-connected-components / DAG
diff / etc. would turn it into a graph engine; if that is ever
needed, factor them into separate leaf modules each with
narrow contracts. For now the only real consumer pressure is
canvas orchestration (Kahn-style topological order).

Usage
=====
::

    from app.shared.graph import kahn_topological_order, GraphCycleError

    # Truncate-on-cycle (legacy Arricchisci shape):
    order = kahn_topological_order(
        node_ids=["A", "B", "C"],
        edges=[("A", "B"), ("B", "C")],
        on_cycle="truncate",
    )
    # -> ["A", "B", "C"]

    # Raise-on-cycle (legacy Manipola shape):
    try:
        order = kahn_topological_order(
            node_ids=["A", "B"],
            edges=[("A", "B"), ("B", "A")],
            on_cycle="raise",
        )
    except GraphCycleError as exc:
        cycle_nodes = exc.cycle_nodes  # ["A", "B"]
"""

from __future__ import annotations

from collections import deque
from typing import Iterable, List, Literal, Tuple


__all__ = [
    "GraphCycleError",
    "kahn_topological_order",
]


CycleHandling = Literal["truncate", "raise"]


class GraphCycleError(ValueError):
    """Raised when ``kahn_topological_order(..., on_cycle='raise')``
    detects a cycle in the input graph.

    Subclass of ``ValueError`` so existing code paths that catch
    ``ValueError`` (e.g. Manipola's pre-migration cycle handling)
    continue to work without changes.

    Attributes:
        cycle_nodes: list of node ids that could not be ordered.
            Order matches the input ``node_ids`` order, so the
            error message is deterministic.
    """

    def __init__(self, cycle_nodes: List[str]) -> None:
        self.cycle_nodes = list(cycle_nodes)
        super().__init__(
            f"Ciclo rilevato nel canvas tra i nodi: "
            f"{self.cycle_nodes}. "
            "Rimuovere le connessioni circolari prima di eseguire "
            "la pipeline."
        )


def kahn_topological_order(
    node_ids: Iterable[str],
    edges: Iterable[Tuple[str, str]],
    *,
    on_cycle: CycleHandling = "truncate",
) -> List[str]:
    """Pure Kahn (BFS) topological sort.

    Args:
        node_ids: iterable of node identifiers. Insertion order is
            preserved by the algorithm: nodes with in-degree 0 are
            processed in the order they appear in ``node_ids``,
            making the output deterministic for any given input.
        edges: iterable of ``(source, target)`` tuples. Edges
            whose source or target is not in ``node_ids`` are
            silently dropped (matches the legacy Arricchisci /
            Manipola behavior of tolerating dangling references).
        on_cycle: how to handle a graph with a cycle.

            - ``"truncate"`` (default, Arricchisci legacy shape):
              cycle nodes are silently omitted from the result.
              The caller can detect a cycle by comparing
              ``len(result)`` with ``len(node_ids)``.
            - ``"raise"`` (Manipola legacy shape): raises
              :class:`GraphCycleError` listing the cycle node
              ids. The exception message matches the verbatim
              Italian operator string Manipola used to emit
              (``"Ciclo rilevato nel canvas tra i nodi: ..."``)
              so log greps and operator-visible UX do not
              regress.

    Returns:
        List of node ids in topological order. Stable across
        Python versions because the queue is a ``deque`` seeded
        in input-order.

    Raises:
        GraphCycleError: when ``on_cycle='raise'`` and a cycle
            exists.
    """
    ids: List[str] = list(node_ids)
    id_set = set(ids)
    in_degree: dict[str, int] = {nid: 0 for nid in ids}
    # ``out_edges`` is keyed on every node so the inner loop
    # never has to handle KeyError; nodes with zero outgoing
    # edges keep an empty list.
    out_edges: dict[str, List[str]] = {nid: [] for nid in ids}
    for source, target in edges:
        # Edge filter: both endpoints must exist in node_ids.
        # Drops "dangling" edges that point at deleted nodes
        # (the legacy Arricchisci / Manipola implementations
        # both did this implicitly through their dict lookups).
        if source in id_set and target in id_set:
            out_edges[source].append(target)
            in_degree[target] += 1

    # Seed queue with all in-degree-0 nodes in input order.
    # Using a deque (not list) so popleft() is O(1).
    queue: deque[str] = deque(nid for nid in ids if in_degree[nid] == 0)
    order: List[str] = []
    while queue:
        nid = queue.popleft()
        order.append(nid)
        for next_id in out_edges[nid]:
            in_degree[next_id] -= 1
            if in_degree[next_id] == 0:
                queue.append(next_id)

    if len(order) != len(ids):
        if on_cycle == "raise":
            ordered_set = set(order)
            cycle_nodes = [nid for nid in ids if nid not in ordered_set]
            raise GraphCycleError(cycle_nodes)
        # on_cycle == "truncate": cycle nodes are silently
        # omitted (legacy Arricchisci shape).
    return order
