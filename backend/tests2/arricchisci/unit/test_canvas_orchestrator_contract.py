"""
Contract test BE-GO-READY-TRANSFORMATION-ENGINE-011 — canvas_orchestrator extraction.

Lock i 6 invariant dell'estrazione:
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# 1. Source location


def test_canvas_orchestrator_module_exists():
    p = _BACKEND_ROOT / "app" / "core" / "arricchisci" / "canvas_orchestrator.py"
    assert p.exists(), (
        "Atteso: app/core/arricchisci/canvas_orchestrator.py creato da "
        "BE-GO-READY-TRANSFORMATION-ENGINE-011."
    )


def test_execute_enrichment_no_longer_inline_in_api_connector():
    """Source-bound: la sostanza dell'orchestrator (BFS sui nodi,
    dispatch per node_type, gruppi paralleli) non deve piu' vivere
    in `api_connector.py`. Il wrapper KEEP_COMPAT e' ammesso (10
    righe), ma le sezioni canvas-graph specifiche devono essere
    sparite."""
    src = (
        _BACKEND_ROOT / "app" / "core" / "arricchisci" / "api_connector.py"
    ).read_text(encoding="utf-8")
    forbidden = [
        # Body markers tipici dell'orchestrator pre-batch.
        "step_node_ids[0]",
        "compute_parallel_groups(",
        "execute_parallel_enrichers(",
        "node_results[node_id] = self._load_static_values",
        "_passthrough_file_source",
    ]
    for pat in forbidden:
        assert pat not in src, (
            f"REGRESSIONE: pattern `{pat}` ancora presente in "
            f"api_connector.py dopo BE-GO-READY-TRANSFORMATION-"
            f"ENGINE-011. Il body deve vivere in canvas_orchestrator.py."
        )


def test_orchestrator_module_exposes_pure_function_api():
    """Il modulo nuovo deve esportare le 5 funzioni modulo-level
    documentate nel docstring (no metodi su un singleton)."""
    from app.core.arricchisci import canvas_orchestrator as co

    for name in (
        "execute_canvas_enrichment",
        "init_execution_context",
        "get_execution_order",
        "run_single_enricher",
        "is_file_source_for_file_processor",
    ):
        assert hasattr(co, name), (
            f"Atteso: `canvas_orchestrator.{name}` esportato come "
            f"funzione modulo-level."
        )
        # Non e' un metodo bound: e' una funzione plain.
        attr = getattr(co, name)
        assert callable(attr)
    # `__all__` documenta esplicitamente la superficie pubblica.
    # BE-GO-READY-TRANSFORMATION-ENGINE-015: aggiunti
    # `ArricchisciExecutionContext` + `build_execution_context`
    # come re-export tipati del context boundary.
    assert set(co.__all__) == {
        "execute_canvas_enrichment",
        "init_execution_context",
        "get_execution_order",
        "run_single_enricher",
        "is_file_source_for_file_processor",
        "ArricchisciExecutionContext",
        "build_execution_context",
    }


# 2 + 3. KEEP_COMPAT wrappers


def test_api_connection_service_execute_enrichment_is_thin_wrapper():
    """Il metodo `execute_enrichment` su `ApiConnectionService` deve
    esistere come wrapper che delega a
    `canvas_orchestrator.execute_canvas_enrichment(self, ...)`."""
    import inspect

    from app.core.arricchisci.api_connector import ApiConnectionService

    src = inspect.getsource(ApiConnectionService.execute_enrichment)
    assert "execute_canvas_enrichment" in src, (
        "REGRESSIONE: `ApiConnectionService.execute_enrichment` non "
        "delega piu' a `canvas_orchestrator.execute_canvas_enrichment`."
    )
    # Il wrapper deve essere thin: niente body BFS canvas-graph.
    assert "compute_parallel_groups" not in src
    assert "step_node_ids" not in src


# BE-GO-READY-CONNECTION-ENGINE-009-API-CONNECTOR-INSTANCE-WRAPPER-CUT:
# rimosso `test_api_connection_service_get_execution_order_is_thin_wrapper`.
# Il wrapper KEEP_COMPAT `ApiConnectionService._get_execution_order` e'
# stato eliminato: l'ultimo callsite di produzione
# (`workers/tasks/enrichment_task.py:306`) e' stato migrato alla pure
# function `canvas_orchestrator.get_execution_order(...)`. Il lock
# negativo (assenza del metodo dalla classe) vive ora in
# `test_api_connection_service_no_longer_exposes_get_execution_order`
# (vedi sezione "Pure-function behavior locks" sotto).


# 5. No re-export maze


def test_api_connector_does_not_wildcard_import_orchestrator():
    """Anti-frammentazione (AST-bound): `api_connector.py` NON deve
    fare `from ... canvas_orchestrator import *` ne' top-level
    re-import dei 5 nomi pubblici dell'orchestrator. Il wrapper
    KEEP_COMPAT chiama esplicitamente le 2 funzioni che servono via
    lazy import dentro il body del metodo.

    Lo scan e' AST-based per ignorare commenti / docstring che
    legittimamente menzionano i nomi.
    """
    import ast as _ast

    src = (
        _BACKEND_ROOT / "app" / "core" / "arricchisci" / "api_connector.py"
    ).read_text(encoding="utf-8")
    tree = _ast.parse(src)

    forbidden_names = {
        "init_execution_context",
        "run_single_enricher",
        "is_file_source_for_file_processor",
    }
    violations = []
    for node in tree.body:
        # Top-level imports only (lazy imports inside def/class bodies
        # are NOT walked here — that's intentional, the wrapper does
        # `from ... import execute_canvas_enrichment` lazy).
        if (
            isinstance(node, _ast.ImportFrom)
            and node.module == "app.core.arricchisci.canvas_orchestrator"
        ):
            for alias in node.names:
                # Wildcard re-export forbidden.
                assert alias.name != "*", (
                    "REGRESSIONE: wildcard import da canvas_orchestrator "
                    "in api_connector.py — anti-frammentazione."
                )
                if alias.name in forbidden_names:
                    violations.append(alias.name)
    assert not violations, (
        f"REGRESSIONE: top-level re-import di {sorted(violations)} da "
        f"`canvas_orchestrator` in `api_connector.py`. I 3 nomi devono "
        f"vivere solo nel modulo nuovo (no re-export maze)."
    )


# 6. Pure-function behavior locks


def test_get_execution_order_kahn_simple_chain():
    """Pure function: A -> B -> C deve produrre [A, B, C]."""
    from app.core.arricchisci.canvas_orchestrator import get_execution_order

    nodes = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
    edges = [{"source": "A", "target": "B"}, {"source": "B", "target": "C"}]
    assert get_execution_order(nodes, edges) == ["A", "B", "C"]


def test_get_execution_order_disconnected_nodes_appear_first():
    """Pure function: nodi senza edge hanno in_degree=0 e finiscono
    in head (ordine di apparizione preservato dalla deque)."""
    from app.core.arricchisci.canvas_orchestrator import get_execution_order

    nodes = [{"id": "X"}, {"id": "A"}, {"id": "B"}]
    edges = [{"source": "A", "target": "B"}]
    order = get_execution_order(nodes, edges)
    # X compare prima di B (B ha in_degree=1 finche' A non e'
    # processato).
    assert order.index("X") < order.index("B")
    assert order.index("A") < order.index("B")


# BE-GO-READY-CONNECTION-ENGINE-009: rimosso
# `test_get_execution_order_via_class_wrapper_matches_module_function`.
# Il wrapper KEEP_COMPAT e' stato eliminato; l'identita' wrapper-vs-
# function non e' piu' applicabile. Lock dell'assenza del wrapper:
# `test_api_connection_service_no_longer_exposes_get_execution_order`
# in `test_executor_orchestrator_callsite_contract.py`.


def test_is_file_source_for_file_processor_pure_predicate():
    """Pure predicate: ritorna True se file_source e' connesso a un
    api_enrich di tipo `rest_file`."""
    from app.core.arricchisci.canvas_orchestrator import (
        is_file_source_for_file_processor,
    )

    edges = [{"source": "fs", "target": "ae"}]
    node_map = {
        "fs": {"id": "fs", "type": "file_source"},
        "ae": {"id": "ae", "type": "api_enrich", "api_id": "api-1"},
    }
    api_connections = {"api-1": {"connection_type": "rest_file"}}
    assert is_file_source_for_file_processor("fs", edges, node_map, api_connections)

    # Non rest_file → False.
    api_connections_other = {"api-1": {"connection_type": "rest"}}
    assert not is_file_source_for_file_processor(
        "fs", edges, node_map, api_connections_other
    )


# 7. KEEP_COMPAT execute_canvas_enrichment dispatch (smoke)


def test_node_wrappers_built_once_at_entry_boundary():
    """BE-GO-READY-ARCH-057-W2 / 058-W3: source-bound guard.

    `execute_canvas_enrichment` must parse the canvas graph exactly once at
    the entry boundary via ``CanvasGraph.from_canvas(...)``.  No raw
    ``CanvasNode(...)`` calls may appear inside the function body — they all
    live inside ``CanvasGraph.from_canvas``.

    Updated in BE-GO-READY-ARCH-058-W6: W3 replaced the ``node_wrappers``
    comprehension (1 × CanvasNode call) with ``CanvasGraph.from_nodes_edges``
    (0 × direct CanvasNode call in execute_canvas_enrichment).  The invariant
    — no per-iteration node re-wrapping — is still enforced here.
    """
    import ast as _ast

    src = (
        _BACKEND_ROOT / "app" / "core" / "arricchisci" / "canvas_orchestrator.py"
    ).read_text(encoding="utf-8")
    tree = _ast.parse(src)

    # Locate execute_canvas_enrichment function body in the AST.
    exec_fn = next(
        (
            n
            for n in _ast.walk(tree)
            if isinstance(n, _ast.FunctionDef)
            and n.name == "execute_canvas_enrichment"
        ),
        None,
    )
    assert exec_fn is not None, "execute_canvas_enrichment not found in AST"

    # No bare CanvasNode(...) calls allowed — they moved inside CanvasGraph.
    canvas_node_calls = [
        n
        for n in _ast.walk(exec_fn)
        if isinstance(n, _ast.Call)
        and isinstance(n.func, _ast.Name)
        and n.func.id == "CanvasNode"
    ]
    assert len(canvas_node_calls) == 0, (
        f"Bare CanvasNode(...) call reintroduced in execute_canvas_enrichment "
        f"(found {len(canvas_node_calls)}). Use CanvasGraph.from_nodes_edges "
        f"at the entry boundary instead."
    )

    # Exactly 1 CanvasGraph.from_canvas call at the entry boundary.
    graph_intake_calls = [
        n
        for n in _ast.walk(exec_fn)
        if isinstance(n, _ast.Call)
        and isinstance(n.func, _ast.Attribute)
        and n.func.attr == "from_canvas"
    ]
    assert len(graph_intake_calls) == 1, (
        f"Expected exactly 1 CanvasGraph.from_canvas(...) call in "
        f"execute_canvas_enrichment; found {len(graph_intake_calls)}. "
        f"The canvas graph must be parsed once at the entry boundary."
    )


def test_execute_canvas_enrichment_empty_canvas_returns_error_dict():
    """Smoke test: la funzione modulo-level orchestrator si chiama
    con `svc` esplicito e ritorna lo stesso shape errore del wrapper.
    """
    from unittest.mock import patch

    from app.core.arricchisci.api_connector import ApiConnectionService
    from app.core.arricchisci.canvas_orchestrator import (
        execute_canvas_enrichment,
    )

    svc = ApiConnectionService.__new__(ApiConnectionService)
    with patch(
        "app.db.db_service.get_project", return_value={"group_id": "grp-1"}
    ):
        result = execute_canvas_enrichment(
            svc, project_id="proj-1", canvas={"nodes": [], "edges": []},
        )
    assert result == {"success": False, "error": "Nessun nodo nel canvas"}
