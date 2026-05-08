"""Cleanup boundary for file-source-owned rows."""

from __future__ import annotations

from app.db.cleanup_sql import delete_group_rows


_DELETE_FILE_SOURCES_FOR_GROUP_SQL = "DELETE FROM file_sources WHERE group_id = ?"


def delete_file_sources_for_group(group_id: str) -> int:
    """Delete file-source rows owned by a group."""
    return delete_group_rows(_DELETE_FILE_SOURCES_FOR_GROUP_SQL, group_id)


__all__ = ["delete_file_sources_for_group"]
