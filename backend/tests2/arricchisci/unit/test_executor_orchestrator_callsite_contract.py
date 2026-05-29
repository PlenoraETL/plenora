"""
Contract test BE-GO-READY-TRANSFORMATION-ENGINE-014 — executor.py callsite migration.

Lock i 3 invariant del migration batch:
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_EXECUTOR_PY = (
    _BACKEND_ROOT / "app" / "core" / "arricchisci" / "executor.py"
)


# 1. Source-bound: no more `_get_execution_order` call via singleton


def test_executor_does_not_call_get_execution_order_via_singleton():
    """AST-based scan: `executor.py` non deve piu' chiamare
    `api_connection_service._get_execution_order(...)` come
    Attribute call. La menzione in commenti/docstring e' ammessa
    (riferimento al Previous contract).
    """
    src = _EXECUTOR_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not isinstance(f, ast.Attribute):
            continue
        if f.attr != "_get_execution_order":
            continue
        # `obj._get_execution_order(...)` — controlla che obj sia
        # `api_connection_service` (singleton).
        target = f.value
        if isinstance(target, ast.Name) and target.id == "api_connection_service":
            violations.append(node.lineno)
    assert not violations, (
        f"REGRESSIONE BE-GO-READY-TRANSFORMATION-ENGINE-014: "
        f"executor.py chiama ancora "
        f"api_connection_service._get_execution_order(...) alle "
        f"linee {violations}. Il batch migra il callsite alla "
        f"pure function `canvas_orchestrator.get_execution_order(...)`."
    )


def test_executor_preview_delegates_to_canvas_orchestrator():
    """Step C unificazione: `EnrichExecutor.preview` deve DELEGARE al loop
    canonico `execute_canvas_enrichment(...)` invece di reimplementare il
    loop per-nodo. Lock positivo: chiamata Name `execute_canvas_enrichment`.
    """
    src = _EXECUTOR_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "execute_canvas_enrichment"
        for node in ast.walk(tree)
    )
    assert found, (
        "Atteso: executor.py chiama `execute_canvas_enrichment(...)` "
        "(la preview riusa il loop canonico, non lo reimplementa)."
    )


# 2. Import esplicito top-level


def test_executor_imports_execute_canvas_enrichment():
    """Import top-level di `execute_canvas_enrichment` da canvas_orchestrator;
    gli helper di reimplementazione loop (`get_execution_order`,
    `init_execution_context`) non sono piu' importati in executor.py — la
    preview delega al loop canonico."""
    src = _EXECUTOR_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "app.core.arricchisci.canvas_orchestrator":
            continue
        imported.update(alias.name for alias in node.names)
    assert "execute_canvas_enrichment" in imported, (
        "Atteso: top-level import di `execute_canvas_enrichment` da "
        "canvas_orchestrator in executor.py."
    )
    assert not ({"get_execution_order", "init_execution_context"} & imported), (
        "executor.py non deve piu' importare gli helper di reimplementazione "
        "loop: la preview delega a `execute_canvas_enrichment`."
    )


# 3. Preview ctx-strict callsites


def test_executor_does_not_reimplement_canvas_loop():
    """Step C guard (sostituisce TE-031): la preview non deve reintrodurre
    la reimplementazione del loop per-nodo. I concern ctx-strict sui source
    loader e l'ordine topologico sono ora del loop canonico
    `execute_canvas_enrichment` (lockato da `test_canvas_orchestrator_contract`
    e `test_mixin_ctx_migration_contract`). Quindi executor.py NON deve
    chiamare direttamente i loader/generator ne' `get_execution_order`."""
    src = _EXECUTOR_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_name_calls = {"get_execution_order", "init_execution_context"}
    forbidden_attr_calls = {
        "_load_static_values",
        "_load_file_source",
        "_load_source_project",
        "_execute_api_generator",
    }
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in forbidden_name_calls:
            violations.append((f.id, node.lineno))
        elif isinstance(f, ast.Attribute) and f.attr in forbidden_attr_calls:
            violations.append((f.attr, node.lineno))
    assert not violations, (
        "REGRESSIONE Step C: executor.py ha reintrodotto la reimplementazione "
        f"del loop canvas nella preview: {violations}. La preview deve delegare "
        "a `execute_canvas_enrichment` con un finalizer."
    )


# 4. Behavior preservation (smoke parity)


# BE-GO-READY-CONNECTION-ENGINE-009: rimosso
# `test_get_execution_order_module_function_matches_singleton_wrapper`.
# Il wrapper KEEP_COMPAT `ApiConnectionService._get_execution_order`
# e' stato eliminato: non c'e' piu' un "singleton wrapper" da
# parificare alla pure function. La parita' byte-per-byte e' ora
# garantita dall'unica implementazione (la pure function modulo-
# level), non da un test di equivalenza wrapper-vs-function.


def test_get_execution_order_pure_function_topological_order():
    """Smoke: la pure function modulo-level produce un ordering
    topologicamente valido per un DAG non-trivial. Sostituisce
    il pre-009 test di parita' wrapper-vs-function (il wrapper
    e' stato eliminato dal CONNECTION-ENGINE-009)."""
    from app.core.arricchisci.canvas_orchestrator import (
        get_execution_order,
    )

    nodes = [{"id": n} for n in ("seed", "f1", "f2", "join", "result")]
    edges = [
        {"source": "seed", "target": "f1"},
        {"source": "seed", "target": "f2"},
        {"source": "f1", "target": "join"},
        {"source": "f2", "target": "join"},
        {"source": "join", "target": "result"},
    ]
    order = get_execution_order(nodes, edges)
    # Sanity: l'output e' un valido topological order
    # (seed precede f1/f2; f1+f2 precedono join; join precede result).
    assert order[0] == "seed"
    assert order.index("f1") < order.index("join")
    assert order.index("f2") < order.index("join")
    assert order.index("join") < order.index("result")


def test_api_connection_service_no_longer_exposes_get_execution_order():
    """CONNECTION-ENGINE-009 cut lock: `ApiConnectionService` non
    deve piu' esporre il metodo di istanza `_get_execution_order`.
    L'unico canonico e' la pure function
    `canvas_orchestrator.get_execution_order(...)`. Ogni futura
    re-introduzione del wrapper riapre la dipendenza dal singleton
    e va rifiutata in review."""
    from app.core.arricchisci.api_connector import ApiConnectionService

    assert not hasattr(ApiConnectionService, "_get_execution_order"), (
        "REGRESSIONE CONNECTION-ENGINE-009: "
        "`ApiConnectionService._get_execution_order` e' stato "
        "re-introdotto. Il wrapper KEEP_COMPAT e' stato eliminato; "
        "tutti i call site devono usare la pure function "
        "`canvas_orchestrator.get_execution_order(nodes, edges)` "
        "direttamente."
    )
