"""Project-owned runtime context helpers for worker/orchestration edges."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectRuntimeContext:
    """Small project context needed by asynchronous runtime boundaries."""

    group_id: str | None = None
    project_type: str | None = None
    retention_n: int | None = None


def get_project_runtime_context(project_id: str) -> ProjectRuntimeContext | None:
    """Return the persisted project fields workers need at task runtime."""
    from app.core.projects.db import get_project_row

    row = get_project_row(project_id)
    if row is None:
        return None
    return ProjectRuntimeContext(
        group_id=row.group_id,
        project_type=row.project_type,
        retention_n=row.retention_n,
    )


__all__ = ["ProjectRuntimeContext", "get_project_runtime_context"]
