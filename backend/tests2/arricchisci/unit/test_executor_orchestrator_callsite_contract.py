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


def test_executor_calls_canvas_orchestrator_get_execution_order():
    """AST-based positive lock: `executor.py` deve chiamare
    `get_execution_order(nodes, edges)` come funzione modulo-level
    (Name call, non Attribute call su un'istanza)."""
    src = _EXECUTOR_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        # Plain `get_execution_order(...)` Name call.
        if isinstance(f, ast.Name) and f.id == "get_execution_order":
            found = True
            break
    assert found, (
        "Atteso: executor.py chiama "
        "`get_execution_order(nodes, edges)` come funzione "
        "modulo-level (importata da "
        "`app.core.arricchisci.canvas_orchestrator`)."
    )


# 2. Import esplicito top-level


def test_executor_imports_canvas_orchestrator_helpers():
    """Import top-level da `app.core.arricchisci.canvas_orchestrator`
    per i due helper usati dalla preview: `get_execution_order` e
    `init_execution_context` (no lazy import, no re-export via
    api_connection_service)."""
    src = _EXECUTOR_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "app.core.arricchisci.canvas_orchestrator":
            continue
        for alias in node.names:
            if alias.name in {"get_execution_order", "init_execution_context"}:
                found.add(alias.name)
    assert found == {"get_execution_order", "init_execution_context"}, (
        "Atteso: top-level import di `get_execution_order` e "
        "`init_execution_context` da canvas_orchestrator in executor.py."
    )


# 3. Preview ctx-strict callsites


def test_executor_preview_passes_ctx_to_source_loaders():
    """TE-031 regression guard: dopo TE-030B i source loader sono
    ctx-strict. `EnrichExecutor.preview()` deve costruire `ctx` e
    passarlo ai tre loader source/static, altrimenti la preview HTTP
    fail-chiude per file_source/project_source nodes."""
    src = _EXECUTOR_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target_methods = {
        "_load_static_values",
        "_load_file_source",
        "_load_source_project",
    }
    found = {name: False for name in target_methods}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in target_methods:
            continue
        has_ctx_kw = any(
            kw.arg == "ctx"
            and isinstance(kw.value, ast.Name)
            and kw.value.id == "ctx"
            for kw in node.keywords
        )
        found[func.attr] = has_ctx_kw

    missing = [name for name, ok in found.items() if not ok]
    assert not missing, (
        "EnrichExecutor.preview must pass `ctx=ctx` to source/static "
        f"loaders. Missing ctx kwarg on: {missing}"
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
