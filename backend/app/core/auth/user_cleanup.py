"""Cleanup boundary for auth-user-owned rows."""

from __future__ import annotations

from app.db.pg_connection import execute


def _rowcount(cursor, fallback: int = 0) -> int:
    return cursor.rowcount if hasattr(cursor, "rowcount") else fallback


def delete_users_for_group(group_id: str) -> int:
    """Delete users owned by a group."""
    cur = execute("DELETE FROM users WHERE group_id = ?", (group_id,))
    return _rowcount(cur)


__all__ = ["delete_users_for_group"]
