"""Node snapshot persistence for intermediate runs.

Owns `node_meta`, `node_rows`, and JSONB normalization for persisted
intermediate node data. Runtime callers should use this leaf boundary
directly.
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
    """Atomically write `node_rows` plus extended `node_meta`."""
    transaction_boundary = transaction_func or unit_of_work
    records = _build_node_row_records(run_id, node_id, rows_to_persist or [])
    persisted_rows_count = len(records)
    if row_count is None:
        row_count = persisted_rows_count

    with transaction_boundary() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_DELETE_NODE_ROWS, (run_id, node_id))
            _bulk_insert_node_rows(cur, records)
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


_SQL_DELETE_NODE_ROWS = "DELETE FROM node_rows WHERE run_id = %s AND node_id = %s"
_SQL_INSERT_NODE_ROW = (
    "INSERT INTO node_rows (run_id, node_id, row_idx, row_hash, row_data) "
    "VALUES (%s, %s, %s, %s, %s::jsonb)"
)


def _bulk_insert_node_rows(cur, records: List[tuple]) -> None:
    """Bulk insert node_rows via psycopg3 executemany.

    Pre-fix usavamo `psycopg2.extras.execute_values` con template +
    VALUES %s, perche' execute_values univa N tuple in un singolo
    INSERT per ridurre i round-trip. psycopg3 ha rimosso
    execute_values: usiamo `cursor.executemany` su una INSERT
    parametrizzata, e psycopg3 internamente cache la prepared
    statement (default `prepare_threshold=5`), riducendo l'overhead
    di parsing dopo le prime esecuzioni. Per dataset molto grandi
    (>>10k righe) la migrazione successiva passera' a
    `cursor.copy(...)` (binary COPY), ma il default executemany
    e' gia' competitivo sul caso comune.
    """
    if not records:
        return
    cur.executemany(_SQL_INSERT_NODE_ROW, records)


_SQL_UPSERT_NODE_META = (
    "INSERT INTO node_meta "
    "(run_id, node_id, node_type, row_count, col_schema, exec_time, status, error_msg) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON CONFLICT (run_id, node_id) DO UPDATE SET "
    "node_type=EXCLUDED.node_type, row_count=EXCLUDED.row_count, "
    "col_schema=EXCLUDED.col_schema, exec_time=EXCLUDED.exec_time, "
    "status=EXCLUDED.status, error_msg=EXCLUDED.error_msg"
)


def _build_node_row_records(
    run_id: str,
    node_id: str,
    rows: List[Dict],
) -> List[tuple]:
    records: List[tuple] = []
    for idx, row in enumerate(rows or []):
        row_hash = row.get("_row_hash", "")
        row_data = {
            key: _to_jsonable(value)
            for key, value in row.items()
            if key != "_row_hash"
        }
        records.append(
            (
                run_id,
                node_id,
                idx,
                row_hash,
                json.dumps(row_data, ensure_ascii=False),
            )
        )
    return records


def insert_node_rows(
    run_id: str,
    node_id: str,
    rows: List[Dict],
    col_schema: dict,
    *,
    transaction_func=None,
) -> None:
    """Idempotently bulk insert node rows for one run/node pair."""
    transaction_boundary = transaction_func or unit_of_work
    records = _build_node_row_records(run_id, node_id, rows)

    with transaction_boundary() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_DELETE_NODE_ROWS, (run_id, node_id))
            _bulk_insert_node_rows(cur, records)


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
    """Atomically write legacy `node_rows` plus base `node_meta`."""
    transaction_boundary = transaction_func or unit_of_work
    records = _build_node_row_records(run_id, node_id, rows or [])
    meta_params = (
        run_id,
        node_id,
        node_type,
        len(records),
        json.dumps(col_schema) if col_schema else None,
        exec_time,
        status,
        error_msg,
    )

    with transaction_boundary() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_DELETE_NODE_ROWS, (run_id, node_id))
            _bulk_insert_node_rows(cur, records)
            cur.execute(_SQL_UPSERT_NODE_META, meta_params)


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
