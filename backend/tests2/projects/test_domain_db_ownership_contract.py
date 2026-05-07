"""Contracts for the projects domain-owned DB access pilot."""

from __future__ import annotations

import inspect

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_projects_domain_db_bridge_reexports_existing_sql_leaf() -> None:
    """Project-domain persistence imports go through app.core.projects.db.

    KEEP_COMPAT: the bridge delegates to app.db.config.projects for this pilot,
    so SQL bodies and legacy patch targets remain in one place.
    """
    from app.core.projects import db as domain_db
    from app.db.config import projects as projects_leaf

    exported_names = {
        "ProjectRow",
        "ProjectDetailRow",
        "get_project_row",
        "get_project",
        "list_projects",
        "create_project",
        "update_project",
        "delete_project",
        "get_project_retention_n",
        "list_owned_project_ids",
        "list_project_ids_for_group",
        "list_projects_using_connection",
        "list_projects_with_canvas_for_datasource_check",
        "update_project_canvas_key_atomic",
    }

    assert set(domain_db.__all__) == exported_names
    for name in exported_names:
        assert getattr(domain_db, name) is getattr(projects_leaf, name)


def test_projects_router_imports_domain_db_bridge_not_db_leaf() -> None:
    from app.core.projects import router as projects_router

    src = inspect.getsource(projects_router._visible_project_ids_strict)

    assert "from app.core.projects.db import list_owned_project_ids" in src
    assert "from app.db.config.projects import list_owned_project_ids" not in src
