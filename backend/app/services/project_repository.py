"""KEEP_COMPAT wrapper for the project repository contract.

Canonical implementation lives in `app.core.projects.repository`.
"""

from app.core.projects.repository import (
    DbProjectCanvasKeyRepository,
    ProjectCanvasKeyRepository,
)

__all__ = ["DbProjectCanvasKeyRepository", "ProjectCanvasKeyRepository"]
