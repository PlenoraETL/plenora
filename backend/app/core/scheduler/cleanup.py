"""Cleanup boundary for scheduler-owned rows."""

from __future__ import annotations

from app.db.cleanup_sql import delete_group_rows


_DELETE_SCHEDULES_FOR_GROUP_SQL = "DELETE FROM schedules WHERE group_id = ?"
_DELETE_LOG_RUNS_FOR_GROUP_SQL = "DELETE FROM log_runs WHERE group_id = ?"


def delete_schedules_for_group(group_id: str) -> int:
    """Delete schedules owned by a group."""
    return delete_group_rows(_DELETE_SCHEDULES_FOR_GROUP_SQL, group_id)


def delete_log_runs_for_group(group_id: str) -> int:
    """Delete scheduler run log rows owned by a group."""
    return delete_group_rows(_DELETE_LOG_RUNS_FOR_GROUP_SQL, group_id)


__all__ = ["delete_log_runs_for_group", "delete_schedules_for_group"]
