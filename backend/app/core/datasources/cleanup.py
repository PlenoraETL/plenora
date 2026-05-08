"""Cleanup boundary for datasource-owned rows."""

from __future__ import annotations

from app.db.cleanup_sql import delete_group_rows


_DELETE_DATASOURCES_FOR_GROUP_SQL = "DELETE FROM datasources WHERE group_id = ?"


def delete_datasources_for_group(group_id: str) -> int:
    """Delete datasource configuration rows owned by a group."""
    return delete_group_rows(_DELETE_DATASOURCES_FOR_GROUP_SQL, group_id)


__all__ = ["delete_datasources_for_group"]
