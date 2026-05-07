"""
FastAPI owner dependencies - canonical leaf.

"""

from __future__ import annotations

from fastapi import Depends

from app.core.auth.dependencies import get_current_user
from app.core.auth.ownership import _check_ownership
from app.shared.contracts.auth import CurrentUser


__all__ = [
    "require_project_owner",
    "require_schedule_owner",
    "require_file_owner",
]


def require_project_owner(
    project_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Owner check su projects per update, delete, canvas e duplicate."""
    from app.db.config.projects import get_project as _get_project
    project = _get_project(project_id)
    _check_ownership(project, "Progetto", current_user)
    return current_user


def require_schedule_owner(
    schedule_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Owner check su schedules per update, delete, toggle e run."""
    from app.db.config.schedules import get_schedule as _get_schedule
    schedule = _get_schedule(schedule_id)
    _check_ownership(schedule, "Schedule", current_user)
    return current_user


def require_file_owner(
    file_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Owner check su file_sources per delete."""
    from app.db.config.file_sources import get_file_source as _get_file_source
    file_source = _get_file_source(file_id)
    _check_ownership(file_source, "File", current_user)
    return current_user
