"""Contract tests for the shared Kahn topological-sort utility.

BE-GO-READY-GRAPH-ENGINE-001-SHARED-TOPOLOGICAL-SORT.

Locks the deterministic behavior of
``app.shared.execution.graph.kahn_topological_order``:

  - simple chain ordering;
  - independent / disconnected nodes preserve input order;
  - dangling edges are silently dropped;
  - duplicate edges contribute multiple in-degree counts and
    therefore require multiple decrements;
  - on_cycle='truncate' silently omits cycle nodes (Arricchisci
    legacy shape);
  - on_cycle='raise' raises ``GraphCycleError`` with the
    canonical Italian message AND the cycle-node list (Manipola
    legacy shape);
  - ``GraphCycleError`` subclasses ``ValueError`` so legacy
    ``except ValueError`` paths keep working.

All tests are pure / stdlib-only and run without any DB /
FastAPI / pandas import; they catch a regression in the shared
algorithm before either the Arricchisci or Manipola callsite
sees it.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# 1. Happy paths


def test_simple_chain_a_b_c():
    """A -> B -> C must return [A, B, C]."""
    from app.shared.execution.graph import kahn_topological_order

    order = kahn_topological_order(
        ["A", "B", "C"], [("A", "B"), ("B", "C")],
    )
    assert order == ["A", "B", "C"]


def test_disconnected_nodes_preserve_input_order_at_head():
    """Disconnected nodes (in-degree 0) appear in input-order;
    each in-degree-positive node appears AFTER its predecessor.
    """
    from app.shared.execution.graph import kahn_topological_order

    order = kahn_topological_order(
        ["X", "A", "B"], [("A", "B")],
    )
    # X has in_degree 0 and appears in input position [0]; A
    # also in_degree 0 at position [1]; B appears AFTER A.
    assert order.index("X") < order.index("B")
    assert order.index("A") < order.index("B")
    # Stability: X processed before A because X comes first
    # in node_ids.
    assert order.index("X") < order.index("A")


def test_diamond_graph():
    """A -> B, A -> C, B -> D, C -> D. D must come last; B and
    C order is determined by their edge from A (both ready
    after A -> in input order)."""
    from app.shared.execution.graph import kahn_topological_order

    order = kahn_topological_order(
        ["A", "B", "C", "D"],
        [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")],
    )
    assert order[0] == "A"
    assert order[-1] == "D"
    assert set(order) == {"A", "B", "C", "D"}


def test_no_edges_returns_input_order():
    """No edges -> every node has in-degree 0 -> result equals
    input order."""
    from app.shared.execution.graph import kahn_topological_order

    order = kahn_topological_order(["A", "B", "C", "D"], [])
    assert order == ["A", "B", "C", "D"]


def test_empty_graph_returns_empty_list():
    from app.shared.execution.graph import kahn_topological_order

    assert kahn_topological_order([], []) == []


def test_single_node_returns_single_element():
    from app.shared.execution.graph import kahn_topological_order

    assert kahn_topological_order(["only"], []) == ["only"]


# 2. Edge filtering


def test_dangling_edge_source_unknown_is_dropped():
    """An edge whose source is NOT in node_ids is silently
    dropped (legacy behavior tolerates dangling refs)."""
    from app.shared.execution.graph import kahn_topological_order

    order = kahn_topological_order(
        ["A", "B"], [("UNKNOWN", "B")],
    )
    # The dangling edge contributes nothing; both A and B are
    # in-degree 0; output is input order.
    assert order == ["A", "B"]


def test_dangling_edge_target_unknown_is_dropped():
    from app.shared.execution.graph import kahn_topological_order

    order = kahn_topological_order(
        ["A", "B"], [("A", "UNKNOWN")],
    )
    assert order == ["A", "B"]


def test_self_loop_creates_cycle():
    """A self-loop (A -> A) makes A unreachable; with
    on_cycle='truncate' A is omitted; with on_cycle='raise'
    it raises."""
    from app.shared.execution.graph import (
        GraphCycleError,
        kahn_topological_order,
    )

    truncated = kahn_topological_order(
        ["A"], [("A", "A")], on_cycle="truncate",
    )
    assert truncated == []

    with pytest.raises(GraphCycleError) as exc_info:
        kahn_topological_order(["A"], [("A", "A")], on_cycle="raise")
    assert exc_info.value.cycle_nodes == ["A"]


def test_duplicate_edges_increase_in_degree():
    """Two edges A->B contribute in_degree=2 on B. Algorithm
    must decrement BOTH before B becomes ready."""
    from app.shared.execution.graph import kahn_topological_order

    order = kahn_topological_order(
        ["A", "B"], [("A", "B"), ("A", "B")],
    )
    # B becomes ready only after both decrements; A processes
    # both out-edges.
    assert order == ["A", "B"]


# 3. Cycle handling


def test_two_node_cycle_truncate_omits_both():
    from app.shared.execution.graph import kahn_topological_order

    order = kahn_topological_order(
        ["A", "B"], [("A", "B"), ("B", "A")], on_cycle="truncate",
    )
    assert order == []


def test_two_node_cycle_raise_lists_cycle_nodes():
    from app.shared.execution.graph import (
        GraphCycleError,
        kahn_topological_order,
    )

    with pytest.raises(GraphCycleError) as exc_info:
        kahn_topological_order(
            ["A", "B"], [("A", "B"), ("B", "A")], on_cycle="raise",
        )
    err = exc_info.value
    assert err.cycle_nodes == ["A", "B"]
    # Verbatim Italian operator message preserved from the
    # legacy Manipola implementation:
    msg = str(err)
    assert "Ciclo rilevato nel canvas tra i nodi" in msg
    assert "Rimuovere le connessioni circolari" in msg


def test_partial_cycle_truncate_returns_acyclic_prefix():
    """A -> B (acyclic), C <-> D (cycle). Truncate returns just
    [A, B]; cycle nodes silently omitted."""
    from app.shared.execution.graph import kahn_topological_order

    order = kahn_topological_order(
        ["A", "B", "C", "D"],
        [("A", "B"), ("C", "D"), ("D", "C")],
        on_cycle="truncate",
    )
    assert order == ["A", "B"]


def test_partial_cycle_raise_lists_only_cycle_nodes():
    from app.shared.execution.graph import (
        GraphCycleError,
        kahn_topological_order,
    )

    with pytest.raises(GraphCycleError) as exc_info:
        kahn_topological_order(
            ["A", "B", "C", "D"],
            [("A", "B"), ("C", "D"), ("D", "C")],
            on_cycle="raise",
        )
    # The cycle is C <-> D; A and B are NOT in the cycle.
    assert exc_info.value.cycle_nodes == ["C", "D"]


def test_graph_cycle_error_is_value_error_subclass():
    """KEEP_COMPAT: legacy callers that ``except ValueError``
    keep working unchanged."""
    from app.shared.execution.graph import GraphCycleError

    assert issubclass(GraphCycleError, ValueError)
    err = GraphCycleError(["A", "B"])
    assert isinstance(err, ValueError)


# 4. Determinism


def test_input_order_determines_tie_break():
    """Multiple in-degree-0 nodes are processed in input order
    (deque popleft + initial-queue input-order seeding)."""
    from app.shared.execution.graph import kahn_topological_order

    order_ab = kahn_topological_order(["A", "B"], [])
    assert order_ab == ["A", "B"]

    order_ba = kahn_topological_order(["B", "A"], [])
    assert order_ba == ["B", "A"]


def test_repeated_calls_return_identical_lists():
    """Pure function: same input -> same output, no hidden
    state."""
    from app.shared.execution.graph import kahn_topological_order

    nodes = ["A", "B", "C", "D"]
    edges = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
    result1 = kahn_topological_order(nodes, edges)
    result2 = kahn_topological_order(nodes, edges)
    result3 = kahn_topological_order(nodes, edges)
    assert result1 == result2 == result3


# 5. Module boundary


def test_module_imports_no_domain_dependencies():
    """The shared graph utility MUST NOT pull domain modules
    (FastAPI, app.config, app.db, pandas, etc.) as transitive
    imports. Lock-test by snapshotting sys.modules."""
    import sys

    # Drop any cached version so the next import is observable.
    for mod_name in list(sys.modules):
        if mod_name == "app.shared.execution.graph" or mod_name.startswith(
            "app.shared.execution.graph."
        ):
            del sys.modules[mod_name]

    before = set(sys.modules.keys())
    import app.shared.execution.graph  # noqa: F401
    new_modules = set(sys.modules.keys()) - before

    forbidden = {
        "fastapi",
        "starlette",
        "pandas",
        "geopandas",
        "polars",
        "psycopg2",
        "redis",
    }
    leaked = forbidden & new_modules
    assert not leaked, (
        f"Importing app.shared.execution.graph pulled forbidden modules: "
        f"{sorted(leaked)}. The graph utility must remain a pure "
        "leaf with stdlib-only dependencies."
    )
    # Also no app.* modules (the utility is a leaf consumed by
    # core; it must not depend on core).
    leaked_app = {m for m in new_modules if m.startswith("app.")}
    assert leaked_app == {"app.shared.execution.graph"} or leaked_app == set(), (
        f"Importing app.shared.execution.graph pulled extra app.* modules: "
        f"{sorted(leaked_app - {'app.shared.execution.graph'})}. The shared "
        "graph utility must be a domain-agnostic leaf."
    )


def test_public_surface_is_exactly_two_symbols():
    """Anti-god-engine: the public surface of ``app.shared.execution.graph``
    is intentionally narrow -- ONE primitive + its typed
    exception. Adding a third symbol is a deliberate contract
    change; this test catches a silent expansion."""
    import app.shared.execution.graph as graph

    assert hasattr(graph, "__all__")
    assert set(graph.__all__) == {"GraphCycleError", "kahn_topological_order"}
    # Defense-in-depth: every documented symbol exists at runtime.
    for name in graph.__all__:
        assert hasattr(graph, name), (
            f"Documented symbol `{name}` missing from app.shared.execution.graph"
        )


# 6. Adapter-shape regression: Arricchisci dict shape
#
# Arricchisci pulls (id / source / target) from canvas dicts
# before calling the shared utility. Lock the legacy chain
# behavior so a refactor cannot regress the canvas-orchestrator
# adapter.


def test_arricchisci_dict_adapter_matches_legacy_chain_behavior():
    """The dict-shape adapter that ``canvas_orchestrator.
    get_execution_order`` uses must produce the same order as
    the legacy hand-rolled Kahn implementation for a simple
    chain."""
    from app.shared.execution.graph import kahn_topological_order

    nodes = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
    edges = [{"source": "A", "target": "B"}, {"source": "B", "target": "C"}]

    # Adapter shape mirrors the production wrapper.
    node_ids = [n["id"] for n in nodes]
    edge_pairs = [(e.get("source"), e.get("target")) for e in edges]
    order = kahn_topological_order(
        node_ids, edge_pairs, on_cycle="truncate",
    )
    assert order == ["A", "B", "C"]
