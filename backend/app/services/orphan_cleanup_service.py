"""Compatibility wrapper for result-owned orphan cleanup."""

from app.core.results.orphan_cleanup import (
    OrphanCleanupReport,
    cleanup_orphans,
    find_orphan_result_project_ids,
    find_orphan_run_canvas_ids,
)

__all__ = [
    "OrphanCleanupReport",
    "cleanup_orphans",
    "find_orphan_result_project_ids",
    "find_orphan_run_canvas_ids",
]
