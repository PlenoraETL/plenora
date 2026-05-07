"""KEEP_COMPAT wrapper for project-domain policies.

Canonical implementation lives in `app.core.projects.domain`.
"""

from app.core.projects.domain import build_project_id, resolve_project_group_id

__all__ = ["build_project_id", "resolve_project_group_id"]
