"""Run persistence boundary for intermediate executions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from app.db.pg_connection import execute, fetchall, fetchone
from app.core.intermediate.retention import (
    DEFAULT_RUNS_RETENTION,
    _delete_run,
    apply_runs_retention,
    delete_all_runs_for_canvas,
)

RunRow = Dict
FetchAll = Callable[[str, tuple], List[Dict]]
FetchOne = Callable[[str, tuple], Optional[Dict]]
Execute = Callable[[str, tuple], object]
GetRun = Callable[[str], Optional[Dict]]
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
