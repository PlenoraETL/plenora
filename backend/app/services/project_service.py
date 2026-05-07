"""KEEP_COMPAT wrapper for the project domain service.

Canonical implementation lives in `app.core.projects.service`.
"""

from app.core.projects.service import ProjectService, RawProjectCanvas

__all__ = ["ProjectService", "RawProjectCanvas"]
