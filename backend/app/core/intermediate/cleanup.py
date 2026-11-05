"""Lifecycle cleanup boundary for intermediate execution data."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from app.core.intermediate.retention import (
    delete_all_runs_for_canvas,
    list_canvas_ids_with_runs as _list_canvas_ids_with_runs,
    list_run_counts_by_canvas_group as _list_run_counts_by_canvas_group,
)
from app.db.pg_connection import close_connection as _close_connection


class CleanupLogger(Protocol):
    def warning(self, msg: str, *args, **kwargs) -> None: ...


def list_canvas_ids_with_runs() -> list[str]:
    """Return distinct canvas IDs that currently have intermediate runs."""
    return _list_canvas_ids_with_runs()


def list_run_counts_by_canvas_group() -> list[tuple[str, str, int]]:
    """(canvas_id, group_id, numero di run) dei canvas con run intermedie."""
    return _list_run_counts_by_canvas_group()


def delete_runs_for_canvas(canvas_id: str) -> int:
    """Delete all intermediate runs for a canvas/project."""
    return delete_all_runs_for_canvas(canvas_id)


def delete_runs_for_canvases(
    canvas_ids: Iterable[str],
    log: CleanupLogger,
    *,
    delete_canvas_func: Callable[[str], int] | None = None,
) -> int:
    """Delete intermediate runs for several canvases and return the total count."""
    delete_canvas = delete_canvas_func or delete_runs_for_canvas
    intermediate_runs = 0

    for canvas_id in canvas_ids:
        try:
            intermediate_runs += delete_canvas(canvas_id)
        except Exception as exc:
            log.warning(f"Cascade delete intermediate progetto {canvas_id}: {exc}")

    return intermediate_runs


def close_worker_connection() -> None:
    """Release the current worker thread's intermediate DB connection."""
    _close_connection()


__all__ = [
    "close_worker_connection",
    "delete_runs_for_canvas",
    "delete_runs_for_canvases",
    "list_canvas_ids_with_runs",
    "list_run_counts_by_canvas_group",
]
