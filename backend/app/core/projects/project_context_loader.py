"""Project-owned context loaders for schema inference."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.core.projects import db as project_db


@dataclass(frozen=True)
class ProjectSourceSchemaContext:
    """Persisted project fields needed to infer a project_source node."""

    project_id: str
    canvas: Any


def load_project_source_schema_context(
    project_id: str,
) -> Optional[ProjectSourceSchemaContext]:
    """Load the source project's persisted canvas for schema inference.

    This is the explicit DB boundary for project_source schema inference.
    The inference service only needs canvas data, not a serialized project
    detail payload or direct access to the projects repository.
    """
    row = project_db.get_project_row(project_id)
    if row is None:
        return None
    return ProjectSourceSchemaContext(project_id=row.id, canvas=row.canvas or {})
