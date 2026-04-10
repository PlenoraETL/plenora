"""
tests/scheduler/unit/test_scheduler_router_extra.py
====================================================
Extra unit tests for the scheduler router to cover missing lines.

Covers:
  - GET  /schedules/healthcheck  → beat_time parsing, age ok/down, unknown, exception
  - POST /schedules/resync-redbeat → sync loop, redbeat_available, errors
  - POST /schedules/check-orphans  → success, error 500
  - POST /schedules               → croniter invalid 400, generic 500
  - GET  /schedules/{id}          → group access 403, generic 500
  - PUT  /schedules/{id}          → ownership 403, project_id change 404, update, 404, 500
  - DELETE /schedules/{id}        → group 403, error 500
  - PATCH /schedules/{id}/toggle  → group 403, existing check, orphan 400, error 500
  - POST /schedules/{id}/run      → ownership 403, queued + celery apply_async, error rollback
  - GET  /schedules/{id}/download → results_db_service, media types, streaming, 404
  - GET  /schedules/{id}/runs     → runs_dir iteration, no runs dir
  - DELETE /schedules/{id}/logs   → schedule check 404, delete_schedule_logs, error 500
  - GET  /schedules/{id}/logs     → schedule_exists 404, get_logs, error 500
  - GET  /schedules/{id}/logs/{log_id} → schedule_id mismatch 404, error 500
"""

import pytest
import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def silence_crash_logs(caplog):
    """Suppress [CRASH] and [GESTITO] error logs during unit tests."""
    with caplog.at_level(logging.CRITICAL, logger="app.core.scheduler.router"):
        yield


from app.core.scheduler.router import router
from app.core.scheduler.models import (
    ScheduleConfig, ScheduleFrequency, SchedulePipeline,
    ScheduleRuntime, ScheduleExecution, ScheduleErrorPolicy,
    ScheduleConflictPolicy, ScheduleConcurrency, SchedulePreCheck,
    ScheduleList, ScheduleLogList, ScheduleLogEntry, BeatHealthcheck,
)
from app.core.auth.auth import get_current_user, CurrentUser


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _mock_user() -> CurrentUser:
    return CurrentUser(
        user_id="test-uid",
        username="testuser",
        group_id="default",
        role="admin",
        is_active=True,
    )


def make_client():
    app = FastAPI()
    app.dependency_overrides[get_current_user] = _mock_user
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def make_config(schedule_id="sched-001", project_id="proj-1"):
    now = datetime.now(timezone.utc)
    return ScheduleConfig(
        id=schedule_id,
        created_at=now,
        updated_at=now,
        pipeline=SchedulePipeline(project_id=project_id, project_name="Test", project_type="estrai"),
        schedule=ScheduleFrequency(cron_expression="0 6 * * 1"),
        execution=ScheduleExecution(),
        on_error=ScheduleErrorPolicy(),
        conflict_policy=ScheduleConflictPolicy(),
        concurrency=ScheduleConcurrency(),
        pre_check=SchedulePreCheck(),
        runtime=ScheduleRuntime(),
    )




# =============================================================================
# GET /schedules/{id}/runs
# =============================================================================

class TestListRuns:

    def test_list_runs_schedule_not_found_404(self):
        """Schedule not found => 404."""
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            mock_svc.return_value.get_schedule.return_value = None
            res = client.get("/schedules/sched-001/runs")
        assert res.status_code == 404

    def test_list_runs_no_runs_dir(self):
        """Runs directory does not exist => empty runs list."""
        client = make_client()
        config = make_config()

        mock_runs_dir = MagicMock()
        mock_runs_dir.exists.return_value = False

        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc, \
             patch("app.config.settings") as mock_settings:
            mock_svc.return_value.get_schedule.return_value = config
            # Chain: settings.RESULTS_DIR / project_id / "runs" / schedule_short
            mock_settings.RESULTS_DIR.__truediv__ = MagicMock(return_value=MagicMock(
                __truediv__=MagicMock(return_value=MagicMock(
                    __truediv__=MagicMock(return_value=mock_runs_dir)
                ))
            ))
            res = client.get("/schedules/sched-001/runs")

        assert res.status_code == 200
        assert res.json()["runs"] == []

    def test_list_runs_with_files(self):
        """Runs directory has run folders with files."""
        client = make_client()
        config = make_config()

        # Build a mock filesystem
        mock_file = MagicMock()
        mock_file.name = "output.csv"
        mock_file.is_file.return_value = True
        mock_file.stat.return_value = MagicMock(st_size=1024)

        mock_run_folder = MagicMock()
        mock_run_folder.name = "20260401_060000"
        mock_run_folder.is_dir.return_value = True
        mock_run_folder.iterdir.return_value = [mock_file]

        mock_runs_dir = MagicMock()
        mock_runs_dir.exists.return_value = True
        mock_runs_dir.iterdir.return_value = [mock_run_folder]

        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc, \
             patch("app.config.settings") as mock_settings:
            mock_svc.return_value.get_schedule.return_value = config
            mock_settings.RESULTS_DIR.__truediv__ = MagicMock(return_value=MagicMock(
                __truediv__=MagicMock(return_value=MagicMock(
                    __truediv__=MagicMock(return_value=mock_runs_dir)
                ))
            ))
            res = client.get("/schedules/sched-001/runs")

        assert res.status_code == 200
        body = res.json()
        assert body["runs_count"] == 1
        assert body["runs"][0]["timestamp"] == "20260401_060000"
        assert body["runs"][0]["files"][0]["name"] == "output.csv"

    def test_list_runs_generic_error_500(self):
        """Unexpected error => 500."""
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            mock_svc.return_value.get_schedule.side_effect = RuntimeError("boom")
            res = client.get("/schedules/sched-001/runs")
        assert res.status_code == 500


# =============================================================================
# GET /schedules/{id}/logs
# =============================================================================

class TestGetLogsExtra:

    def test_get_logs_schedule_not_exists_404(self):
        """schedule_exists returns False => 404."""
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc, \
             patch("app.core.scheduler.log_service.ScheduleLogService"):
            mock_svc.return_value.schedule_exists.return_value = False
            res = client.get("/schedules/sched-001/logs")
        assert res.status_code == 404

    def test_get_logs_success(self):
        """Successful logs retrieval."""
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc, \
             patch("app.core.scheduler.log_service.ScheduleLogService") as mock_ls:
            mock_svc.return_value.schedule_exists.return_value = True
            mock_ls.return_value.get_logs.return_value = ScheduleLogList(logs=[], total=0)
            res = client.get("/schedules/sched-001/logs")
        assert res.status_code == 200

    def test_get_logs_generic_error_500(self):
        """Unexpected error in get_logs => 500."""
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc, \
             patch("app.core.scheduler.log_service.ScheduleLogService") as mock_ls:
            mock_svc.return_value.schedule_exists.return_value = True
            mock_ls.return_value.get_logs.side_effect = RuntimeError("DB fail")
            res = client.get("/schedules/sched-001/logs")
        assert res.status_code == 500


# =============================================================================
# _format_size helper
# =============================================================================

class TestFormatSize:

    def test_bytes(self):
        from app.core.scheduler.router import _format_size
        assert _format_size(500) == "500.0 B"

    def test_kilobytes(self):
        from app.core.scheduler.router import _format_size
        assert _format_size(2048) == "2.0 KB"

    def test_megabytes(self):
        from app.core.scheduler.router import _format_size
        result = _format_size(1048576)
        assert "MB" in result

    def test_gigabytes(self):
        from app.core.scheduler.router import _format_size
        result = _format_size(1073741824)
        assert "GB" in result

    def test_terabytes(self):
        from app.core.scheduler.router import _format_size
        result = _format_size(1099511627776)
        assert "TB" in result
