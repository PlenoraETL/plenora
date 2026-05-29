"""Contract guards for pipeline worker runtime/domain boundaries."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_PIPELINE_TASK = _BACKEND_ROOT / "workers" / "tasks" / "pipeline_task.py"


def _pipeline_task_tree() -> ast.AST:
    return ast.parse(_PIPELINE_TASK.read_text(encoding="utf-8"))


def test_pipeline_worker_uses_project_runtime_context_boundary():
    tree = _pipeline_task_tree()

    raw_project_db_imports = []
    runtime_context_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "app.core.projects.db":
                raw_project_db_imports.append(node.lineno)
            if node.module == "app.core.projects.runtime_context":
                runtime_context_imports.append(node.lineno)
            if node.module == "app.core.projects":
                for alias in node.names:
                    if alias.name == "runtime_context":
                        runtime_context_imports.append(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.core.projects.db":
                    raw_project_db_imports.append(node.lineno)
                if alias.name == "app.core.projects.runtime_context":
                    runtime_context_imports.append(node.lineno)

    assert not raw_project_db_imports, (
        "pipeline_task must not import app.core.projects.db directly; "
        f"found at lines {raw_project_db_imports}"
    )
    assert runtime_context_imports, (
        "pipeline_task should read project runtime metadata through "
        "app.core.projects.runtime_context"
    )


def test_pipeline_worker_uses_intermediate_run_lifecycle_boundary():
    tree = _pipeline_task_tree()

    direct_db_imports = []
    lifecycle_imports = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        imported_names = {alias.name for alias in node.names}
        if node.module == "app.core.intermediate.db":
            direct_db_imports.append(node.lineno)
        if (
            node.module == "app.core.intermediate.run_lifecycle"
            and (
                "close_worker_connection" in imported_names
                or "close_run" in imported_names
                or "get_default_runs_retention" in imported_names
            )
        ):
            lifecycle_imports.append(node.lineno)
        if node.module == "app.core.intermediate" and "run_lifecycle" in imported_names:
            lifecycle_imports.append(node.lineno)

    assert not direct_db_imports, (
        "pipeline_task must use app.core.intermediate.run_lifecycle for "
        "intermediate run lifecycle operations; direct db import found at "
        f"lines {direct_db_imports}"
    )
    assert lifecycle_imports, (
        "pipeline_task should import intermediate run lifecycle helpers from "
        "app.core.intermediate.run_lifecycle"
    )


def test_project_runtime_context_preserves_persisted_worker_fields(monkeypatch):
    from app.core.projects import runtime_context

    row = SimpleNamespace(
        group_id=None,
        project_type="arricchisci",
        retention_n=3,
    )
    monkeypatch.setattr("app.core.projects.db.get_project_row", lambda project_id: row)

    context = runtime_context.get_project_runtime_context("proj_1")

    assert context == runtime_context.ProjectRuntimeContext(
        group_id=None,
        project_type="arricchisci",
        retention_n=3,
    )


def test_project_runtime_context_resolves_runtime_values_with_defaults(monkeypatch):
    from app.core.projects import runtime_context

    row = SimpleNamespace(
        group_id="grp-1",
        project_type="arricchisci",
        retention_n=3,
    )
    monkeypatch.setattr("app.core.projects.db.get_project_row", lambda project_id: row)

    assert runtime_context.resolve_project_runtime_group_id(
        "proj_1",
        default_group="default",
    ) == "grp-1"
    assert runtime_context.resolve_project_runtime_retention_n(
        "proj_1",
        default=10,
    ) == 3

    monkeypatch.setattr("app.core.projects.db.get_project_row", lambda project_id: None)

    assert runtime_context.resolve_project_runtime_group_id(
        "missing",
        default_group="default",
    ) == "default"
    assert runtime_context.resolve_project_runtime_retention_n(
        "missing",
        default=10,
    ) == 10
