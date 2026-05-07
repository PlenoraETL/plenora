"""Domain-owned project persistence access.

KEEP_COMPAT: SQL still lives in ``app.db.config.projects`` for this pilot.
This module is the canonical import path for project-domain callers while the
existing DB leaf remains the infrastructure adapter and legacy patch target.
"""

from __future__ import annotations

from app.db.config.projects import (
    ProjectDetailRow,
    ProjectRow,
    create_project,
    delete_project,
    get_project,
    get_project_retention_n,
    get_project_row,
    list_owned_project_ids,
    list_project_ids_for_group,
    list_projects,
    list_projects_using_connection,
    list_projects_with_canvas_for_datasource_check,
    update_project,
    update_project_canvas_key_atomic,
)

__all__ = [
    "ProjectDetailRow",
    "ProjectRow",
    "create_project",
    "delete_project",
    "get_project",
    "get_project_retention_n",
    "get_project_row",
    "list_owned_project_ids",
    "list_project_ids_for_group",
    "list_projects",
    "list_projects_using_connection",
    "list_projects_with_canvas_for_datasource_check",
    "update_project",
    "update_project_canvas_key_atomic",
]
