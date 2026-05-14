"""
Intermediate DB compatibility facade.

Runtime code should import the explicit intermediate leaf modules
(`runs_db`, `node_snapshots_db`, `retention`, `cleanup`, `init`) instead. This
module remains for tests and old external imports that still patch the
historical `app.core.intermediate.db` surface.
"""

from typing import Any, Dict, List, Optional

from psycopg2.extras import execute_values

from app.core.intermediate import runs_db as _runs_db
from app.core.intermediate import node_snapshots_db as _node_snapshots_db
from app.core.intermediate import retention as _retention
from app.core.intermediate import init as _init
from app.utils.logger import get_logger

# Import from pg_connection (same pool used by config/results).
from app.db.pg_connection import (
    fetchone, fetchall, execute, close_connection as _pg_close, transaction,
)

logger = get_logger(__name__)

__all__ = [
    "DEFAULT_RUNS_RETENTION",
    "apply_runs_retention",
    "close_connection",
    "close_worker_connection",
    "create_run",
    "delete_all_runs_for_canvas",
    "get_node_meta",
    "get_node_table_rows",
    "get_run",
    "init_db",
    "insert_node_rows",
    "list_node_meta",
    "list_runs",
    "pin_run",
    "update_run",
    "upsert_node_meta",
    "upsert_node_meta_extended",
    "write_node_atomic",
    "write_node_snapshot_atomic",
]


def close_connection() -> None:
    """No-op per PostgreSQL: il pool gestisce le connessioni.

    Kept for Celery tasks that call close_connection in finally.
    """
    _pg_close()


def close_worker_connection() -> None:
    """Release the current worker thread's intermediate DB connection."""
    close_connection()


def init_db():
    """Compatibility wrapper for the explicit intermediate init boundary."""
    _init.init_db()


# fetchone, fetchall, execute are imported from pg_connection above.

# RUNS

DEFAULT_RUNS_RETENTION = _retention.DEFAULT_RUNS_RETENTION


def create_run(run_id: str, canvas_id: str, group_id: str,
               total_nodes: int = 0,
               user_id: Optional[str] = None) -> Optional[Dict]:
    """Compatibility facade for run creation."""
    return _run_row_to_dict(
        _runs_db.create_run(
            run_id,
            canvas_id,
            group_id,
            total_nodes=total_nodes,
            user_id=user_id,
            execute_func=execute,
            get_run_func=get_run,
        )
    )


def get_run(run_id: str) -> Optional[Dict]:
    return _run_row_to_dict(_runs_db.get_run(run_id, fetchone_func=fetchone))


def list_runs(canvas_id: str, group_id: str, limit: int = 50) -> List[Dict]:
    return [
        _run_row_to_dict(row)
        for row in _runs_db.list_runs(
            canvas_id,
            group_id,
            limit=limit,
            fetchall_func=fetchall,
        )
    ]


def _run_row_to_dict(row: Any) -> Optional[Dict]:
    if row is None:
        return None
    if isinstance(row, _runs_db.RunRow):
        return row.as_dict()
    return row


def update_run(run_id: str, **fields) -> None:
    return _runs_db.update_run(run_id, execute_func=execute, **fields)


def pin_run(run_id: str, group_id: Optional[str], pinned: bool = True) -> bool:
    return _runs_db.pin_run(
        run_id,
        group_id,
        pinned=pinned,
        execute_func=execute,
    )

# NODE SNAPSHOTS
#
# Node meta/row persistence lives in node_snapshots_db. These explicit facade
# functions keep the historical app.core.intermediate.db surface stable while
# the intermediate monolith is split.

_to_jsonable = _node_snapshots_db._to_jsonable
_from_jsonable_value = _node_snapshots_db._from_jsonable_value
_SPECIAL_FLOAT_KEY = _node_snapshots_db._SPECIAL_FLOAT_KEY


def upsert_node_meta(run_id: str, node_id: str, node_type: str = None,
                     row_count: int = None, col_schema: dict = None,
                     exec_time: float = None, status: str = None,
                     error_msg: str = None) -> None:
    return _node_snapshots_db.upsert_node_meta(
        run_id,
        node_id,
        node_type=node_type,
        row_count=row_count,
        col_schema=col_schema,
        exec_time=exec_time,
        status=status,
        error_msg=error_msg,
        execute_func=execute,
    )


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
) -> None:
    return _node_snapshots_db.upsert_node_meta_extended(
        run_id,
        node_id,
        node_type=node_type,
        row_count=row_count,
        col_schema=col_schema,
        exec_time=exec_time,
        status=status,
        error_msg=error_msg,
        node_hash=node_hash,
        hash_algo=hash_algo,
        persistence_mode=persistence_mode,
        persisted_rows_count=persisted_rows_count,
        preview_truncated=preview_truncated,
        artifact_uri=artifact_uri,
        artifact_format=artifact_format,
        artifact_status=artifact_status,
        artifact_error=artifact_error,
        execute_func=execute,
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
) -> None:
    return _node_snapshots_db.write_node_snapshot_atomic(
        run_id,
        node_id,
        node_type=node_type,
        rows_to_persist=rows_to_persist,
        col_schema=col_schema,
        exec_time=exec_time,
        status=status,
        error_msg=error_msg,
        row_count=row_count,
        node_hash=node_hash,
        hash_algo=hash_algo,
        persistence_mode=persistence_mode,
        preview_truncated=preview_truncated,
        artifact_uri=artifact_uri,
        artifact_format=artifact_format,
        artifact_status=artifact_status,
        artifact_error=artifact_error,
        transaction_func=transaction,
        execute_values_func=execute_values,
    )


def get_node_meta(run_id: str, node_id: str) -> Optional[Dict]:
    return _node_snapshots_db.get_node_meta(
        run_id,
        node_id,
        fetchone_func=fetchone,
    )


def list_node_meta(run_id: str) -> List[Dict]:
    return _node_snapshots_db.list_node_meta(run_id, fetchall_func=fetchall)


def insert_node_rows(
    run_id: str,
    node_id: str,
    rows: List[Dict],
    col_schema: dict,
) -> None:
    return _node_snapshots_db.insert_node_rows(
        run_id,
        node_id,
        rows,
        col_schema,
        transaction_func=transaction,
        execute_values_func=execute_values,
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
) -> None:
    return _node_snapshots_db.write_node_atomic(
        run_id,
        node_id,
        node_type=node_type,
        rows=rows,
        col_schema=col_schema,
        exec_time=exec_time,
        status=status,
        error_msg=error_msg,
        transaction_func=transaction,
        execute_values_func=execute_values,
    )


def get_node_table_rows(
    run_id: str,
    node_id: str,
    group_id: Optional[str],
    limit: int = None,
) -> List[Dict]:
    return _node_snapshots_db.get_node_table_rows(
        run_id,
        node_id,
        group_id,
        limit=limit,
        fetchone_func=fetchone,
        fetchall_func=fetchall,
    )

# RETENTION POLICY


def apply_runs_retention(
    canvas_id: str,
    group_id: str,
    retention_n: int | None = None,
) -> None:
    """Compatibility facade for run retention."""
    return _retention.apply_runs_retention(
        canvas_id,
        group_id,
        retention_n=retention_n,
        list_runs_func=list_runs,
        delete_run_func=_delete_run,
        logger_obj=logger,
    )


def _delete_run(run_id: str) -> None:
    """Compatibility facade for deleting one intermediate run."""
    return _retention._delete_run(
        run_id,
        fetchall_func=fetchall,
        transaction_func=transaction,
        logger_obj=logger,
    )


def delete_all_runs_for_canvas(canvas_id: str) -> int:
    """Compatibility facade for deleting all runs of a canvas."""
    return _retention.delete_all_runs_for_canvas(
        canvas_id,
        fetchall_func=fetchall,
        delete_run_func=_delete_run,
        logger_obj=logger,
    )
