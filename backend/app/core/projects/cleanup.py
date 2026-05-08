"""Lifecycle cleanup boundary for project-owned config artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.intermediate.cleanup import delete_runs_for_canvas
from app.core.projects import db as project_db
from app.core.results.cleanup import delete_results_for_project


class CleanupLogger(Protocol):
    def warning(self, msg: str, *args, **kwargs) -> None: ...


@dataclass(frozen=True)
class GroupProjectCleanupReport:
    """Counts produced while removing a group's project-owned artifacts."""

    projects: int = 0
    results: int = 0
    intermediate_runs: int = 0


def delete_comparison_keys_for_project(project_id: str) -> None:
    """Delete comparison-key config rows associated with a project canvas."""
    project_db.delete_comparison_keys(project_id)


def list_existing_project_ids() -> set[str]:
    """Return all persisted project IDs for cleanup/orphan reconciliation."""
    return {row.id for row in project_db.list_projects()}


def delete_intermediate_runs_for_group_project(project_id: str) -> int:
    """Delete intermediate runs for a project during group lifecycle cleanup."""
    return delete_runs_for_canvas(project_id)


def delete_projects_for_group(
    group_id: str,
    log: CleanupLogger,
) -> GroupProjectCleanupReport:
    """Delete projects and project-owned dependent artifacts for a group."""
    projects = 0
    results = 0
    intermediate_runs = 0

    try:
        project_ids = sorted(project_db.list_project_ids_for_group(group_id))
    except Exception as exc:
        log.warning(f"Cascade delete projects fallito: {exc}")
        return GroupProjectCleanupReport()

    for project_id in project_ids:
        try:
            results += delete_results_for_project(project_id)
        except Exception as exc:
            log.warning(f"Cascade delete results progetto {project_id}: {exc}")

        try:
            intermediate_runs += delete_intermediate_runs_for_group_project(project_id)
        except Exception as exc:
            log.warning(f"Cascade delete intermediate progetto {project_id}: {exc}")

    for project_id in project_ids:
        try:
            delete_comparison_keys_for_project(project_id)
        except Exception as exc:
            log.warning(f"Cascade delete comparison_keys progetto {project_id}: {exc}")

        try:
            if project_db.delete_project(project_id, group_id=group_id):
                projects += 1
        except Exception as exc:
            log.warning(f"Cascade delete project {project_id} fallito: {exc}")

    return GroupProjectCleanupReport(
        projects=projects,
        results=results,
        intermediate_runs=intermediate_runs,
    )


__all__ = [
    "GroupProjectCleanupReport",
    "delete_comparison_keys_for_project",
    "delete_intermediate_runs_for_group_project",
    "delete_projects_for_group",
    "list_existing_project_ids",
]
