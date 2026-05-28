"""Contracts for the projects domain-owned DB access pilot."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROJECTS_CORE_ROOT = BACKEND_ROOT / "app" / "core" / "projects"


def test_projects_domain_db_owns_sql_and_exports_project_contract() -> None:
    """Project-domain persistence owns SQL and the public project contract."""
    from app.core.projects import db as domain_db

    exported_names = {
        "ProjectRow",
        "ProjectDetailRow",
        "ProjectDependencyRow",
        "ProjectCanvasCheckRow",
        "ProjectRawCanvasRow",
        "ProjectRepository",
        "get_project_row",
        "get_project_raw_canvas_row",
        "list_projects",
        "create_project_row",
        "delete_comparison_keys",
        "update_project_row",
        "delete_project",
        "get_project_retention_n",
        "list_owned_project_ids",
        "list_project_ids_for_group",
        "list_project_dependency_rows_using_connection",
        "list_project_canvas_check_rows",
        "update_project_output_contract_row",
        "update_project_canvas_key_atomic",
    }

    assert set(domain_db.__all__) == exported_names
    for name in exported_names:
        assert callable(getattr(domain_db, name))

    domain_src = inspect.getsource(domain_db)
    assert "SELECT * FROM projects" in domain_src
    assert "__getattr__" not in domain_src
    assert "def get_project(" not in domain_src
    assert "def create_project(" not in domain_src
    assert "def update_project(" not in domain_src
    assert "list_projects_using_connection" not in domain_src
    assert "list_projects_with_canvas_for_datasource_check" not in domain_src


def test_projects_router_uses_project_owned_usage_boundary() -> None:
    from app.core.projects import router as projects_router

    router_src = inspect.getsource(projects_router)
    helper_src = inspect.getsource(projects_router._visible_project_ids_strict)

    assert "from app.core.projects.usage import" in router_src
    assert "app.core.projects.db" not in router_src
    assert "list_owned_project_ids(" in helper_src
    legacy_import = "from app.db.config." + "projects import list_owned_project_ids"
    assert legacy_import not in router_src


def test_project_core_does_not_call_legacy_project_dict_read() -> None:
    offenders: list[str] = []

    for path in sorted(PROJECTS_CORE_ROOT.glob("*.py")):
        if path.name == "db.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get_project"
            ):
                offenders.append(path.relative_to(BACKEND_ROOT).as_posix())

    assert offenders == []
