"""Cleanup boundary for arricchisci-owned API connection rows."""

from __future__ import annotations

from app.db.group_cleanup import delete_group_rows_for_table


def delete_api_connections_for_group(group_id: str) -> int:
    """Delete API connection configuration rows owned by a group."""
    return delete_group_rows_for_table("api_connections", group_id)


__all__ = ["delete_api_connections_for_group"]
