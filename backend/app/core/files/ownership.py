"""File-source-owned ownership access helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, cast

from app.core.files import db as files_db


@dataclass(frozen=True)
class FileOwnerContext:
    file_id: str
    group_id: Optional[str]
    created_by: Optional[str]

    def ownership_resource(self) -> dict[str, object]:
        return {
            "id": self.file_id,
            "group_id": self.group_id,
            "created_by": self.created_by,
        }


def load_file_owner_context(file_id: str) -> Optional[FileOwnerContext]:
    """Load file-source ownership fields for auth owner dependencies."""
    row = files_db.get_file_source(file_id)
    if row is None:
        return None
    return FileOwnerContext(
        file_id=cast(str, row["id"]),
        group_id=cast(Optional[str], row.get("group_id")),
        created_by=cast(Optional[str], row.get("created_by")),
    )


__all__ = [
    "FileOwnerContext",
    "load_file_owner_context",
]
