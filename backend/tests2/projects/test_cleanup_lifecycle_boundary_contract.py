from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def _imported_modules(src: str) -> set[str]:
    tree = ast.parse(src)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_project_delete_uses_project_cleanup_boundary() -> None:
    from app.core.projects.service import ProjectService

    src = inspect.getsource(ProjectService.delete_project)

    assert "app.core.projects.cleanup" in src
    assert "delete_comparison_keys_for_project" in src
    assert "from app.core.projects.db import delete_comparison_keys" not in src


def test_project_cleanup_boundary_delegates_comparison_keys(monkeypatch) -> None:
    from app.core.projects import cleanup

    calls: list[str] = []
    monkeypatch.setattr(
        cleanup.project_db,
        "delete_comparison_keys",
        lambda project_id: calls.append(project_id),
    )

    cleanup.delete_comparison_keys_for_project("project-1")

    assert calls == ["project-1"]


def test_orphan_cleanup_uses_named_lifecycle_boundaries() -> None:
    path = BACKEND_ROOT / "app" / "core" / "results" / "orphan_cleanup.py"
    src = path.read_text(encoding="utf-8")
    imports = _imported_modules(src)

    assert "app.db.pg_connection" not in imports
    assert "app.core.intermediate.db" not in imports
    assert "app.core.intermediate" not in imports
    assert "app.core.intermediate.cleanup" in imports
    assert "app.core.projects.cleanup" in imports
    assert "app.core.results.cleanup" in imports


def test_orphan_result_scan_filters_through_cleanup_boundaries(monkeypatch) -> None:
    from app.core.results import orphan_cleanup

    monkeypatch.setattr(
        orphan_cleanup,
        "list_existing_project_ids",
        lambda: {"existing-project"},
    )
    monkeypatch.setattr(
        orphan_cleanup,
        "list_project_ids_with_results",
        lambda: ["existing-project", "orphan-project"],
    )

    assert orphan_cleanup.find_orphan_result_project_ids() == ["orphan-project"]


def test_orphan_run_scan_filters_through_cleanup_boundaries(monkeypatch) -> None:
    from app.core.intermediate import cleanup as intermediate_cleanup
    from app.core.results import orphan_cleanup

    monkeypatch.setattr(
        orphan_cleanup,
        "list_existing_project_ids",
        lambda: {"existing-canvas"},
    )
    monkeypatch.setattr(
        intermediate_cleanup,
        "list_canvas_ids_with_runs",
        lambda: ["existing-canvas", "orphan-canvas"],
    )

    assert orphan_cleanup.find_orphan_run_canvas_ids() == ["orphan-canvas"]


def test_results_cleanup_boundary_lists_project_ids(monkeypatch) -> None:
    from app.core.results import cleanup

    monkeypatch.setattr(
        cleanup,
        "fetchall",
        lambda sql: [
            {"project_id": "project-a"},
            ("project-b",),
            {"project_id": None},
        ],
    )

    assert cleanup.list_project_ids_with_results() == ["project-a", "project-b"]


def test_intermediate_cleanup_boundary_lists_canvas_ids(monkeypatch) -> None:
    from app.core.intermediate import cleanup

    monkeypatch.setattr(
        cleanup,
        "fetchall",
        lambda sql: [
            {"canvas_id": "canvas-a"},
            ("canvas-b",),
            {"canvas_id": None},
        ],
    )

    assert cleanup.list_canvas_ids_with_runs() == ["canvas-a", "canvas-b"]
