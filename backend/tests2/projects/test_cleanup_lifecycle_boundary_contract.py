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


def test_project_cleanup_report_preserves_public_group_keys() -> None:
    from app.core.projects import cleanup

    assert cleanup.GROUP_PROJECT_CLEANUP_REPORT_KEYS == (
        "projects",
        "results",
        "intermediate_runs",
    )
    assert cleanup.GroupProjectCleanupReport(
        projects=1,
        results=2,
        intermediate_runs=3,
    ).as_report() == {
        "projects": 1,
        "results": 2,
        "intermediate_runs": 3,
    }


def test_project_group_cleanup_delegates_fanout_to_domain_boundaries(
    monkeypatch,
) -> None:
    from app.core.projects import cleanup

    calls: list[tuple[str, tuple[str, ...]]] = []

    monkeypatch.setattr(
        cleanup.project_db,
        "list_project_ids_for_group",
        lambda _group_id: {"project-b", "project-a"},
    )

    def _delete_results(project_ids, _log, *, delete_project_func=None):
        calls.append(("results", tuple(project_ids)))
        assert delete_project_func is cleanup.delete_results_for_project
        return 5

    def _delete_runs(project_ids, _log, *, delete_canvas_func=None):
        calls.append(("intermediate", tuple(project_ids)))
        assert delete_canvas_func is cleanup.delete_intermediate_runs_for_group_project
        return 7

    monkeypatch.setattr(cleanup, "delete_results_for_projects", _delete_results)
    monkeypatch.setattr(cleanup, "delete_runs_for_canvases", _delete_runs)
    monkeypatch.setattr(
        cleanup,
        "delete_project_records_for_group",
        lambda _group_id, project_ids, _log: len(project_ids),
    )

    report = cleanup.delete_projects_for_group(
        "group-1",
        type("Log", (), {"warning": lambda *_args, **_kwargs: None})(),
    )

    assert calls == [
        ("results", ("project-a", "project-b")),
        ("intermediate", ("project-a", "project-b")),
    ]
    assert report.as_report() == {
        "projects": 2,
        "results": 5,
        "intermediate_runs": 7,
    }


def test_orphan_cleanup_uses_named_lifecycle_boundaries() -> None:
    path = BACKEND_ROOT / "app" / "core" / "results" / "orphan_cleanup.py"
    src = path.read_text(encoding="utf-8")
    imports = _imported_modules(src)

    assert "app.db.pg_connection" not in imports
    assert "app.core.intermediate.db" not in imports
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
    # `orphan_cleanup` fa `from app.core.intermediate.cleanup import
    # list_canvas_ids_with_runs`, quindi il binding va patchato sul
    # consumer.
    _ = intermediate_cleanup  # noqa: F841
    monkeypatch.setattr(
        orphan_cleanup,
        "list_canvas_ids_with_runs",
        lambda: ["existing-canvas", "orphan-canvas"],
    )

    assert orphan_cleanup.find_orphan_run_canvas_ids() == ["orphan-canvas"]


def test_results_cleanup_boundary_lists_project_ids(monkeypatch) -> None:
    from app.core.results import cleanup

    monkeypatch.setattr(
        cleanup.cleanup_repository,
        "list_distinct_project_id_rows",
        lambda: [
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
        "_list_canvas_ids_with_runs",
        lambda: ["canvas-a", "canvas-b"],
    )

    assert cleanup.list_canvas_ids_with_runs() == ["canvas-a", "canvas-b"]


def test_file_source_group_cleanup_uses_storage_aware_single_row_boundary(
    monkeypatch,
) -> None:
    from app.core.files import cleanup

    calls: list[str] = []
    monkeypatch.setattr(
        cleanup.files_db,
        "list_file_source_ids_for_group",
        lambda group_id: ["file-a", "file-b"] if group_id == "group-1" else [],
    )
    monkeypatch.setattr(
        cleanup.files_db,
        "delete_file_source",
        lambda file_id: calls.append(file_id) or file_id == "file-a",
    )

    assert cleanup.delete_file_sources_for_group("group-1") == 1
    assert calls == ["file-a", "file-b"]
