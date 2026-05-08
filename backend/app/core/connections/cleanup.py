"""Cleanup boundary for connection-owned rows."""

from __future__ import annotations

from app.db.cleanup_sql import delete_group_rows


_DELETE_CONNECTIONS_FOR_GROUP_SQL = "DELETE FROM connections WHERE group_id = ?"


def delete_connections_for_group(group_id: str) -> int:
    """Delete database connection configuration rows owned by a group."""
    return delete_group_rows(_DELETE_CONNECTIONS_FOR_GROUP_SQL, group_id)


__all__ = ["delete_connections_for_group"]
