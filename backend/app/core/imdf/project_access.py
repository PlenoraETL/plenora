"""Project access boundary for IMDF bundle authorization."""

from __future__ import annotations

from typing import Any


def get_project_for_bundle(project_id: str) -> Any:
    """Read project metadata needed to authorize an IMDF bundle."""
    from app.core.projects import db as projects_db

    return projects_db.get_project_row(project_id)


def list_project_ids_visible_to_group(group_id: str) -> set[str]:
    """Read project ids visible to a group for IMDF bundle filtering."""
    from app.core.projects import db as projects_db

    return projects_db.list_project_ids_for_group(group_id)
