"""Arricchisci project access boundary.

Keeps arricchisci code away from direct project DB helpers while exposing the
small tenant-binding shape needed during enrichment execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.projects.access_context import (
    load_project_access_context as _load_project_access_context,
)


@dataclass(frozen=True)
class ArricchisciProjectAccessContext:
    project_id: str
    group_id: Optional[str]


def load_project_access_context(
    project_id: str,
) -> Optional[ArricchisciProjectAccessContext]:
    """Load the project tenant binding through the projects access boundary."""
    project_ctx = _load_project_access_context(project_id)
    if project_ctx is None:
        return None
    return ArricchisciProjectAccessContext(
        project_id=project_ctx.project_id,
        group_id=project_ctx.group_id,
    )
