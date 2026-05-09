"""Project-owned retention policy helpers."""

from __future__ import annotations

from app.core.projects.db import get_project_retention_n
from app.utils.logger import get_logger

logger = get_logger(__name__)


def resolve_runs_retention_n(project_id: str, *, default: int) -> int:
    """Return the project retention policy, falling back to the supplied default."""
    try:
        retention_n = get_project_retention_n(project_id)
        if retention_n is None:
            return default
        return retention_n
    except Exception as exc:
        logger.warning(
            "runs retention policy lookup failed for %s, using default: %s",
            project_id,
            exc,
            op="projects_retention",
        )
        return default
