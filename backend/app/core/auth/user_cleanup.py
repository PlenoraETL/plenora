"""Cleanup boundary for auth-user-owned rows."""

from __future__ import annotations

from app.db.group_cleanup import delete_group_rows_for_table


def delete_users_for_group(group_id: str) -> int:
    """Delete users owned by a group."""
    return delete_group_rows_for_table("users", group_id)


__all__ = ["delete_users_for_group"]
