"""
tests/scheduler/unit/test_execution_manager_coverage.py
========================================================
Additional coverage tests for ExecutionManager.

Targets uncovered lines:
  - 58-63:   __init__ — real constructor creates services
  - 67-70:   _get_connection_service — lazy singleton
  - 79-96:   _get_orchestrator — lazy import + build
  - 150-151: execute() — group_id read fails silently
  - 239-245: execute() — pre-check failure raises ExecutionError
  - 498, 504-505: _archive_output — files_to_copy empty / fallback iterdir
  - 525-527: _archive_output — exception during copy
  - 566:     _execute_carica — fallback raw path from pipeline_result
  - 581-583: _execute_carica — cleanup temp file (urby_cached_)
  - 718-719: _handle_failure — debug_payload builder exception
  - 752-755: _handle_failure — retry scheduling fails → update_runtime failed
"""

import pytest
import time
import tempfile
import os
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

pytestmark = pytest.mark.unit

from app.core.scheduler.execution_manager import ExecutionManager, ExecutionError
from app.core.scheduler.models import (
    ScheduleConfig, ScheduleFrequency, SchedulePipeline,
    ScheduleRuntime, ScheduleExecution, ScheduleErrorPolicy,
    ScheduleConflictPolicy, ScheduleConcurrency, SchedulePreCheck,
    ScheduleDestination, LogStep,
)


# -- Helpers ------------------------------------------------------------------

def _make_config(
    schedule_id="sched-001",
    project_id="proj-1",
    with_destination=False,
    verify_connection=False,
    max_retries=3,
    use_cache=False,
):
    now = datetime.now(timezone.utc)
    dest = None
    if with_destination:
        dest = ScheduleDestination(
            connection_id="conn-1",
            table_name="test_table",
            write_mode="append",
        )
    return ScheduleConfig(
        id=schedule_id,
        created_at=now,
        updated_at=now,
        pipeline=SchedulePipeline(project_id=project_id, project_name="Test", project_type="estrai"),
        schedule=ScheduleFrequency(cron_expression="0 6 * * 1"),
        execution=ScheduleExecution(use_cache=use_cache),
        on_error=ScheduleErrorPolicy(max_retries=max_retries, retry_backoff_seconds=[60, 300, 900]),
        conflict_policy=ScheduleConflictPolicy(),
        concurrency=ScheduleConcurrency(max_queue_size=3),
        pre_check=SchedulePreCheck(verify_destination_connection=verify_connection),
        runtime=ScheduleRuntime(),
        destination=dest,
    )


def _make_manager():
    """ExecutionManager built with __new__ and mocked services."""
    mgr = ExecutionManager.__new__(ExecutionManager)
    mgr.schedule_service = MagicMock()
    mgr.log_service = MagicMock()
    mgr.concurrency = MagicMock()
    mgr.policy = MagicMock()
    mgr._connection_service = None
    mgr.log_service.create_log_entry.return_value = MagicMock()
    mgr.log_service.write_log.return_value = "2026/sched_001.json"
    mgr.concurrency.try_acquire.return_value = "acquired"
    mgr.concurrency.release.return_value = True
    mgr.policy.decide_after_success.return_value = {
        "status": "completed", "event": "PIPELINE_COMPLETED", "log_level": "INFO"
    }
    mgr.policy.decide_after_failure.return_value = {
        "action": "give_up", "wait_seconds": 0,
        "attempt": 0, "event": "RETRY_EXHAUSTED", "log_level": "ERROR"
    }
    return mgr


def _make_project_meta(group_id="default"):
    meta = MagicMock()
    meta.group_id = group_id
    return meta




# =============================================================================
# Lines 752-755: _handle_failure — retry scheduling fails -> update_runtime failed
# =============================================================================

class TestHandleFailureRetrySchedulingFails:

    def test_retry_task_exception_marks_failed(self):
        """When apply_async for retry raises, schedule is marked as failed."""
        mgr = _make_manager()
        config = _make_config(max_retries=3)
        mgr.policy.decide_after_failure.return_value = {
            "action": "retry", "wait_seconds": 60,
            "attempt": 1, "event": "RETRY_SCHEDULED", "log_level": "WARNING"
        }

        with patch("workers.tasks.schedule_task.schedule_pipeline_task") as mock_task:
            mock_task.apply_async.side_effect = RuntimeError("broker down")

            result = mgr._handle_failure(
                config=config,
                schedule_id="sched-001",
                triggered_by="cron",
                retry_attempt=0,
                error_message="pipeline crash",
                steps=[],
                start_time=time.time(),
                run_id="",
            )

        # Should still return retry status (the function returns before the except changes it)
        # But update_runtime must have been called with status="failed"
        mgr.schedule_service.update_runtime.assert_called_once()
        call_kwargs = mgr.schedule_service.update_runtime.call_args
        assert call_kwargs[1]["status"] == "failed" or call_kwargs[0][1] == "failed"

    def test_retry_task_exception_runtime_has_timestamp(self):
        """update_runtime call on retry failure includes execution metadata."""
        mgr = _make_manager()
        config = _make_config(max_retries=3)
        mgr.policy.decide_after_failure.return_value = {
            "action": "retry", "wait_seconds": 30,
            "attempt": 1, "event": "RETRY_SCHEDULED", "log_level": "WARNING"
        }

        with patch("workers.tasks.schedule_task.schedule_pipeline_task") as mock_task:
            mock_task.apply_async.side_effect = ConnectionError("no redis")

            mgr._handle_failure(
                config=config,
                schedule_id="sched-001",
                triggered_by="cron",
                retry_attempt=1,
                error_message="err",
                steps=[],
                start_time=time.time(),
                run_id="",
            )

        call_kwargs = mgr.schedule_service.update_runtime.call_args[1]
        last_exec = call_kwargs["last_execution"]
        assert last_exec["status"] == "failed"
        assert "timestamp" in last_exec
        assert last_exec["rows_written"] == 0
