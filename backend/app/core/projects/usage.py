"""Project-owned usage queries for other core domains.

This boundary keeps project dependency checks owned by the projects domain
while exposing router-friendly payloads at application edges.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.core.projects import db as project_db
from app.core.projects.types import ProjectUsageRows


def list_owned_project_ids(
    project_ids: Iterable[str] | None,
    *,
    owner_group_id: str | None,
) -> set[str]:
    """Return project ids owned by the given group.

    This is the project-domain boundary used by routers that need to validate
    project references without reaching into the persistence bridge directly.
    """
    return project_db.list_owned_project_ids(
        project_ids,
        owner_group_id=owner_group_id,
    )


def list_projects_using_connection(connection_id: str) -> ProjectUsageRows:
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


def list_projects_with_canvas_for_datasource_check(
    project_ids: set[str] | None = None,
) -> ProjectUsageRows:
    return [
        {
            "id": row.id,
            "name": row.name,
            "project_type": row.project_type,
            "canvas": row.canvas,
            "group_id": row.group_id,
        }
        for row in project_db.list_project_canvas_check_rows(project_ids)
    ]


def list_manipola_source_refs(project_ids: set[str]) -> list[project_db.ManipolaSourceRefs]:
    """Sorgenti (progetti e file) dei progetti Manipola, senza leggere i canvas
    nell'applicazione (UI-1, C24): una riga tipizzata per progetto
    (ManipolaSourceRefs)."""
    return project_db.list_manipola_source_refs(project_ids)
