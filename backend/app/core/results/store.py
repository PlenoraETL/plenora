"""Result-owned persistence boundary used by legacy result-store callers."""

from __future__ import annotations

from typing import Optional

from app.core.results import db as results_db

ResultMetadataRow = results_db.ResultMetadataRow


def resolve_group_id_for_result(
    project_id: str,
    group_id: Optional[str] = None,
) -> str:
    """Return the explicit group id or the owning project's group fallback."""
    if group_id:
        return group_id

    from app.config import settings
    from app.core.projects.runtime_context import resolve_project_runtime_group_id

    return resolve_project_runtime_group_id(
        project_id,
        default_group=settings.DEFAULT_GROUP,
    )


def save_result_row(
    *,
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
) -> Optional[ResultMetadataRow]:
    """Persist result metadata through the results domain boundary."""
    return results_db.save_result_row(
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


def save_result(
    *,
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
    """Persist result metadata through the results domain boundary."""
    return results_db.save_result(
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


def list_result_rows(project_id: str) -> list[ResultMetadataRow]:
    """Return result metadata rows for a project, newest first."""
    return results_db.list_result_rows(project_id)


def get_latest_result_row(
    project_id: str,
    group_id: Optional[str] = None,
) -> Optional[ResultMetadataRow]:
    """Return latest typed result metadata for a project."""
    return results_db.get_latest_result_row(project_id, group_id=group_id)


def get_latest_result(
    project_id: str,
    group_id: Optional[str] = None,
) -> Optional[dict[str, object]]:
    """Return latest result metadata for a project as a legacy dict."""
    return results_db.get_latest_result(project_id, group_id=group_id)


def list_results(project_id: str) -> list[dict[str, object]]:
    """Return result metadata rows for a project as legacy dicts."""
    return results_db.list_results(project_id)


def get_result_data(result_id: str, group_id: Optional[str] = None) -> Optional[bytes]:
    """Return result blob bytes for a result id."""
    from app.core.results.blob import get_result_data as _get_result_data

    if group_id is None:
        return _get_result_data(result_id)
    return _get_result_data(result_id, group_id=group_id)


__all__ = [
    "ResultMetadataRow",
    "get_latest_result",
    "get_latest_result_row",
    "get_result_data",
    "list_result_rows",
    "list_results",
    "resolve_group_id_for_result",
    "save_result",
    "save_result_row",
]
