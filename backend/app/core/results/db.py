"""Canonical result metadata persistence.

Blob reads live in ``app.core.results.blob.get_result_data``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Optional

from app.db.pg_connection import execute, fetchall, fetchone


@dataclass(frozen=True)
class ResultMetadataRow:
    """Read-only metadata row for persisted results.

    Public legacy functions still return dicts; use this row inside core code
    when the result metadata shape matters.
    """

    id: Optional[str]
    project_id: Optional[str] = None
    group_id: Optional[str] = None
    filename: Optional[str] = None
    format: Optional[str] = None
    size_bytes: Optional[int] = None
    rows_count: Optional[int] = None
    created_at: object = None
    storage_key: Optional[str] = None
    _selected_keys: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ResultMetadataRow":
        return cls(
            id=_optional_str(row.get("id")),
            project_id=_optional_str(row.get("project_id")),
            group_id=_optional_str(row.get("group_id")),
            filename=_optional_str(row.get("filename")),
            format=_optional_str(row.get("format")),
            size_bytes=_optional_int(row.get("size_bytes")),
            rows_count=_optional_int(row.get("rows_count")),
            created_at=row.get("created_at"),
            storage_key=_optional_str(row.get("storage_key")),
            _selected_keys=tuple(row.keys()),
        )

    def to_legacy_dict(self) -> dict[str, object]:
        values: dict[str, object] = {
            "id": self.id,
            "project_id": self.project_id,
            "group_id": self.group_id,
            "filename": self.filename,
            "format": self.format,
            "size_bytes": self.size_bytes,
            "rows_count": self.rows_count,
            "created_at": self.created_at,
            "storage_key": self.storage_key,
        }
        return {key: values[key] for key in self._selected_keys if key in values}


def _optional_str(value: object) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> Optional[int]:
    if value is None:
        return None
    return int(value)


def save_result(
    result_id: str,
    project_id: str,
    group_id: str,
    filename: str,
    fmt: str,
    data: Optional[bytes] = None,
    rows_count: Optional[int] = None,
    created_by: Optional[str] = None,
    storage_key: Optional[str] = None,
    size_bytes: Optional[int] = None,
) -> Optional[dict[str, object]]:
    """Persist result metadata.

    Supports both legacy BYTEA blobs and object-storage backed rows.
    """
    now = datetime.now(timezone.utc).isoformat()
    resolved_size_bytes = size_bytes
    if resolved_size_bytes is None:
        resolved_size_bytes = len(data) if data else 0

    execute(
        "INSERT INTO results (id, project_id, group_id, filename, format, data, "
        "size_bytes, rows_count, created_at, created_by, storage_key) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data, size_bytes=EXCLUDED.size_bytes, "
        "rows_count=EXCLUDED.rows_count, created_at=EXCLUDED.created_at, "
        "storage_key=EXCLUDED.storage_key",
        (
            result_id,
            project_id,
            group_id,
            filename,
            fmt,
            data,
            resolved_size_bytes,
            rows_count,
            now,
            created_by,
            storage_key,
        ),
    )
    return get_result(result_id)


def get_result_row(result_id: str) -> Optional[ResultMetadataRow]:
    """Return typed result metadata by id."""
    row = fetchone(
        "SELECT id, project_id, group_id, filename, format, size_bytes, "
        "rows_count, created_at, storage_key "
        "FROM results WHERE id = ?",
        (result_id,),
    )
    if row is None:
        return None
    return ResultMetadataRow.from_mapping(row)


def get_result(result_id: str) -> Optional[dict[str, object]]:
    """Return result metadata by id as legacy dict."""
    row = get_result_row(result_id)
    if row is None:
        return None
    return row.to_legacy_dict()


def get_latest_result_row(
    project_id: str,
    group_id: Optional[str] = None,
) -> Optional[ResultMetadataRow]:
    """Return latest typed result metadata for a project."""
    if group_id:
        row = fetchone(
            "SELECT id, project_id, group_id, filename, format, size_bytes, rows_count, created_at "
            "FROM results WHERE project_id = ? AND group_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id, group_id),
        )
    else:
        row = fetchone(
            "SELECT id, project_id, group_id, filename, format, size_bytes, rows_count, created_at "
            "FROM results WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        )
    if row is None:
        return None
    return ResultMetadataRow.from_mapping(row)


def get_latest_result(
    project_id: str,
    group_id: Optional[str] = None,
) -> Optional[dict[str, object]]:
    """Return latest result metadata for a project as legacy dict."""
    row = get_latest_result_row(project_id, group_id=group_id)
    if row is None:
        return None
    return row.to_legacy_dict()


def list_result_rows(project_id: str) -> list[ResultMetadataRow]:
    """Return typed result metadata rows for a project, newest first."""
    rows = fetchall(
        "SELECT id, project_id, filename, format, size_bytes, rows_count, created_at "
        "FROM results WHERE project_id = ? ORDER BY created_at DESC",
        (project_id,),
    )
    return [ResultMetadataRow.from_mapping(row) for row in rows]


def list_results(project_id: str) -> list[dict[str, object]]:
    """Return result metadata rows for a project as legacy dicts."""
    return [row.to_legacy_dict() for row in list_result_rows(project_id)]
