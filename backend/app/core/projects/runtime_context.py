"""Project-owned runtime context helpers for worker/orchestration edges."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.projects import db as project_db


@dataclass(frozen=True)
class ProjectRuntimeContext:
    """Small project context needed by asynchronous runtime boundaries."""

    group_id: str | None = None
    project_type: str | None = None
    retention_n: int | None = None


def get_project_runtime_context(project_id: str) -> ProjectRuntimeContext | None:
    """Return the persisted project fields workers need at task runtime."""
    row = project_db.get_project_row(project_id)
    if row is None:
        return None
    return ProjectRuntimeContext(
        group_id=row.group_id,
        project_type=row.project_type,
        retention_n=row.retention_n,
    )


def resolve_project_runtime_group_id(
    project_id: str,
    *,
    default_group: str,
) -> str:
    """Return the project's persisted group id, falling back to the default."""
    context = get_project_runtime_context(project_id)
    if context is None:
        return default_group
    return context.group_id or default_group


def resolve_project_runtime_retention_n(
    project_id: str,
    *,
    default: int,
) -> int:
    """Return the project's result retention policy, falling back to default."""
    context = get_project_runtime_context(project_id)
    if context is None or context.retention_n is None:
        return default
    return context.retention_n


__all__ = [
    "ProjectRuntimeContext",
    "get_project_runtime_context",
    "resolve_project_runtime_group_id",
    "resolve_project_runtime_retention_n",
]
