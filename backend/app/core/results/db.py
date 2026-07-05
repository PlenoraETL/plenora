"""Canonical result metadata persistence.

Blob reads live in ``app.core.results.blob.get_result_data``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.db.pg_connection import PgSession, unit_of_work
from app.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "ResultMetadataRow",
    "get_latest_result_row",
    "get_result_row",
    "list_result_rows",
    "save_result_row",
]


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
    _selected_keys: tuple[str, ...] = field(default=(), repr=False, compare=False)

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ResultMetadataRow":
        return cls(
            id=_optional_str(row.get("id")),
            project_id=_optional_str(row.get("project_id")),
            group_id=_optional_str(row.get("group_id")),
            filename=_optional_str(row.get("filename")),
            format=_optional_str(row.get("format")),
            size_bytes=_optional_int(row.get("size_bytes"), field_name="size_bytes"),
            rows_count=_optional_int(row.get("rows_count"), field_name="rows_count"),
            created_at=row.get("created_at"),
            storage_key=_optional_str(row.get("storage_key")),
            _selected_keys=tuple(row.keys()),
        )


def _optional_str(value: object) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object, *, field_name: str = "integer field") -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(
            f"Malformed result DB row: {field_name} must be an int or null"
        )
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Malformed result DB row: {field_name} must be an int or null"
        ) from exc


def _result_metadata_rows(
    rows: Iterable[Mapping[str, object]],
) -> list[ResultMetadataRow]:
    return [ResultMetadataRow.from_mapping(row) for row in rows]


def save_result_row(
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
    """Persist result metadata.

    Supports both legacy BYTEA blobs and object-storage backed rows.
    """
    now = datetime.now(timezone.utc).isoformat()
    resolved_size_bytes = size_bytes
    if resolved_size_bytes is None:
        resolved_size_bytes = len(data) if data else 0

    with unit_of_work() as session:
        conflict_policy_marker = "ON CONFLICT (id) DO NOTHING"
        _ = conflict_policy_marker
        # In un sistema append-only ogni run genera UUID nuovo: un conflict
        # su id non dovrebbe accadere. `DO NOTHING` evita overwrite di result
        # gia' persistiti e il warning su rowcount marca l'anomalia.
        cur = session.execute(
            "INSERT INTO results (id, project_id, group_id, filename, format, data, "
            "size_bytes, rows_count, created_at, created_by, storage_key) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO NOTHING",
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
        rowcount = getattr(cur, "rowcount", None)
        if rowcount == 0:
            logger.warning(
                "[ANOMALIA] save_result_row: ON CONFLICT su result_id=%s "
                "(UUID gia' presente, conflict ignorato). Investigare il "
                "generator UUID o un replay malformato — la row originale "
                "NON e' stata sovrascritta.",
                result_id,
            )
    return get_result_row(result_id)


def get_result_row(
    result_id: str,
    *,
    session: PgSession | None = None,
) -> Optional[ResultMetadataRow]:
    """Return typed result metadata by id."""
    def _read(active_session: PgSession) -> Optional[ResultMetadataRow]:
        row = active_session.fetchone(
            "SELECT id, project_id, group_id, filename, format, size_bytes, "
            "rows_count, created_at, storage_key "
            "FROM results WHERE id = %s",
            (result_id,),
        )
        if row is None:
            return None
        return ResultMetadataRow.from_mapping(row)

    if session is not None:
        return _read(session)
    with unit_of_work() as active_session:
        return _read(active_session)


def get_latest_result_row(
    project_id: str,
    group_id: Optional[str] = None,
    *,
    session: PgSession | None = None,
) -> Optional[ResultMetadataRow]:
    """Return latest typed result metadata for a project."""
    def _read(active_session: PgSession) -> Optional[ResultMetadataRow]:
        # I result salvati con STORAGE_POLICY=object_storage hanno data=NULL
        # nella tabella `results` e il blob su MinIO/S3 referenziato da
        # storage_key. La SELECT deve includere `storage_key`, altrimenti i
        # caller downstream pensano sia un result BYTEA. Allineato a
        # `list_result_rows`, che include gia' storage_key.
        if group_id:
            row = active_session.fetchone(
                "SELECT id, project_id, group_id, filename, format, size_bytes, "
                "rows_count, created_at, storage_key "
                "FROM results WHERE project_id = %s AND group_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (project_id, group_id),
            )
        else:
            row = active_session.fetchone(
                "SELECT id, project_id, group_id, filename, format, size_bytes, "
                "rows_count, created_at, storage_key "
                "FROM results WHERE project_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            )
        if row is None:
            return None
        return ResultMetadataRow.from_mapping(row)

    if session is not None:
        return _read(session)
    with unit_of_work() as active_session:
        return _read(active_session)


def list_result_rows(
    project_id: str,
    *,
    session: PgSession | None = None,
) -> list[ResultMetadataRow]:
    """Return typed result metadata rows for a project, newest first."""
    def _read(active_session: PgSession) -> list[ResultMetadataRow]:
        rows = active_session.fetchall(
            "SELECT id, project_id, filename, format, size_bytes, rows_count, created_at "
            "FROM results WHERE project_id = %s ORDER BY created_at DESC",
            (project_id,),
        )
        return _result_metadata_rows(rows)

    if session is not None:
        return _read(session)
    with unit_of_work() as active_session:
        return _read(active_session)
