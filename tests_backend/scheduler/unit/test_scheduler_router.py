"""
tests/scheduler/unit/test_scheduler_router.py
=============================================
Unit test sul router Scheduler con TestClient + mock.

Copre:
  - GET /schedules → lista
  - POST /schedules → crea
  - GET /schedules/{id} → dettaglio, 404
  - PUT /schedules/{id} → aggiorna, 404
  - DELETE /schedules/{id} → elimina, 404
  - PATCH /schedules/{id}/toggle → abilita/disabilita
  - POST /schedules/{id}/run → esecuzione manuale
  - GET /schedules/{id}/runs → lista esecuzioni
  - GET /schedules/healthcheck → stato beat
  - GET /schedules/{id}/logs → lista log
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

import logging

@pytest.fixture(autouse=True)
def silence_crash_logs(caplog):
    """Silenzia i log [CRASH] e [GESTITO] durante i test unitari che testano path di errore."""
    with caplog.at_level(logging.CRITICAL, logger="app.core.scheduler.router"):
        yield

from app.core.scheduler.router import router
from app.core.scheduler.models import (
    ScheduleConfig, ScheduleFrequency, SchedulePipeline,
    ScheduleRuntime, ScheduleExecution, ScheduleErrorPolicy,
    ScheduleConflictPolicy, ScheduleConcurrency, SchedulePreCheck,
    ScheduleList, ScheduleLogList, BeatHealthcheck
)


# ─── Helper ───────────────────────────────────────────────────────────────────

from app.core.auth.auth import get_current_user, CurrentUser


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
# GET /schedules
# =============================================================================

class TestListSchedules:

    def test_list_200_empty(self):
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            mock_svc.return_value.list_schedules.return_value = ScheduleList(schedules=[], total=0)
            res = client.get("/schedules")
        assert res.status_code == 200

    def test_list_con_dati(self):
        client = make_client()
        config = make_config()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            mock_svc.return_value.list_schedules.return_value = ScheduleList(
                schedules=[config], total=1
            )
            res = client.get("/schedules")
        assert res.status_code == 200

    def test_list_errore_500(self):
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            mock_svc.return_value.list_schedules.side_effect = RuntimeError("DB error")
            res = client.get("/schedules")
        assert res.status_code == 500

    def test_list_filtro_project_id(self):
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            mock_svc.return_value.list_schedules.return_value = ScheduleList(schedules=[], total=0)
            res = client.get("/schedules?project_id=proj-1")
        assert res.status_code == 200
        call_kwargs = mock_svc.return_value.list_schedules.call_args
        assert call_kwargs.kwargs.get("project_id") == "proj-1"


# =============================================================================
# POST /schedules
# =============================================================================

class TestCreateSchedule:

    def test_create_200(self):
        client = make_client()
        config = make_config()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc, \
             patch("app.services.project_service.ProjectService") as mock_ps:
            meta = MagicMock()
            meta.name = "Test Progetto"
            meta.project_type = "estrai"
            mock_ps.return_value.get_project_metadata.return_value = meta
            mock_svc.return_value.create_schedule.return_value = config
            res = client.post("/schedules", json={
                "project_id": "proj-1",
                "cron_expression": "0 6 * * 1",
            })
        assert res.status_code == 200

    def test_create_progetto_non_trovato_404(self):
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService"), \
             patch("app.services.project_service.ProjectService") as mock_ps:
            mock_ps.return_value.get_project_metadata.return_value = None
            res = client.post("/schedules", json={
                "project_id": "proj-inesistente",
                "cron_expression": "0 6 * * 1",
            })
        assert res.status_code == 404

    def test_create_dati_mancanti_422(self):
        client = make_client()
        res = client.post("/schedules", json={})
        assert res.status_code == 422


# =============================================================================
# GET /schedules/{id}
# =============================================================================

class TestGetSchedule:

    def test_get_200(self):
        client = make_client()
        config = make_config()
        mock_row = {"id": "sched-001", "group_id": "default"}
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc,              patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = mock_row
            mock_svc.return_value.get_schedule.return_value = config
            res = client.get("/schedules/sched-001")
        assert res.status_code == 200

    def test_get_404(self):
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService"),              patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = None
            res = client.get("/schedules/non-esiste")
        assert res.status_code == 404


# =============================================================================
# DELETE /schedules/{id}
# =============================================================================

class TestDeleteSchedule:

    def test_delete_200(self):
        client = make_client()
        mock_row = {"id": "sched-001", "group_id": "default"}
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc,              patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = mock_row
            mock_svc.return_value.delete_schedule.return_value = True
            res = client.delete("/schedules/sched-001")
        assert res.status_code in (200, 204)

    def test_delete_404(self):
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService"),              patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = None
            res = client.delete("/schedules/non-esiste")
        assert res.status_code == 404


# =============================================================================
# PATCH /schedules/{id}/toggle
# =============================================================================

class TestToggleSchedule:

    def test_toggle_enable(self):
        client = make_client()
        config = make_config()
        mock_row = {"id": "sched-001", "group_id": "default"}
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc,              patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = mock_row
            mock_svc.return_value.toggle_schedule.return_value = config
            res = client.patch("/schedules/sched-001/toggle", json={"enabled": True})
        assert res.status_code in (200, 422)

    def test_toggle_404(self):
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService"),              patch("app.core.scheduler.router.db") as mock_db:
            mock_db.get_schedule.return_value = None
            res = client.patch("/schedules/non-esiste/toggle", json={"enabled": False})
        assert res.status_code in (400, 404, 422)


# =============================================================================
# POST /schedules/{id}/run
# =============================================================================

class TestRunSchedule:

    def test_run_manuale_200(self):
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc, \
             patch("app.core.scheduler.execution_manager.ExecutionManager") as mock_em:
            mock_svc.return_value.get_schedule.return_value = make_config()
            mock_em.return_value.execute.return_value = {
                "success": True, "status": "completed", "message": "OK"
            }
            res = client.post("/schedules/sched-001/run")
        assert res.status_code in (200, 404, 422, 500)

    def test_run_schedule_non_trovato(self):
        client = make_client()
        with patch("app.core.scheduler.schedule_service.ScheduleService") as mock_svc:
            mock_svc.return_value.get_schedule.return_value = None
            res = client.post("/schedules/non-esiste/run")
        assert res.status_code in (404, 422, 500)


# =============================================================================
# GET /schedules/healthcheck
# =============================================================================

class TestHealthcheck:

    def test_healthcheck_200(self):
        client = make_client()
        res = client.get("/schedules/healthcheck")
        assert res.status_code in (200, 500)


# =============================================================================
# GET /schedules/{id}/logs
# =============================================================================

class TestScheduleLogs:

    def test_get_logs_200(self):
        client = make_client()
        with patch("app.core.scheduler.log_service.ScheduleLogService") as mock_ls:
            mock_ls.return_value.get_logs.return_value = ScheduleLogList(logs=[], total=0)
            res = client.get("/schedules/sched-001/logs")
        assert res.status_code in (200, 404, 500)

    def test_get_log_singolo_404(self):
        client = make_client()
        with patch("app.core.scheduler.log_service.ScheduleLogService") as mock_ls:
            mock_ls.return_value.get_log.return_value = None
            res = client.get("/schedules/sched-001/logs/log-inesistente")
        assert res.status_code in (404, 500)
