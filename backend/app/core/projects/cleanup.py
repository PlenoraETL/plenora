"""Lifecycle cleanup boundary for project-owned config artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.intermediate.cleanup import (
    delete_runs_for_canvas,
    delete_runs_for_canvases,
)
from app.core.projects import db as project_db
from app.core.results.cleanup import (
    delete_results_for_project,
    delete_results_for_projects,
)


GROUP_PROJECT_CLEANUP_REPORT_KEYS: tuple[str, ...] = (
    "projects",
    "results",
    "intermediate_runs",
)


class CleanupLogger(Protocol):
    def warning(self, msg: str, *args, **kwargs) -> None: ...


@dataclass(frozen=True)
class GroupProjectCleanupReport:
    """Counts produced while removing a group's project-owned artifacts."""

    projects: int = 0
    results: int = 0
    intermediate_runs: int = 0

    def as_report(self) -> dict[str, int]:
        """Return the public group cleanup report keys owned by this boundary."""
        return {
            "projects": self.projects,
            "results": self.results,
            "intermediate_runs": self.intermediate_runs,
        }


def delete_comparison_keys_for_project(project_id: str) -> None:
    """Delete comparison-key config rows associated with a project canvas."""
    project_db.delete_comparison_keys(project_id)


def list_existing_project_ids() -> set[str]:
    """Return all persisted project IDs for cleanup/orphan reconciliation."""
    return {row.id for row in project_db.list_projects()}


def delete_intermediate_runs_for_group_project(project_id: str) -> int:
    """Delete intermediate runs for a project during group lifecycle cleanup."""
    return delete_runs_for_canvas(project_id)


def _list_group_project_ids(group_id: str, log: CleanupLogger) -> tuple[str, ...]:
    try:
        return tuple(sorted(project_db.list_project_ids_for_group(group_id)))
    except Exception as exc:
        log.warning(f"Cascade delete projects fallito: {exc}")
        return ()


def delete_results_for_group_projects(
    project_ids: tuple[str, ...],
    log: CleanupLogger,
) -> int:
    """Delete result artifacts for group projects through the results boundary."""
    return delete_results_for_projects(
        project_ids,
        log,
        delete_project_func=delete_results_for_project,
    )


def delete_intermediate_runs_for_group_projects(
    project_ids: tuple[str, ...],
    log: CleanupLogger,
) -> int:
    """Delete intermediate runs for group projects through that boundary."""
    return delete_runs_for_canvases(
        project_ids,
        log,
        delete_canvas_func=delete_intermediate_runs_for_group_project,
    )


def delete_project_records_for_group(
    group_id: str,
    project_ids: tuple[str, ...],
    log: CleanupLogger,
) -> int:
    """Delete project-owned config rows and project rows for a group."""
    projects = 0
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

    return projects


def delete_projects_for_group(
    group_id: str,
    log: CleanupLogger,
) -> GroupProjectCleanupReport:
    """Delete projects and project-owned dependent artifacts for a group."""
    project_ids = _list_group_project_ids(group_id, log)
    if not project_ids:
        return GroupProjectCleanupReport()

    results = delete_results_for_group_projects(project_ids, log)
    intermediate_runs = delete_intermediate_runs_for_group_projects(project_ids, log)
    projects = delete_project_records_for_group(group_id, project_ids, log)

    return GroupProjectCleanupReport(
        projects=projects,
        results=results,
        intermediate_runs=intermediate_runs,
    )


def delete_projects_for_group_report(
    group_id: str,
    log: CleanupLogger,
) -> dict[str, int]:
    """Delete group projects and return public cleanup report key counts."""
    return delete_projects_for_group(group_id, log).as_report()


__all__ = [
    "GROUP_PROJECT_CLEANUP_REPORT_KEYS",
    "GroupProjectCleanupReport",
    "delete_comparison_keys_for_project",
    "delete_intermediate_runs_for_group_project",
    "delete_intermediate_runs_for_group_projects",
    "delete_project_records_for_group",
    "delete_projects_for_group",
    "delete_projects_for_group_report",
    "delete_results_for_group_projects",
    "list_existing_project_ids",
]
