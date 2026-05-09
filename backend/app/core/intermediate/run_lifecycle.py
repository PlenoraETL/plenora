"""Intermediate-owned run lifecycle boundary for worker tasks."""

from __future__ import annotations

from typing import Optional

from app.core.intermediate import db as _db
from app.core.intermediate.cleanup import close_worker_connection
from app.core.intermediate.writer import close_run


def get_default_runs_retention() -> int:
    """Return the intermediate default retention policy."""
    return _db.DEFAULT_RUNS_RETENTION


def create_run(
    *,
    run_id: str,
    canvas_id: str,
    group_id: str,
    total_nodes: int = 0,
    user_id: Optional[str] = None,
):
    """Create an intermediate run row."""
    return _db.create_run(
        run_id=run_id,
        canvas_id=canvas_id,
        group_id=group_id,
        total_nodes=total_nodes,
        user_id=user_id,
    )


__all__ = [
    "close_run",
    "close_worker_connection",
    "create_run",
    "get_default_runs_retention",
]
