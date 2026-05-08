"""Cleanup boundary for arricchisci-owned API connection rows."""

from __future__ import annotations

from app.db.cleanup_sql import delete_group_rows


_DELETE_API_CONNECTIONS_FOR_GROUP_SQL = "DELETE FROM api_connections WHERE group_id = ?"


def delete_api_connections_for_group(group_id: str) -> int:
    """Delete API connection configuration rows owned by a group."""
    return delete_group_rows(_DELETE_API_CONNECTIONS_FOR_GROUP_SQL, group_id)


__all__ = ["delete_api_connections_for_group"]
