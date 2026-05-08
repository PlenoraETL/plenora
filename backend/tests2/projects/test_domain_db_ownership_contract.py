"""Contracts for the projects domain-owned DB access pilot."""

from __future__ import annotations

import inspect

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_projects_domain_db_owns_sql_and_exports_project_contract() -> None:
    """Project-domain persistence owns SQL and the public project contract."""
    from app.core.projects import db as domain_db

    exported_names = {
        "ProjectRow",
        "ProjectDetailRow",
        "ProjectDependencyRow",
        "ProjectCanvasCheckRow",
        "ProjectRepository",
        "get_project_row",
        "get_project",
        "list_projects",
        "create_project",
        "create_project_row",
        "delete_comparison_keys",
        "update_project",
        "update_project_row",
        "delete_project",
        "get_project_retention_n",
        "list_owned_project_ids",
        "list_project_ids_for_group",
        "list_project_dependency_rows_using_connection",
        "list_projects_using_connection",
        "list_project_canvas_check_rows",
        "list_projects_with_canvas_for_datasource_check",
        "update_project_canvas_key_atomic",
    }

    assert set(domain_db.__all__) == exported_names
    for name in exported_names:
        assert callable(getattr(domain_db, name))

    domain_src = inspect.getsource(domain_db)
    assert "SELECT * FROM projects" in domain_src
    assert "__getattr__" not in domain_src


def test_projects_router_uses_project_owned_usage_boundary() -> None:
    from app.core.projects import router as projects_router

    router_src = inspect.getsource(projects_router)
    helper_src = inspect.getsource(projects_router._visible_project_ids_strict)

    assert "from app.core.projects.usage import list_owned_project_ids" in router_src
    assert "app.core.projects.db" not in router_src
    assert "list_owned_project_ids(" in helper_src
    legacy_import = "from app.db.config." + "projects import list_owned_project_ids"
    assert legacy_import not in router_src
