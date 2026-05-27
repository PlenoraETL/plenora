"""Contracts for the intermediate cleanup lifecycle boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_CLEANUP_PATH = _BACKEND_ROOT / "app" / "core" / "intermediate" / "cleanup.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_intermediate_cleanup_imports_leaf_boundaries_without_db_facade() -> None:
    tree = _tree(_CLEANUP_PATH)
    imports = _imported_modules(tree)

    assert "app.core.intermediate.db.fetchall" not in imports
    assert "app.core.intermediate.db.close_connection" not in imports
    assert "app.core.intermediate.db.delete_all_runs_for_canvas" not in imports

    assert "app.core.intermediate.retention.delete_all_runs_for_canvas" in imports
    assert "app.core.intermediate.retention.list_canvas_ids_with_runs" in imports
    assert "app.db.pg_connection.fetchall" not in imports
    assert "app.db.pg_connection.close_connection" in imports
    assert "app.core.intermediate.db.close_worker_connection" not in imports


def test_intermediate_cleanup_delete_delegates_to_retention_boundary(monkeypatch) -> None:
    from app.core.intermediate import cleanup

    calls: list[str] = []

    def fake_delete_all_runs_for_canvas(canvas_id: str) -> int:
        calls.append(canvas_id)
        return 3

    monkeypatch.setattr(
        cleanup,
        "delete_all_runs_for_canvas",
        fake_delete_all_runs_for_canvas,
    )

    assert cleanup.delete_runs_for_canvas("canvas-1") == 3
    assert calls == ["canvas-1"]


def test_intermediate_cleanup_close_delegates_to_pg_boundary(monkeypatch) -> None:
    from app.core.intermediate import cleanup

    calls: list[str] = []
    monkeypatch.setattr(cleanup, "_close_connection", lambda: calls.append("close"))

    cleanup.close_worker_connection()

    assert calls == ["close"]
