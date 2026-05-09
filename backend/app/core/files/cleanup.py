"""Cleanup boundary for file-source-owned rows."""

from __future__ import annotations

from app.db.group_cleanup import delete_group_rows_for_table


def delete_file_sources_for_group(group_id: str) -> int:
    """Delete file-source rows owned by a group."""
    return delete_group_rows_for_table("file_sources", group_id)


__all__ = ["delete_file_sources_for_group"]
