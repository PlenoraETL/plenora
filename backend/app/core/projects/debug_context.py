"""Project-owned context helpers for debug payload construction."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.projects import db as project_db


@dataclass(frozen=True)
class ProjectDebugContext:
    """Project fields needed by debug payload utilities."""

    project_id: str
    project_name: str
    project_type: str
    triggered_by: str
    canvas: object


def load_project_debug_context(project_id: str) -> ProjectDebugContext | None:
    """Load persisted project context used to build a failed-run debug payload."""
    row = project_db.get_project_row(project_id)
    if row is None:
        return None
    return ProjectDebugContext(
        project_id=row.id,
        project_name=row.name,
        project_type=row.project_type,
        triggered_by="manual",
        canvas=row.canvas,
    )


__all__ = ["ProjectDebugContext", "load_project_debug_context"]
