"""Project-owned context loaders for schema inference."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.core.projects import db as project_db


@dataclass(frozen=True)
class ProjectSourceSchemaContext:
    """Persisted project fields needed to infer a project_source node."""

    project_id: str
    output_contract: Any


def load_project_source_schema_context(
    project_id: str,
) -> Optional[ProjectSourceSchemaContext]:
    """Load the source project's persisted output contract for schema inference.

    This is the explicit DB boundary for project_source schema inference. The
    inference service consumes the public project-to-project contract, not
    upstream canvas editor state.
    """
    row = project_db.get_project_row(project_id)
    if row is None:
        return None
    return ProjectSourceSchemaContext(
        project_id=row.id,
        output_contract=row.output_contract,
    )
