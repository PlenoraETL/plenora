"""Narrow DB boundary for artifact resolver metadata lookups."""

from __future__ import annotations

from typing import Any


def get_project_for_artifact(project_id: str, *, group_id: str | None = None) -> Any:
    """Read project visibility metadata for latest-artifact resolution."""
    from app.core.projects import db as projects_db

    if group_id is None:
        return projects_db.get_project(project_id)
    return projects_db.get_project(project_id, group_id=group_id)


def get_latest_result_row_for_artifact(project_id: str) -> Any:
    """Read the latest result metadata row for a project artifact."""
    from app.core.results import db as results_db

    return results_db.get_latest_result_row(project_id)


def get_result_row_for_artifact(result_id: str) -> Any:
    """Read result metadata used before result-blob materialization."""
    from app.core.results import db as results_db

    return results_db.get_result_row(result_id)
