"""Cleanup boundary for connection-owned rows."""

from __future__ import annotations

from app.db.group_cleanup import delete_group_rows_for_table


def delete_connections_for_group(group_id: str) -> int:
    """Delete database connection configuration rows owned by a group."""
    return delete_group_rows_for_table("connections", group_id)


__all__ = ["delete_connections_for_group"]
