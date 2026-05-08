"""Run persistence boundary for intermediate executions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from app.db.pg_connection import execute, fetchall, fetchone, transaction
from app.utils.logger import get_logger


logger = get_logger(__name__)

RunRow = Dict
FetchAll = Callable[[str, tuple], List[Dict]]
FetchOne = Callable[[str, tuple], Optional[Dict]]
Execute = Callable[[str, tuple], object]
GetRun = Callable[[str], Optional[Dict]]
ListRuns = Callable[[str, str, int], List[Dict]]
DeleteRun = Callable[[str], None]

DEFAULT_RUNS_RETENTION = 5

_ALLOWED_RUN_UPDATE_FIELDS = {
    "status",
    "input_hash", "output_hash",
    "total_exec_time",
    "total_rows_in", "total_rows_out", "total_rows_dropped",
    "total_nodes", "nodes_ok", "nodes_error", "nodes_skipped",
}


def create_run(
    run_id: str,
    canvas_id: str,
    group_id: str,
    total_nodes: int = 0,
    user_id: Optional[str] = None,
    *,
    execute_func: Execute | None = None,
    get_run_func: GetRun | None = None,
) -> Dict:
    """Create a new row in intermediate.runs."""
    execute_boundary = execute_func or execute
    get_run_boundary = get_run_func or get_run
    now = datetime.now(timezone.utc).isoformat()
    execute_boundary(
        """INSERT INTO runs
           (run_id, canvas_id, group_id, timestamp, status, total_nodes, user_id)
           VALUES (?,?,?,?,?,?,?)""",
        (run_id, canvas_id, group_id, now, "running", total_nodes, user_id),
    )
    return get_run_boundary(run_id)


def get_run(
    run_id: str,
    *,
    fetchone_func: FetchOne | None = None,
) -> Optional[Dict]:
    fetchone_boundary = fetchone_func or fetchone
    return fetchone_boundary("SELECT * FROM runs WHERE run_id = ?", (run_id,))


def list_runs(
    canvas_id: str,
    group_id: str,
    limit: int = 50,
    *,
    fetchall_func: FetchAll | None = None,
) -> List[Dict]:
    """List runs for a canvas, isolated by group, newest first."""
    fetchall_boundary = fetchall_func or fetchall
    return fetchall_boundary(
        "SELECT * FROM runs WHERE canvas_id = ? AND group_id = ? ORDER BY timestamp DESC LIMIT ?",
        (canvas_id, group_id, limit),
    )


def update_run(
    run_id: str,
    *,
    execute_func: Execute | None = None,
    **fields,
) -> None:
    """Update allowed mutable fields for a run."""
    if not fields:
        return
    invalid = set(fields) - _ALLOWED_RUN_UPDATE_FIELDS
    if invalid:
        raise ValueError(
            f"Campi non consentiti per UPDATE runs: {invalid}. "
            f"Consentiti: {_ALLOWED_RUN_UPDATE_FIELDS}"
        )
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    execute_boundary = execute_func or execute
    execute_boundary(
        f"UPDATE runs SET {set_clause} WHERE run_id = ?",
        tuple(fields.values()) + (run_id,),
    )


def pin_run(
    run_id: str,
    group_id: Optional[str],
    pinned: bool = True,
    *,
    execute_func: Execute | None = None,
) -> bool:
    """Pin or unpin a run, optionally scoped by group."""
    execute_boundary = execute_func or execute
    if group_id is None:
        cur = execute_boundary(
            "UPDATE runs SET pinned = ? WHERE run_id = ?",
            (pinned, run_id),
        )
    else:
        cur = execute_boundary(
            "UPDATE runs SET pinned = ? WHERE run_id = ? AND group_id = ?",
            (pinned, run_id, group_id),
        )
    return cur.rowcount > 0


def apply_runs_retention(
    canvas_id: str,
    group_id: str,
    retention_n: int | None = None,
    *,
    list_runs_func: ListRuns | None = None,
    delete_run_func: DeleteRun | None = None,
    logger_obj=None,
) -> None:
    """Apply FIFO retention to unpinned runs for a canvas."""
    log = logger_obj or logger
    list_boundary = list_runs_func or list_runs
    delete_boundary = delete_run_func or _delete_run
    try:
        if retention_n is None:
            retention_n = DEFAULT_RUNS_RETENTION

        all_runs = list_boundary(canvas_id, group_id, 1000)
        unpinned = [r for r in all_runs if not r.get("pinned")]

        if len(unpinned) <= retention_n:
            return

        to_delete = unpinned[retention_n:]
        for run in to_delete:
            delete_boundary(run["run_id"])

        log.info(
            f"Retention intermediate: canvas={canvas_id} "
            f"conservate={retention_n} eliminate={len(to_delete)}",
            op="retention",
        )
    except Exception as e:
        log.warning(
            f"Errore retention intermediate ({canvas_id}): {e}",
            op="retention",
        )


def _delete_run(
    run_id: str,
    *,
    fetchall_func: FetchAll | None = None,
    transaction_func=None,
    logger_obj=None,
) -> None:
    """Delete one run and track object-storage artifact cleanup."""
    fetchall_boundary = fetchall_func or fetchall
    transaction_boundary = transaction_func or transaction
    log = logger_obj or logger

    artifact_rows = fetchall_boundary(
        "SELECT node_id, artifact_uri "
        "FROM node_meta "
        "WHERE run_id = ? "
        "  AND artifact_uri IS NOT NULL "
        "  AND artifact_status = 'complete'",
        (run_id,),
    )

    pending_specs: List[tuple] = []
    for row in artifact_rows:
        node_id = row["node_id"]
        artifact_uri = row["artifact_uri"]
        if not artifact_uri or "://" in artifact_uri:
            continue
        pending_specs.append((node_id, artifact_uri, "data.parquet"))
        if artifact_uri.endswith("/data.parquet"):
            manifest_uri = artifact_uri[: -len("data.parquet")] + "manifest.json"
            pending_specs.append((node_id, manifest_uri, "manifest.json"))

    pending_refs: List[tuple] = []
    with transaction_boundary() as conn:
        with conn.cursor() as cur:
            if pending_specs:
                from app.shared.storage_reconciliation import record_pending

                for node_id, storage_key, kind in pending_specs:
                    ref = record_pending(
                        cur,
                        domain="intermediate_artifact",
                        storage_key=storage_key,
                        operation="delete",
                        reason="run_delete_cascade",
                        extras={
                            "run_id": run_id,
                            "node_id": node_id,
                            "kind": kind,
                        },
                    )
                    pending_refs.append((storage_key, ref))
            cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))

    if pending_refs:
        from app.shared.storage_compensation import compensating_delete
        from app.shared.storage_reconciliation import (
            mark_done,
            mark_failed_attempt,
        )

        for storage_key, ref in pending_refs:
            outcome = compensating_delete(
                storage_key,
                domain="intermediate_artifact",
                reason="run_delete_cascade",
                extras={
                    "run_id": run_id,
                    "reconciliation_id": ref.id,
                },
                logger=log,
            )
            if outcome in ("ok", "skipped"):
                mark_done(ref.id)
            else:
                mark_failed_attempt(
                    ref.id,
                    error=f"compensating_delete_outcome={outcome}",
                )


def delete_all_runs_for_canvas(
    canvas_id: str,
    *,
    fetchall_func: FetchAll | None = None,
    delete_run_func: DeleteRun | None = None,
    logger_obj=None,
) -> int:
    """Delete all runs for a canvas and return the successful delete count."""
    fetchall_boundary = fetchall_func or fetchall
    delete_boundary = delete_run_func or _delete_run
    log = logger_obj or logger
    try:
        rows = fetchall_boundary(
            "SELECT run_id FROM runs WHERE canvas_id = ?",
            (canvas_id,),
        )
        if not rows:
            return 0
        count = 0
        for row in rows:
            run_id = row["run_id"] if isinstance(row, dict) else row[0]
            try:
                delete_boundary(run_id)
                count += 1
            except Exception as e:
                log.warning(
                    f"delete_all_runs_for_canvas: errore su run {run_id}: {e}",
                    op="delete_project",
                    exc_info=True,
                )
        log.info(
            f"delete_all_runs_for_canvas: canvas={canvas_id} -> {count} run eliminate",
            op="delete_project",
        )
        return count
    except Exception as e:
        log.error(
            f"[CRASH] delete_all_runs_for_canvas: errore canvas={canvas_id}: {e}",
            op="delete_project",
            exc_info=True,
        )
        raise
