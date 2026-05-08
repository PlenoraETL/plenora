"""Contracts for the intermediate run persistence split."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_INTERMEDIATE_ROOT = _BACKEND_ROOT / "app" / "core" / "intermediate"
_RUN_TABLE_SQL_MARKERS = (
    "INSERT INTO runs",
    "SELECT * FROM runs",
    "UPDATE runs",
    "DELETE FROM runs",
    "SELECT run_id FROM runs",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _string_literals(tree: ast.AST) -> list[str]:
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_runs_db_owns_run_crud_without_cross_domain_db_imports() -> None:
    path = _INTERMEDIATE_ROOT / "runs_db.py"
    tree = _tree(path)

    exported_functions = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert {
        "create_run",
        "get_run",
        "list_runs",
        "update_run",
        "pin_run",
        "apply_runs_retention",
        "_delete_run",
        "delete_all_runs_for_canvas",
    }.issubset(exported_functions)

    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not {
        module
        for module in imported_modules
        if module.startswith("app.core.")
        and module.endswith(".db")
        and module != "app.core.intermediate.db"
    }


def test_intermediate_db_facade_contains_no_embedded_run_sql() -> None:
    facade_tree = _tree(_INTERMEDIATE_ROOT / "db.py")
    runs_tree = _tree(_INTERMEDIATE_ROOT / "runs_db.py")

    facade_literals = "\n".join(_string_literals(facade_tree))
    runs_literals = "\n".join(_string_literals(runs_tree))

    leaked = [
        marker for marker in _RUN_TABLE_SQL_MARKERS if marker in facade_literals
    ]
    assert leaked == [], (
        "app.core.intermediate.db must stay a compatibility facade. "
        f"Move run SQL back to runs_db.py. Leaked markers: {leaked}"
    )
    missing_from_leaf = [
        marker for marker in _RUN_TABLE_SQL_MARKERS if marker not in runs_literals
    ]
    assert missing_from_leaf == [], (
        "Run persistence SQL must be owned by runs_db.py. "
        f"Missing leaf markers: {missing_from_leaf}"
    )


def test_intermediate_db_facade_imports_only_intermediate_leaf_dbs() -> None:
    tree = _tree(_INTERMEDIATE_ROOT / "db.py")

    imports = _imported_modules(tree)

    assert "app.core.projects.db" not in imports
    assert "app.core.intermediate.runs_db" in imports
    assert "app.core.intermediate.node_snapshots_db" in imports


def test_intermediate_db_create_run_facade_preserves_patchable_boundary(
    monkeypatch,
) -> None:
    from app.core.intermediate import db as idb

    captured: list[dict] = []

    def fake_execute(sql, params=()):
        captured.append({"sql": sql, "params": tuple(params)})

    monkeypatch.setattr(idb, "execute", fake_execute)
    monkeypatch.setattr(idb, "get_run", lambda run_id: {"run_id": run_id})

    out = idb.create_run(
        "run-1",
        "canvas-1",
        "group-1",
        total_nodes=2,
        user_id="user-1",
    )

    assert out == {"run_id": "run-1"}
    assert len(captured) == 1
    assert "INSERT INTO runs" in captured[0]["sql"]
    assert "user-1" in captured[0]["params"]


def test_intermediate_db_delete_all_runs_facade_uses_patchable_delete(
    monkeypatch,
) -> None:
    from app.core.intermediate import db as idb

    deleted: list[str] = []
    monkeypatch.setattr(
        idb,
        "fetchall",
        lambda sql, params=(): [{"run_id": "r1"}, {"run_id": "r2"}],
    )
    monkeypatch.setattr(idb, "_delete_run", lambda run_id: deleted.append(run_id))
    monkeypatch.setattr(
        idb,
        "logger",
        SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )

    assert idb.delete_all_runs_for_canvas("canvas-1") == 2
    assert deleted == ["r1", "r2"]
