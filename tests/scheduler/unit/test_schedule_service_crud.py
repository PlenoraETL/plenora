"""
tests/scheduler/unit/test_schedule_service_crud.py
====================================================
Test CRUD completi ScheduleService — create, list, update_runtime.
Con mock DB.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.unit

from app.core.scheduler.schedule_service import ScheduleService
from app.core.scheduler.models import (
    ScheduleConfig, ScheduleFrequency, SchedulePipeline,
    ScheduleRuntime, ScheduleExecution, ScheduleErrorPolicy,
    ScheduleConflictPolicy, ScheduleConcurrency, SchedulePreCheck,
    ScheduleCreate, ScheduleList,
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_svc():
    return ScheduleService.__new__(ScheduleService)


def make_config(schedule_id="sched-001", project_id="proj-1", enabled=True):
    now = datetime.now(timezone.utc)
    return ScheduleConfig(
        id=schedule_id,
        created_at=now, updated_at=now,
        pipeline=SchedulePipeline(project_id=project_id, project_name="Test", project_type="estrai"),
        schedule=ScheduleFrequency(cron_expression="0 6 * * 1", enabled=enabled),
        execution=ScheduleExecution(),
        on_error=ScheduleErrorPolicy(),
        conflict_policy=ScheduleConflictPolicy(),
        concurrency=ScheduleConcurrency(),
        pre_check=SchedulePreCheck(),
        runtime=ScheduleRuntime(),
    )


# =============================================================================
# list_schedules
# =============================================================================

class TestListSchedules:

    def test_lista_vuota(self):
        svc = make_svc()
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.list_schedules.return_value = []
            result = svc.list_schedules()
        assert result.total == 0
        assert result.schedules == []

    def test_lista_con_schedules(self):
        svc = make_svc()
        config = make_config()
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.list_schedules.return_value = [{"config": config.model_dump()}]
            result = svc.list_schedules()
        assert result.total == 1

    def test_lista_filtra_per_project_id(self):
        svc = make_svc()
        config1 = make_config("sched-1", "proj-A")
        config2 = make_config("sched-2", "proj-B")
        rows = [
            {"config": config1.model_dump()},
            {"config": config2.model_dump()},
        ]
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.list_schedules.return_value = rows
            result = svc.list_schedules(project_id="proj-A")
        assert result.total == 1
        assert result.schedules[0].pipeline.project_id == "proj-A"

    def test_lista_ordinata_per_data(self):
        """Mutation target: sort by updated_at reverse."""
        svc = make_svc()
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        config_old = make_config("sched-old")
        config_old.updated_at = now - timedelta(days=1)
        config_new = make_config("sched-new")
        config_new.updated_at = now
        rows = [
            {"config": config_old.model_dump()},
            {"config": config_new.model_dump()},
        ]
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.list_schedules.return_value = rows
            result = svc.list_schedules()
        # Primo deve essere il più recente
        assert result.schedules[0].id == "sched-new"

    def test_lista_skip_row_invalide(self):
        """Righe con config invalida vengono skippate senza crash."""
        svc = make_svc()
        config = make_config()
        rows = [
            {"config": {"campo_invalido": "x"}},  # invalida
            {"config": config.model_dump()},        # valida
        ]
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.list_schedules.return_value = rows
            result = svc.list_schedules()
        assert result.total == 1


# =============================================================================
# create_schedule
# =============================================================================

class TestCreateSchedule:

    def test_create_genera_id(self):
        svc = make_svc()
        data = ScheduleCreate(project_id="proj-1", cron_expression="0 6 * * 1")
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.save_schedule.return_value = None
            config = svc.create_schedule(data, "Test Project", "estrai")
        assert config.id.startswith("schedule_")

    def test_create_pipeline_project_id(self):
        svc = make_svc()
        data = ScheduleCreate(project_id="proj-42", cron_expression="0 6 * * 1")
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.save_schedule.return_value = None
            config = svc.create_schedule(data, "Test", "estrai")
        assert config.pipeline.project_id == "proj-42"

    def test_create_cron_expression_salvata(self):
        svc = make_svc()
        data = ScheduleCreate(project_id="proj-1", cron_expression="0 3 * * *")
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.save_schedule.return_value = None
            config = svc.create_schedule(data, "Test", "estrai")
        assert config.schedule.cron_expression == "0 3 * * *"

    def test_create_use_cache(self):
        svc = make_svc()
        data = ScheduleCreate(project_id="proj-1", cron_expression="0 6 * * 1", use_cache=True)
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.save_schedule.return_value = None
            config = svc.create_schedule(data, "Test", "estrai")
        assert config.execution.use_cache is True

    def test_create_max_retries(self):
        svc = make_svc()
        data = ScheduleCreate(project_id="proj-1", cron_expression="0 6 * * 1", max_retries=5)
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.save_schedule.return_value = None
            config = svc.create_schedule(data, "Test", "estrai")
        assert config.on_error.max_retries == 5

    def test_create_runtime_idle(self):
        """Nuovo schedule → status idle."""
        svc = make_svc()
        data = ScheduleCreate(project_id="proj-1", cron_expression="* * * * *")
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.save_schedule.return_value = None
            config = svc.create_schedule(data, "Test", "estrai")
        assert config.runtime.status == "idle"

    def test_create_pre_check_senza_destination(self):
        """Senza destination → verify_destination_connection=False."""
        svc = make_svc()
        data = ScheduleCreate(project_id="proj-1", cron_expression="0 6 * * 1")
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.save_schedule.return_value = None
            config = svc.create_schedule(data, "Test", "estrai")
        assert config.pre_check.verify_destination_connection is False


# =============================================================================
# update_runtime
# =============================================================================

class TestUpdateRuntime:

    def test_update_runtime_success(self):
        svc = make_svc()
        config = make_config()
        with patch.object(svc, "get_schedule", return_value=config), \
             patch("app.core.scheduler.schedule_service.db"):
            svc.update_runtime(
                "sched-001",
                status="completed",
                last_execution={
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "completed",
                    "rows_written": 100,
                    "duration_ms": 5000,
                }
            )
        # Non deve crashare
        assert True

    def test_update_runtime_schedule_non_trovato(self):
        svc = make_svc()
        with patch.object(svc, "get_schedule", return_value=None):
            svc.update_runtime("non-esiste", status="completed", last_execution={})
        assert True

    def test_update_runtime_incrementa_total_runs(self):
        svc = make_svc()
        config = make_config()
        config.runtime.total_runs = 5
        with patch.object(svc, "get_schedule", return_value=config), \
             patch("app.core.scheduler.schedule_service.db"):
            svc.update_runtime("sched-001", status="completed", last_execution={
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "completed", "rows_written": 0, "duration_ms": 0
            })
        assert config.runtime.total_runs == 6

    def test_update_runtime_completed_incrementa_success(self):
        svc = make_svc()
        config = make_config()
        config.runtime.success_count = 3
        with patch.object(svc, "get_schedule", return_value=config), \
             patch("app.core.scheduler.schedule_service.db"):
            svc.update_runtime("sched-001", status="completed", last_execution={
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "completed", "rows_written": 0, "duration_ms": 0
            })
        assert config.runtime.success_count == 4

    def test_update_runtime_failed_incrementa_failure(self):
        svc = make_svc()
        config = make_config()
        config.runtime.failure_count = 1
        config.runtime.consecutive_failures = 0
        with patch.object(svc, "get_schedule", return_value=config), \
             patch("app.core.scheduler.schedule_service.db"):
            svc.update_runtime("sched-001", status="failed", last_execution={
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "failed", "rows_written": 0, "duration_ms": 0
            })
        assert config.runtime.failure_count == 2

    def test_update_runtime_success_azzera_consecutive_failures(self):
        """Mutation target: success → consecutive_failures = 0."""
        svc = make_svc()
        config = make_config()
        config.runtime.consecutive_failures = 3
        with patch.object(svc, "get_schedule", return_value=config), \
             patch("app.core.scheduler.schedule_service.db"):
            svc.update_runtime("sched-001", status="completed", last_execution={
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "completed", "rows_written": 0, "duration_ms": 0
            })
        assert config.runtime.consecutive_failures == 0
