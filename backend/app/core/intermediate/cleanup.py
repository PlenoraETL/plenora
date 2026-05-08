"""Lifecycle cleanup boundary for intermediate execution data."""

from __future__ import annotations

from app.core.intermediate.db import delete_all_runs_for_canvas


def delete_runs_for_canvas(canvas_id: str) -> int:
    """Delete all intermediate runs for a canvas/project."""
    return delete_all_runs_for_canvas(canvas_id)


__all__ = ["delete_runs_for_canvas"]
