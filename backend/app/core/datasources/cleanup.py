"""Cleanup boundary for datasource-owned rows."""

from __future__ import annotations

from app.db.pg_connection import execute


def _rowcount(cursor, fallback: int = 0) -> int:
    return cursor.rowcount if hasattr(cursor, "rowcount") else fallback


def delete_datasources_for_group(group_id: str) -> int:
    """Delete datasource configuration rows owned by a group."""
    cur = execute("DELETE FROM datasources WHERE group_id = ?", (group_id,))
    return _rowcount(cur)


__all__ = ["delete_datasources_for_group"]
