"""Contract test BE-GO-READY-TRANSFORMATION-ENGINE-014 — executor.py callsite migration.

Lock i 3 invariant del migration batch:

1. **Source-bound**: `executor.py` chiama
   `canvas_orchestrator.get_execution_order(...)` (pure function
   modulo-level), NON `api_connection_service._get_execution_order(...)`
   (singleton wrapper KEEP_COMPAT).
2. **Import esplicito**: `executor.py` importa
   `get_execution_order` da
   `app.core.arricchisci.canvas_orchestrator` al top-level (no
   lazy import, no re-export via api_connection_service).
3. **Behavior preservation**: la chiamata diretta alla pure
   function ritorna lo stesso ordering del wrapper KEEP_COMPAT
   (Kahn topo-sort deterministico byte-per-byte).

Lo scope di questo batch e' rigorosamente l'1-line callsite
migration. NON copre R-011-B (i 4 mixin private methods chiamati
da executor.py:176-202: `_load_static_values`, `_load_file_source`,
`_load_source_project`, `_execute_api_generator`) — tracciato come
audit dedicato.
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


# ---------------------------------------------------------------------------
# 1. Source-bound: no more `_get_execution_order` call via singleton
# ---------------------------------------------------------------------------


def test_executor_does_not_call_get_execution_order_via_singleton():
    """AST-based scan: `executor.py` non deve piu' chiamare
    `api_connection_service._get_execution_order(...)` come
    Attribute call. La menzione in commenti/docstring e' ammessa
    (riferimento al pre-batch).
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


# ---------------------------------------------------------------------------
# 2. Import esplicito top-level
# ---------------------------------------------------------------------------


def test_executor_imports_get_execution_order_from_canvas_orchestrator():
    """Import top-level `from app.core.arricchisci.canvas_orchestrator
    import get_execution_order` (no lazy import, no re-export via
    api_connection_service)."""
    src = _EXECUTOR_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "app.core.arricchisci.canvas_orchestrator":
            continue
        for alias in node.names:
            if alias.name == "get_execution_order":
                found = True
                break
        if found:
            break
    assert found, (
        "Atteso: top-level `from app.core.arricchisci.canvas_orchestrator "
        "import get_execution_order` in executor.py."
    )


# ---------------------------------------------------------------------------
# 3. Behavior preservation (smoke parity)
# ---------------------------------------------------------------------------


def test_get_execution_order_module_function_matches_singleton_wrapper():
    """Smoke parity: la pure function modulo-level e il wrapper
    KEEP_COMPAT sul singleton ritornano lo stesso ordering per un
    DAG non-trivial. Lock di compatibilita' byte-per-byte."""
    from app.core.arricchisci.api_connector import ApiConnectionService
    from app.core.arricchisci.canvas_orchestrator import (
        get_execution_order,
    )

    svc = ApiConnectionService.__new__(ApiConnectionService)
    nodes = [{"id": n} for n in ("seed", "f1", "f2", "join", "result")]
    edges = [
        {"source": "seed", "target": "f1"},
        {"source": "seed", "target": "f2"},
        {"source": "f1", "target": "join"},
        {"source": "f2", "target": "join"},
        {"source": "join", "target": "result"},
    ]
    via_module = get_execution_order(nodes, edges)
    via_wrapper = svc._get_execution_order(nodes, edges)
    assert via_module == via_wrapper
    # Sanity: l'output e' un valido topological order
    # (seed precede f1/f2; f1+f2 precedono join; join precede result).
    assert via_module[0] == "seed"
    assert via_module.index("f1") < via_module.index("join")
    assert via_module.index("f2") < via_module.index("join")
    assert via_module.index("join") < via_module.index("result")
