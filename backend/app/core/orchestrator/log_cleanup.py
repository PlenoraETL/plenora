"""Orchestrator-owned cleanup boundary for task execution logs."""

from __future__ import annotations

from app.core.orchestrator import log_cleanup_repository


__all__ = ["delete_task_log_runs"]


def delete_task_log_runs(task_id: str) -> int:
    """Delete log-run rows owned by one orchestrator task.

    The orchestrator owns the cleanup decision and failure handling; the DB
    write is intentionally kept at this orchestrator boundary instead of going
    through an `app.db.*_service` facade. Exceptions intentionally
    propagate so the router can preserve its existing best-effort
    partial-cleanup response contract.
    """
    cur = log_cleanup_repository.execute(
        "DELETE FROM log_runs WHERE run_id = %s",
        (task_id,),
    )
    return cur.rowcount
