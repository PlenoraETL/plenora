"""Cleanup boundary for auth-user-owned rows."""

from __future__ import annotations

from app.db.cleanup_sql import delete_group_rows


_DELETE_USERS_FOR_GROUP_SQL = "DELETE FROM users WHERE group_id = ?"


def delete_users_for_group(group_id: str) -> int:
    """Delete users owned by a group."""
    return delete_group_rows(_DELETE_USERS_FOR_GROUP_SQL, group_id)


__all__ = ["delete_users_for_group"]
