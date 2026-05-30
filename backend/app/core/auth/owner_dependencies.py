"""
FastAPI owner dependencies - canonical leaf.

"""

from __future__ import annotations

from fastapi import Depends

from app.core.auth.dependencies import get_current_user
from app.core.auth.ownership import _check_ownership
import app.core.files.ownership as files_ownership
import app.core.projects.access_context as projects_access_context
import app.core.scheduler.ownership as scheduler_ownership
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
    project = projects_access_context.load_project_owner_context(project_id)
    _check_ownership(
        project.ownership_resource() if project is not None else None,
        "Progetto",
        current_user,
    )
    return current_user


def require_schedule_owner(
    schedule_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Owner check su schedules per update, delete, toggle e run."""
    schedule = scheduler_ownership.load_schedule_owner_context(schedule_id)
    _check_ownership(
        schedule.ownership_resource() if schedule is not None else None,
        "Schedule",
        current_user,
    )
    return current_user


def require_file_owner(
    file_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Owner check su file_sources per delete."""
    file_source = files_ownership.load_file_owner_context(file_id)
    _check_ownership(
        file_source.ownership_resource() if file_source is not None else None,
        "File",
        current_user,
    )
    return current_user
