"""Cleanup boundary for file-source-owned rows."""

from __future__ import annotations

from app.core.files import db as files_db


def delete_file_sources_for_group(group_id: str) -> int:
    """Delete file-source rows owned by a group through storage-aware cleanup."""
    deleted_count = 0
    for file_id in files_db.list_file_source_ids_for_group(group_id):
        if files_db.delete_file_source(file_id):
            deleted_count += 1
    return deleted_count


__all__ = ["delete_file_sources_for_group"]
