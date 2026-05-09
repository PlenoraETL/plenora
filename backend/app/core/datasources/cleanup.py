"""Cleanup boundary for datasource-owned rows."""

from __future__ import annotations

from app.db.group_cleanup import delete_group_rows_for_table


def delete_datasources_for_group(group_id: str) -> int:
    """Delete datasource configuration rows owned by a group."""
    return delete_group_rows_for_table("datasources", group_id)


__all__ = ["delete_datasources_for_group"]
