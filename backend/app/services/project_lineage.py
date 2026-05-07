"""KEEP_COMPAT wrapper for project lineage cycle detection.

Canonical implementation lives in `app.core.projects.lineage`.
"""

from app.core.projects.lineage import (
    ProjectLineageCycleError,
    detect_project_lineage_cycle,
)

__all__ = ["ProjectLineageCycleError", "detect_project_lineage_cycle"]
