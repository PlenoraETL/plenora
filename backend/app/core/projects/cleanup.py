"""Lifecycle cleanup boundary for project-owned config artifacts."""

from __future__ import annotations

from app.core.projects import db as project_db


def delete_comparison_keys_for_project(project_id: str) -> None:
    """Delete comparison-key config rows associated with a project canvas."""
    project_db.delete_comparison_keys(project_id)


def list_existing_project_ids() -> set[str]:
    """Return all persisted project IDs for cleanup/orphan reconciliation."""
    return {row.id for row in project_db.list_projects()}


__all__ = ["delete_comparison_keys_for_project", "list_existing_project_ids"]
