"""Project-owned usage queries for other core domains.

This boundary keeps project dependency checks owned by the projects domain
while exposing router-friendly payloads at application edges.
"""

from __future__ import annotations

from app.core.projects import db as project_db


def list_projects_using_connection(connection_id: str) -> list[dict[str, object]]:
    return [
        {
            "id": row.id,
            "name": row.name,
            "project_type": row.project_type,
            "group_id": row.group_id,
        }
        for row in project_db.list_project_dependency_rows_using_connection(
            connection_id
        )
    ]


def list_projects_with_canvas_for_datasource_check() -> list[dict[str, object]]:
    return [
        {
            "id": row.id,
            "name": row.name,
            "project_type": row.project_type,
            "canvas": row.canvas,
            "group_id": row.group_id,
        }
        for row in project_db.list_project_canvas_check_rows()
    ]
