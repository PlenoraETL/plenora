"""Project access boundary for IMDF bundle authorization."""

from __future__ import annotations

from typing import Any

from app.core.projects.access_context import (
    list_project_ids_visible_to_group as _list_project_ids_visible_to_group,
    load_project_visibility_resource,
)


def get_project_for_bundle(project_id: str) -> Any:
    """Read project metadata needed to authorize an IMDF bundle."""
    return load_project_visibility_resource(project_id)


def list_project_ids_visible_to_group(group_id: str) -> set[str]:
    """Read project ids visible to a group for IMDF bundle filtering."""
    return _list_project_ids_visible_to_group(group_id)
