"""Orchestrator-owned cleanup boundary for task execution logs."""

from __future__ import annotations


__all__ = ["delete_task_log_runs"]


def delete_task_log_runs(task_id: str) -> int:
    """Delete log-run rows owned by one orchestrator task.

    The orchestrator owns the cleanup decision and failure handling; the DB
    module remains a leaf adapter that performs the concrete SQL operation.
    Exceptions intentionally propagate so the router can preserve its existing
    best-effort partial-cleanup response contract.
    """
    from app.db.log_runs_db_service import delete_log_run_by_run_id

    return delete_log_run_by_run_id(task_id)
