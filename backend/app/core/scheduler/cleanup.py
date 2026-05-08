"""Cleanup boundary for scheduler-owned rows."""

from __future__ import annotations

from app.db.pg_connection import execute


def _rowcount(cursor, fallback: int = 0) -> int:
    return cursor.rowcount if hasattr(cursor, "rowcount") else fallback


def delete_schedules_for_group(group_id: str) -> int:
    """Delete schedules owned by a group."""
    cur = execute("DELETE FROM schedules WHERE group_id = ?", (group_id,))
    return _rowcount(cur)


__all__ = ["delete_schedules_for_group"]
