"""Project-owned execution context helpers for downstream executors."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.projects import db as project_db
from app.core.projects.retention import resolve_runs_retention_n


@dataclass(frozen=True)
class ProjectExecutionContext:
    """Minimal project metadata needed by executors."""

    project_id: str
    group_id: str | None
    project_type: str
    retention_n: int | None = None


def get_project_execution_context(
    project_id: str,
) -> ProjectExecutionContext | None:
    """Return executor-facing project context, or None when absent."""
    row = project_db.get_project_row(project_id)
    if row is None:
        return None
    return ProjectExecutionContext(
        project_id=row.id or project_id,
        group_id=row.group_id,
        project_type=row.project_type or "estrai",
        retention_n=row.retention_n,
    )


def get_project_group_id(project_id: str) -> str | None:
    """Return the owning group_id for a project, if available."""
    context = get_project_execution_context(project_id)
    return context.group_id if context else None


def get_upstream_run_context(
    project_id: str,
    *,
    default_retention_n: int,
) -> ProjectExecutionContext:
    """Return context for cascade upstream runs with configured defaults."""
    context = get_project_execution_context(project_id)
    if context is None:
        return ProjectExecutionContext(
            project_id=project_id,
            group_id="",
            project_type="estrai",
            retention_n=resolve_runs_retention_n(
                project_id,
                default=default_retention_n,
            ),
        )
    retention_n = context.retention_n
    if retention_n is None:
        retention_n = resolve_runs_retention_n(
            project_id,
            default=default_retention_n,
        )
    return ProjectExecutionContext(
        project_id=context.project_id,
        group_id=context.group_id,
        project_type=context.project_type,
        retention_n=retention_n,
    )
