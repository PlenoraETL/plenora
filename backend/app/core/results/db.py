"""Canonical bridge for result metadata persistence.

Blob reads intentionally stay in ``app.db.results_db_service.get_result_data``
until the Resource Engine compatibility surface is retired.
"""

from __future__ import annotations

from typing import Optional

from app.db import results_db_service as _results_db


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
) -> dict[str, object]:
    """Persist result metadata through the legacy SQL implementation."""
    return _results_db.save_result(
        result_id=result_id,
        project_id=project_id,
        group_id=group_id,
        filename=filename,
        fmt=fmt,
        data=data,
        rows_count=rows_count,
        created_by=created_by,
        storage_key=storage_key,
        size_bytes=size_bytes,
    )


def get_result(result_id: str) -> Optional[dict[str, object]]:
    """Return result metadata by id."""
    return _results_db.get_result(result_id)


def get_latest_result(
    project_id: str,
    group_id: Optional[str] = None,
) -> Optional[dict[str, object]]:
    """Return latest result metadata for a project."""
    if group_id is None:
        return _results_db.get_latest_result(project_id)
    return _results_db.get_latest_result(project_id, group_id)


def list_results(project_id: str) -> list[dict[str, object]]:
    """Return result metadata rows for a project, newest first."""
    return _results_db.list_results(project_id)
