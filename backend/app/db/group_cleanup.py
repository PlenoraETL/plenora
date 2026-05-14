"""Table-safe helpers for group-owned row cleanup."""

from __future__ import annotations

from typing import Literal

from app.db import pg_connection
from app.db.cleanup_sql import rowcount


GroupCleanupTable = Literal[
    "api_connections",
    "connections",
    "datasources",
    "users",
]

_DELETE_GROUP_ROWS_SQL: dict[GroupCleanupTable, str] = {
    "api_connections": "DELETE FROM api_connections WHERE group_id = %s",
    "connections": "DELETE FROM connections WHERE group_id = %s",
    "datasources": "DELETE FROM datasources WHERE group_id = %s",
    "users": "DELETE FROM users WHERE group_id = %s",
}


def delete_group_rows_for_table(table: GroupCleanupTable, group_id: str) -> int:
    """Delete group-owned rows from an allowed cleanup table."""
    sql = _DELETE_GROUP_ROWS_SQL[table]
    return rowcount(pg_connection.execute(sql, (group_id,)))


__all__ = ["GroupCleanupTable", "delete_group_rows_for_table"]
