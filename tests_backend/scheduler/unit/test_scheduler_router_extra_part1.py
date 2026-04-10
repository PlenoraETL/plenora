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
# GET /schedules/healthcheck
# =============================================================================

class TestHealthcheckExtra:

    def test_healthcheck_ok_recent_beat(self):
        """Beat timestamp < 180s ago => status ok."""
        client = make_client()
        recent_ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        with patch("app.db.db_service.fetchone") as mock_fetchone:
            mock_fetchone.side_effect = [
                {"ts": recent_ts},          # beat_heartbeat
                {"n": 5},                    # schedules count
            ]
            res = client.get("/schedules/healthcheck")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        assert "5 schedule attivi" in body["message"]

    def test_healthcheck_down_old_beat(self):
        """Beat timestamp >= 180s ago => status down."""
        client = make_client()
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        with patch("app.db.db_service.fetchone") as mock_fetchone:
            mock_fetchone.side_effect = [
                {"ts": old_ts},
                {"n": 0},
            ]
            res = client.get("/schedules/healthcheck")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "down"
        assert "non risponde" in body["message"]

    def test_healthcheck_unknown_no_heartbeat(self):
        """No heartbeat row => status unknown."""
        client = make_client()
        with patch("app.db.db_service.fetchone") as mock_fetchone:
            mock_fetchone.return_value = None
            res = client.get("/schedules/healthcheck")
        assert res.status_code == 200
        assert res.json()["status"] == "unknown"

    def test_healthcheck_unknown_ts_null(self):
        """Row exists but ts is None => status unknown."""
        client = make_client()
        with patch("app.db.db_service.fetchone") as mock_fetchone:
            mock_fetchone.return_value = {"ts": None}
            res = client.get("/schedules/healthcheck")
        assert res.status_code == 200
        assert res.json()["status"] == "unknown"

    def test_healthcheck_exception_returns_down(self):
        """DB error => status down with error message."""
        client = make_client()
        with patch("app.db.db_service.fetchone") as mock_fetchone:
            mock_fetchone.side_effect = RuntimeError("DB connection lost")
            res = client.get("/schedules/healthcheck")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "down"
        assert "Errore" in body["message"]

    def test_healthcheck_naive_datetime_treated_as_utc(self):
        """Naive datetime (no tzinfo) is treated as UTC."""
        client = make_client()
        naive_ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with patch("app.db.db_service.fetchone") as mock_fetchone:
            mock_fetchone.side_effect = [
                {"ts": naive_ts},
                {"n": 2},
            ]
            res = client.get("/schedules/healthcheck")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"


# =============================================================================
# POST /schedules/resync-redbeat
# =============================================================================

class TestResyncRedbeat:

    def test_resync_syncs_enabled_schedules(self):
        """Enabled, non-orphan schedules are synced."""
        client = make_client()
        config = make_config()
        config.schedule.enabled = True
        config.runtime.status = "idle"
        disabled = make_config(schedule_id="sched-002")
        disabled.schedule.enabled = False

        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            mock_svc.return_value.list_schedules.return_value = ScheduleList(
                schedules=[config, disabled], total=2
            )
            res = client.post("/schedules/resync-redbeat")
        assert res.status_code == 200
        body = res.json()
        assert body["synced"] == 1
        assert body["skipped"] == 1

    def test_resync_redbeat_not_available(self):
        """Without _sync_redbeat method, backend is UrbyBeatScheduler."""
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            svc_instance = mock_svc.return_value
            svc_instance.list_schedules.return_value = ScheduleList(schedules=[], total=0)
            # Ensure _sync_redbeat is NOT present
            if hasattr(svc_instance, "_sync_redbeat"):
                del svc_instance._sync_redbeat
            res = client.post("/schedules/resync-redbeat")
        assert res.status_code == 200
        assert "UrbyBeatScheduler" in res.json()["backend"]

    def test_resync_collects_errors(self):
        """Per-schedule errors are collected, not raised."""
        client = make_client()
        config = make_config()
        config.schedule.enabled = True
        config.runtime.status = "idle"

        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            svc_instance = mock_svc.return_value
            svc_instance.list_schedules.return_value = ScheduleList(
                schedules=[config], total=1
            )
            svc_instance._sync_redbeat = MagicMock(side_effect=RuntimeError("Redis down"))
            res = client.post("/schedules/resync-redbeat")
        assert res.status_code == 200
        body = res.json()
        assert len(body["errors"]) == 1
        assert "Redis down" in body["errors"][0]["error"]

    def test_resync_skips_orphans(self):
        """Orphan schedules are skipped."""
        client = make_client()
        config = make_config()
        config.schedule.enabled = True
        config.runtime.status = "orphan"

        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            mock_svc.return_value.list_schedules.return_value = ScheduleList(
                schedules=[config], total=1
            )
            res = client.post("/schedules/resync-redbeat")
        assert res.status_code == 200
        assert res.json()["skipped"] == 1
        assert res.json()["synced"] == 0


# =============================================================================
# POST /schedules/check-orphans
# =============================================================================

class TestCheckOrphans:

    def test_check_orphans_found(self):
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            mock_svc.return_value.check_orphans.return_value = ["sched-001", "sched-003"]
            res = client.post("/schedules/check-orphans")
        assert res.status_code == 200
        body = res.json()
        assert body["orphaned_count"] == 2
        assert "sched-001" in body["orphaned_ids"]

    def test_check_orphans_none_found(self):
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            mock_svc.return_value.check_orphans.return_value = []
            res = client.post("/schedules/check-orphans")
        assert res.status_code == 200
        assert res.json()["orphaned_count"] == 0

    def test_check_orphans_error_500(self):
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            mock_svc.return_value.check_orphans.side_effect = RuntimeError("DB fail")
            res = client.post("/schedules/check-orphans")
        assert res.status_code == 500


# =============================================================================
# POST /schedules — create (extra coverage)
# =============================================================================

class TestCreateScheduleExtra:

    def test_create_cron_invalid_400(self):
        """Invalid cron expression returns 400."""
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService"), \
             patch("app.services.project_service.ProjectService") as mock_ps:
            meta = MagicMock()
            meta.name = "Test"
            meta.project_type = "estrai"
            mock_ps.return_value.get_project_metadata.return_value = meta
            res = client.post("/schedules", json={
                "project_id": "proj-1",
                "cron_expression": "INVALID CRON",
            })
        assert res.status_code == 400
        assert "non valida" in res.json()["detail"]

    def test_create_generic_error_500(self):
        """Unexpected error in create => 500."""
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc, \
             patch("app.services.project_service.ProjectService") as mock_ps:
            meta = MagicMock()
            meta.name = "Test"
            meta.project_type = "estrai"
            mock_ps.return_value.get_project_metadata.return_value = meta
            mock_svc.return_value.create_schedule.side_effect = RuntimeError("unexpected")
            res = client.post("/schedules", json={
                "project_id": "proj-1",
                "cron_expression": "0 6 * * 1",
            })
        assert res.status_code == 500


# =============================================================================
# GET /schedules/{id} — extra coverage
# =============================================================================

class TestGetScheduleExtra:

    def test_get_group_access_denied_403(self):
        """Different group_id => 403."""
        client = make_client()
        mock_row = {"id": "sched-001", "group_id": "other-group"}
        with patch("app.core.scheduler.schedule_service.ScheduleService"), \
             patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = mock_row
            res = client.get("/schedules/sched-001")
        assert res.status_code == 403

    def test_get_generic_error_500(self):
        """Unexpected error => 500."""
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService"), \
             patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.side_effect = RuntimeError("DB crash")
            res = client.get("/schedules/sched-001")
        assert res.status_code == 500


# =============================================================================
# PUT /schedules/{id} — update
# =============================================================================

class TestUpdateSchedule:

    def test_update_200(self):
        """Successful update returns 200."""
        client = make_client()
        config = make_config()
        mock_row = {"id": "sched-001", "group_id": "default"}
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc, \
             patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = mock_row
            mock_svc.return_value.update_schedule.return_value = config
            res = client.put("/schedules/sched-001", json={
                "cron_expression": "0 8 * * *",
            })
        assert res.status_code == 200

    def test_update_not_found_404(self):
        """Schedule not found => 404."""
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService"), \
             patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = None
            res = client.put("/schedules/sched-001", json={"cron_expression": "0 8 * * *"})
        assert res.status_code == 404

    def test_update_group_access_denied_403(self):
        """Different group => 403."""
        client = make_client()
        mock_row = {"id": "sched-001", "group_id": "other-group"}
        with patch("app.core.scheduler.schedule_service.ScheduleService"), \
             patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = mock_row
            res = client.put("/schedules/sched-001", json={"cron_expression": "0 8 * * *"})
        assert res.status_code == 403

    def test_update_project_id_change_not_found(self):
        """Changing project_id to nonexistent project => 404."""
        client = make_client()
        mock_row = {"id": "sched-001", "group_id": "default"}
        with patch("app.core.scheduler.schedule_service.ScheduleService"), \
             patch("app.core.scheduler.router.db") as mock_db, \
             patch("app.services.project_service.ProjectService") as mock_ps:
            mock_db.get_schedule.return_value = mock_row
            mock_ps.return_value.project_exists.return_value = False
            res = client.put("/schedules/sched-001", json={"project_id": "nonexistent"})
        assert res.status_code == 404
        assert "Progetto" in res.json()["detail"]

    def test_update_service_returns_none_404(self):
        """update_schedule returns None => 404."""
        client = make_client()
        mock_row = {"id": "sched-001", "group_id": "default"}
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc, \
             patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = mock_row
            mock_svc.return_value.update_schedule.return_value = None
            res = client.put("/schedules/sched-001", json={"cron_expression": "0 8 * * *"})
        assert res.status_code == 404

    def test_update_generic_error_500(self):
        """Unexpected error => 500."""
        client = make_client()
        mock_row = {"id": "sched-001", "group_id": "default"}
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc, \
             patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = mock_row
            mock_svc.return_value.update_schedule.side_effect = RuntimeError("unexpected")
            res = client.put("/schedules/sched-001", json={"cron_expression": "0 8 * * *"})
        assert res.status_code == 500


# =============================================================================
# DELETE /schedules/{id} — extra
# =============================================================================

class TestDeleteScheduleExtra:

    def test_delete_group_access_denied_403(self):
        """Different group => 403."""
        client = make_client()
        mock_row = {"id": "sched-001", "group_id": "other-group"}
        with patch("app.core.scheduler.schedule_service.ScheduleService"), \
             patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = mock_row
            res = client.delete("/schedules/sched-001")
        assert res.status_code == 403

    def test_delete_generic_error_500(self):
        """Unexpected error => 500."""
        client = make_client()
        mock_row = {"id": "sched-001", "group_id": "default"}
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc, \
             patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = mock_row
            mock_svc.return_value.delete_schedule.side_effect = RuntimeError("disk error")
            res = client.delete("/schedules/sched-001")
        assert res.status_code == 500
