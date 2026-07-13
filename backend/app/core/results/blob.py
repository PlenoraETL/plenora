"""Canonical result blob read boundary.

Result metadata SQL lives in :mod:`app.core.results.db`; delete/cleanup lives
in :mod:`app.core.results.cleanup`. Blob reads delegate directly to the
Resource Engine dispatch.
"""

from __future__ import annotations

from typing import Iterator, Optional, TypedDict

from app.core.resources.loaders.result_blob import _dispatch_by_id
from app.core.results import db as results_db


class ResultStream(TypedDict):
    body: object
    size: int
    filename: str
    format: str
    backend: str


def get_result_data(result_id: str, group_id: Optional[str] = None) -> Optional[bytes]:
    """Return result blob bytes, preserving the public read contract.

    ``None`` means no visible row or unavailable object-storage payload. When
    ``group_id`` is provided, the Resource Engine applies the tenant SQL filter;
    omitting it preserves existing internal caller behavior.
    """
    data, _backend = _dispatch_by_id(
        result_id,
        group_id_filter=group_id or None,
    )
    return data


def open_result_stream(
    result_id: str,
    *,
    expected_project_id: Optional[str] = None,
    expected_group_id: Optional[str] = None,
) -> Optional[ResultStream]:
    """Open a result for HTTP streaming without materializing S3 bytes.

    Object-storage rows return the provider's streaming iterator directly.
    Legacy BYTEA rows keep the bounded compatibility path until migration.
    """
    row = results_db.get_result_row(result_id)
    if row is None:
        return None
    if expected_project_id and row.project_id != expected_project_id:
        return None
    if expected_group_id and row.group_id != expected_group_id:
        return None

    filename = row.filename or f"{result_id}.{row.format or 'bin'}"
    fmt = row.format or "bin"
    if row.storage_key:
        from app.core.io.object_storage import get_runtime_object_storage

        stream = get_runtime_object_storage().get_object_stream(row.storage_key)
        return {
            "body": stream["body"],
            "size": int(stream.get("size") or row.size_bytes or 0),
            "filename": filename,
            "format": fmt,
            "backend": "object_storage",
        }

    data = get_result_data(result_id, group_id=expected_group_id)
    if data is None:
        return None
    chunk_size = 1 << 20

    def _legacy_chunks() -> Iterator[bytes]:
        for start in range(0, len(data), chunk_size):
            yield data[start : start + chunk_size]

    return {
        "body": _legacy_chunks(),
        "size": len(data),
        "filename": filename,
        "format": fmt,
        "backend": "db_legacy",
    }


__all__ = ["ResultStream", "get_result_data", "open_result_stream"]
