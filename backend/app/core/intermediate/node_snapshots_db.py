"""Node snapshot persistence for intermediate runs.

Owns current `node_meta` writes and read-only compatibility for historical
`node_rows`. Runtime payloads are never written to PostgreSQL.
"""

from __future__ import annotations

import decimal

import json
from typing import Dict, List, Optional, TypeAlias

from app.db.pg_connection import unit_of_work

NodeMetaRow: TypeAlias = Dict[str, object]
NodeTableRow: TypeAlias = Dict[str, object]
SpecialFloatSentinel: TypeAlias = Dict[str, str]


def _fetchone(sql: str, params: tuple = ()) -> Optional[NodeMetaRow]:
    with unit_of_work() as session:
        return session.fetchone(sql, params)


def _fetchall(sql: str, params: tuple = ()) -> List[NodeMetaRow]:
    with unit_of_work() as session:
        return session.fetchall(sql, params)


def _execute(sql: str, params: tuple = ()):
    with unit_of_work() as session:
        return session.execute(sql, params)


def upsert_node_meta(
    run_id: str,
    node_id: str,
    node_type: str = None,
    row_count: int = None,
    col_schema: dict = None,
    exec_time: float = None,
    status: str = None,
    error_msg: str = None,
    *,
    execute_func=_execute,
) -> None:
    execute_func(
        """INSERT INTO node_meta
           (run_id, node_id, node_type, row_count, col_schema, exec_time, status, error_msg)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (run_id, node_id) DO UPDATE SET
           node_type=EXCLUDED.node_type, row_count=EXCLUDED.row_count,
           col_schema=EXCLUDED.col_schema, exec_time=EXCLUDED.exec_time,
           status=EXCLUDED.status, error_msg=EXCLUDED.error_msg""",
        (
            run_id,
            node_id,
            node_type,
            row_count,
            json.dumps(col_schema) if col_schema else None,
            exec_time,
            status,
            error_msg,
        ),
    )


_SQL_UPSERT_NODE_META_EXT = """
INSERT INTO node_meta (
    run_id, node_id, node_type, row_count, col_schema, exec_time, status, error_msg,
    node_hash, hash_algo, persistence_mode, persisted_rows_count,
    preview_truncated, artifact_uri, artifact_format, artifact_status, artifact_error
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (run_id, node_id) DO UPDATE SET
    node_type=EXCLUDED.node_type, row_count=EXCLUDED.row_count,
    col_schema=EXCLUDED.col_schema, exec_time=EXCLUDED.exec_time,
    status=EXCLUDED.status, error_msg=EXCLUDED.error_msg,
    node_hash=EXCLUDED.node_hash, hash_algo=EXCLUDED.hash_algo,
    persistence_mode=EXCLUDED.persistence_mode,
    persisted_rows_count=EXCLUDED.persisted_rows_count,
    preview_truncated=EXCLUDED.preview_truncated,
    artifact_uri=EXCLUDED.artifact_uri, artifact_format=EXCLUDED.artifact_format,
    artifact_status=EXCLUDED.artifact_status, artifact_error=EXCLUDED.artifact_error
"""


def upsert_node_meta_extended(
    run_id: str,
    node_id: str,
    *,
    node_type: Optional[str] = None,
    row_count: Optional[int] = None,
    col_schema: Optional[dict] = None,
    exec_time: Optional[float] = None,
    status: Optional[str] = None,
    error_msg: Optional[str] = None,
    node_hash: Optional[str] = None,
    hash_algo: Optional[str] = None,
    persistence_mode: Optional[str] = None,
    persisted_rows_count: Optional[int] = None,
    preview_truncated: Optional[bool] = None,
    artifact_uri: Optional[str] = None,
    artifact_format: Optional[str] = None,
    artifact_status: Optional[str] = None,
    artifact_error: Optional[str] = None,
    execute_func=_execute,
) -> None:
    """UPSERT node_meta with the current snapshot persistence schema."""
    execute_func(
        _SQL_UPSERT_NODE_META_EXT,
        (
            run_id,
            node_id,
            node_type,
            row_count,
            json.dumps(col_schema) if col_schema else None,
            exec_time,
            status,
            error_msg,
            node_hash,
            hash_algo,
            persistence_mode,
            persisted_rows_count,
            preview_truncated,
            artifact_uri,
            artifact_format,
            artifact_status,
            artifact_error,
        ),
    )


def write_node_snapshot_atomic(
    run_id: str,
    node_id: str,
    *,
    node_type: Optional[str] = None,
    rows_to_persist: Optional[List[Dict]] = None,
    col_schema: Optional[dict] = None,
    exec_time: Optional[float] = None,
    status: Optional[str] = None,
    error_msg: Optional[str] = None,
    row_count: Optional[int] = None,
    node_hash: Optional[str] = None,
    hash_algo: Optional[str] = None,
    persistence_mode: Optional[str] = None,
    preview_truncated: Optional[bool] = None,
    artifact_uri: Optional[str] = None,
    artifact_format: Optional[str] = None,
    artifact_status: Optional[str] = None,
    artifact_error: Optional[str] = None,
    transaction_func=None,
) -> None:
    """Atomically write metadata; payload rows are never stored in PG."""
    transaction_boundary = transaction_func or unit_of_work
    if rows_to_persist:
        raise ValueError(
            "write_node_snapshot_atomic non accetta payload rows: usa "
            "metadata_only o full_artifact"
        )
    persisted_rows_count = 0
    if row_count is None:
        row_count = persisted_rows_count

    with transaction_boundary() as conn:
        with conn.cursor() as cur:
            cur.execute(
                _SQL_UPSERT_NODE_META_EXT,
                (
                    run_id,
                    node_id,
                    node_type,
                    row_count,
                    json.dumps(col_schema) if col_schema else None,
                    exec_time,
                    status,
                    error_msg,
                    node_hash,
                    hash_algo,
                    persistence_mode,
                    persisted_rows_count,
                    preview_truncated,
                    artifact_uri,
                    artifact_format,
                    artifact_status,
                    artifact_error,
                ),
            )


def get_node_meta(
    run_id: str,
    node_id: str,
    *,
    fetchone_func=_fetchone,
) -> Optional[NodeMetaRow]:
    row = fetchone_func(
        "SELECT * FROM node_meta WHERE run_id = %s AND node_id = %s",
        (run_id, node_id),
    )
    if row and row.get("col_schema"):
        col_schema = row["col_schema"]
        # psycopg2 auto-deserializes JSONB after the custom typecaster removal.
        if not isinstance(col_schema, (dict, list)):
            row["col_schema"] = json.loads(col_schema)
    return row


def list_node_meta(run_id: str, *, fetchall_func=_fetchall) -> List[NodeMetaRow]:
    rows = fetchall_func("SELECT * FROM node_meta WHERE run_id = %s", (run_id,))
    for row in rows:
        if row.get("col_schema"):
            col_schema = row["col_schema"]
            if not isinstance(col_schema, (dict, list)):
                row["col_schema"] = json.loads(col_schema)
    return rows


_SPECIAL_FLOAT_KEY = "__plenora_special_float__"
_SPECIAL_FLOAT_TAG_NAN = "nan"
_SPECIAL_FLOAT_TAG_INF_POS = "+inf"
_SPECIAL_FLOAT_TAG_INF_NEG = "-inf"


def _make_special_float_sentinel(tag: str) -> SpecialFloatSentinel:
    return {_SPECIAL_FLOAT_KEY: tag}


def _to_jsonable(value):
    """Normalize a value for JSONB serialization."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()


    if isinstance(value, decimal.Decimal):
        return float(value)
    try:
        from shapely.geometry.base import BaseGeometry

        if isinstance(value, BaseGeometry):
            return value.wkt
    except ImportError:
        pass
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if value != value:
            return _make_special_float_sentinel(_SPECIAL_FLOAT_TAG_NAN)
        if value == float("inf"):
            return _make_special_float_sentinel(_SPECIAL_FLOAT_TAG_INF_POS)
        if value == float("-inf"):
            return _make_special_float_sentinel(_SPECIAL_FLOAT_TAG_INF_NEG)
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return f"bytes:{len(value)}"
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    return str(value)


def _from_jsonable_value(value):
    """Reverse `_to_jsonable` for NaN/+Inf/-Inf sentinel dicts."""
    if (
        isinstance(value, dict)
        and len(value) == 1
        and _SPECIAL_FLOAT_KEY in value
    ):
        tag = value[_SPECIAL_FLOAT_KEY]
        if tag == _SPECIAL_FLOAT_TAG_NAN:
            return float("nan")
        if tag == _SPECIAL_FLOAT_TAG_INF_POS:
            return float("inf")
        if tag == _SPECIAL_FLOAT_TAG_INF_NEG:
            return float("-inf")
    if isinstance(value, list):
        return [_from_jsonable_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _from_jsonable_value(item) for key, item in value.items()}
    return value


def insert_node_rows(
    run_id: str,
    node_id: str,
    rows: List[Dict],
    col_schema: dict,
    *,
    transaction_func=None,
) -> None:
    """Tombstone: PostgreSQL payload writes were removed in 2026."""
    raise RuntimeError(
        "insert_node_rows e' disabilitato: salva il payload su object storage"
    )


def write_node_atomic(
    run_id: str,
    node_id: str,
    *,
    node_type: Optional[str] = None,
    rows: Optional[List[Dict]] = None,
    col_schema: Optional[dict] = None,
    exec_time: Optional[float] = None,
    status: Optional[str] = None,
    error_msg: Optional[str] = None,
    transaction_func=None,
) -> None:
    """Tombstone: PostgreSQL payload writes were removed in 2026."""
    raise RuntimeError(
        "write_node_atomic e' disabilitato: usa metadata_only o full_artifact"
    )


def get_node_table_rows(
    run_id: str,
    node_id: str,
    group_id: Optional[str],
    limit: int = None,
    *,
    fetchone_func=_fetchone,
    fetchall_func=_fetchall,
) -> List[NodeTableRow]:
    """Read persisted node rows after verifying run ownership."""
    if group_id is None:
        run = fetchone_func("SELECT run_id FROM runs WHERE run_id = %s", (run_id,))
    else:
        run = fetchone_func(
            "SELECT run_id FROM runs WHERE run_id = %s AND group_id = %s",
            (run_id, group_id),
        )
    if not run:
        return []

    sql = (
        "SELECT row_hash, row_data FROM node_rows "
        "WHERE run_id = %s AND node_id = %s ORDER BY row_idx"
    )
    params: tuple = (run_id, node_id)
    if limit:
        sql += " LIMIT %s"
        params = params + (int(limit),)

    raw_rows = fetchall_func(sql, params)
    result = []
    for row in raw_rows:
        row_data = row.get("row_data") or {}
        if isinstance(row_data, str):
            row_data = json.loads(row_data)
        row_data = {
            key: _from_jsonable_value(value)
            for key, value in row_data.items()
        }
        result.append({"_row_hash": row.get("row_hash", ""), **row_data})
    return result


def list_node_row_hash_diff(
    *,
    source_run_id: str,
    compare_run_id: str,
    node_id: str,
    limit: int = 500,
    fetchall_func=_fetchall,
) -> List[NodeTableRow]:
    """Rows present in source run/node but absent from compare by row_hash."""
    return fetchall_func(
        """
        SELECT a.row_hash, a.row_data
        FROM node_rows a
        WHERE a.run_id = %s AND a.node_id = %s
          AND NOT EXISTS (
            SELECT 1 FROM node_rows b
            WHERE b.run_id = %s AND b.node_id = %s
              AND b.row_hash = a.row_hash
          )
        ORDER BY a.row_idx
        LIMIT %s
        """,
        (source_run_id, node_id, compare_run_id, node_id, int(limit)),
    )


def count_node_rows(
    run_id: str,
    node_id: str,
    *,
    fetchone_func=_fetchone,
) -> int:
    """Count persisted rows for one run/node."""
    row = fetchone_func(
        "SELECT COUNT(*) AS cnt FROM node_rows WHERE run_id = %s AND node_id = %s",
        (run_id, node_id),
    )
    return int(row["cnt"]) if row else 0
