"""
Domain-agnostic graph utilities.

"""

from __future__ import annotations

from collections import deque
from collections.abc import MutableMapping
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
    in_degree: MutableMapping[str, int] = {nid: 0 for nid in ids}
    # ``out_edges`` is keyed on every node so the inner loop
    # never has to handle KeyError; nodes with zero outgoing
    # edges keep an empty list
    out_edges: MutableMapping[str, List[str]] = {nid: [] for nid in ids}
    for source, target in edges:
        # Edge filter: both endpoints must exist in node_ids
        # Drops "dangling" edges that point at deleted nodes
        # the legacy Arricchisci / Manipola implementations
        # both did this implicitly through their dict lookups
        if source in id_set and target in id_set:
            out_edges[source].append(target)
            in_degree[target] += 1

    # Seed queue with all in-degree-0 nodes in input order
    # Using a deque (not list) so popleft is O(1
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
        # omitted (legacy Arricchisci shape
    return order
