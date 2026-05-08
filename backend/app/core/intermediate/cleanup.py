"""Lifecycle cleanup boundary for intermediate execution data."""

from __future__ import annotations

from app.core.intermediate.retention import delete_all_runs_for_canvas
from app.db.pg_connection import close_connection as _close_connection, fetchall


def list_canvas_ids_with_runs() -> list[str]:
    """Return distinct canvas IDs that currently have intermediate runs."""
    rows = fetchall("SELECT DISTINCT canvas_id FROM runs")
    canvas_ids: list[str] = []
    for row in rows:
        canvas_id = row["canvas_id"] if isinstance(row, dict) else row[0]
        if canvas_id:
            canvas_ids.append(canvas_id)
    return canvas_ids


def delete_runs_for_canvas(canvas_id: str) -> int:
    """Delete all intermediate runs for a canvas/project."""
    return delete_all_runs_for_canvas(canvas_id)


def close_worker_connection() -> None:
    """Release the current worker thread's intermediate DB connection."""
    _close_connection()


__all__ = [
    "close_worker_connection",
    "delete_runs_for_canvas",
    "list_canvas_ids_with_runs",
]
