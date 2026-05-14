"""Execution-log persistence owned by the orchestrator boundary."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.orchestrator.execution_log_repository import (
    execute as _execute,
    fetchall as _fetchall,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_execution(
    log_id: str,
    group_id: str,
    project_id: str | None = None,
    schedule_id: str | None = None,
    status: str = "running",
    message: str | None = None,
    triggered_by: str | None = None,
) -> dict[str, str]:
    """Create an orchestrator execution-log row."""
    now = _utc_now_iso()
    _execute(
        "INSERT INTO execution_logs (id, group_id, project_id, schedule_id, status, "
        "started_at, message, triggered_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            log_id,
            group_id,
            project_id,
            schedule_id,
            status,
            now,
            message,
            triggered_by,
        ),
    )
    return {"id": log_id, "status": status}


def update_log(
    log_id: str,
    status: str,
    finished_at: str | None = None,
    duration_s: float | None = None,
    rows_count: int | None = None,
    message: str | None = None,
    error: str | None = None,
) -> None:
    """Mark an orchestrator execution-log row with its latest status."""
    finished = finished_at or _utc_now_iso()
    _execute(
        "UPDATE execution_logs SET status=%s, finished_at=%s, duration_s=%s, "
        "rows_count=%s, message=%s, error=%s WHERE id=%s",
        (status, finished, duration_s, rows_count, message, error, log_id),
    )


def list_logs(
    project_id: str | None = None,
    group_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, object]]:
    """Read orchestrator execution logs with bounded pagination."""
    bounded_limit = max(1, min(int(limit or 50), 500))
    bounded_offset = max(0, int(offset or 0))
    conditions: list[str] = []
    params: list[object] = []
    if project_id:
        conditions.append("project_id = %s")
        params.append(project_id)
    if group_id:
        conditions.append("group_id = %s")
        params.append(group_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return _fetchall(
        f"SELECT * FROM execution_logs {where} ORDER BY started_at DESC LIMIT %s OFFSET %s",
        tuple(params) + (bounded_limit, bounded_offset),
    )
