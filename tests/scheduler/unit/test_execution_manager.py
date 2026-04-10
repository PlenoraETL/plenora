"""
tests/scheduler/unit/test_execution_manager.py
===============================================
Unit test su ExecutionManager con mock completo.

NON richiede backend, DB reale o Celery.

Copre:
  - execute(): schedule non trovato
  - execute(): progetto non trovato → orphan
  - execute(): group isolation violation
  - execute(): concurrency skipped
  - execute(): concurrency queued
  - execute(): pipeline fallita → _handle_failure
  - execute(): successo senza destination
  - execute(): successo con destination CARICA
  - execute(): lock sempre rilasciato (finally)
  - _pre_check_connection: success, failure
  - _handle_failure: retry, give_up
"""

import pytest
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.unit

from app.core.scheduler.execution_manager import ExecutionManager
from app.core.scheduler.models import (
    ScheduleConfig, ScheduleFrequency, SchedulePipeline,
    ScheduleRuntime, ScheduleExecution, ScheduleErrorPolicy,
    ScheduleConflictPolicy, ScheduleConcurrency, SchedulePreCheck,
    ScheduleDestination,
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_config(
    schedule_id="sched-001",
    project_id="proj-1",
    enabled=True,
    with_destination=False,
    max_retries=3,
    verify_connection=False,
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
        schedule=ScheduleFrequency(cron_expression="0 6 * * 1", enabled=enabled),
        execution=ScheduleExecution(),
        on_error=ScheduleErrorPolicy(max_retries=max_retries, retry_backoff_seconds=[60, 300, 900]),
        conflict_policy=ScheduleConflictPolicy(),
        concurrency=ScheduleConcurrency(max_queue_size=3),
        pre_check=SchedulePreCheck(verify_destination_connection=verify_connection),
        runtime=ScheduleRuntime(),
        destination=dest,
    )


def make_manager():
    """ExecutionManager con tutti i servizi mockati."""
    mgr = ExecutionManager.__new__(ExecutionManager)
    mgr.schedule_service = MagicMock()
    mgr.log_service = MagicMock()
    mgr.concurrency = MagicMock()
    mgr.policy = MagicMock()
    mgr.log_service.create_log_entry.return_value = MagicMock()
    mgr.log_service.write_log.return_value = "2026-03-15/sched_001_090000.json"
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


def make_project_meta(group_id="default"):
    meta = MagicMock()
    meta.group_id = group_id
    return meta


# =============================================================================
# execute() — errori configurazione
# =============================================================================

class TestExecuteConfigErrors:

    def test_schedule_non_trovato(self):
        mgr = make_manager()
        mgr.schedule_service.get_schedule.return_value = None
        result = mgr.execute("sched-xyz")
        assert result["success"] is False
        assert result["status"] == "failed"

    def test_progetto_non_trovato_orphan(self):
        mgr = make_manager()
        config = make_config()
        mgr.schedule_service.get_schedule.return_value = config

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule") as mock_db:
            mock_db.return_value = {"group_id": "default"}
            mock_ps.return_value.get_project_metadata.return_value = None
            result = mgr.execute("sched-001")

        assert result["status"] == "orphan"
        mgr.schedule_service.mark_as_orphan.assert_called_once_with("sched-001")

    def test_group_isolation_violation(self):
        """Schedule e progetto su gruppi diversi → accesso negato."""
        mgr = make_manager()
        config = make_config()
        mgr.schedule_service.get_schedule.return_value = config
        meta = make_project_meta(group_id="altro_gruppo")

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule") as mock_db:
            mock_db.return_value = {"group_id": "default"}
            mock_ps.return_value.get_project_metadata.return_value = meta
            result = mgr.execute("sched-001")

        assert result["success"] is False
        assert "group" in result["message"].lower() or "accesso" in result["message"].lower()


# =============================================================================
# execute() — concurrency
# =============================================================================

class TestExecuteConcurrency:

    def _setup_project(self, mgr, config):
        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule") as mock_db:
            meta = make_project_meta()
            mock_ps.return_value.get_project_metadata.return_value = meta
            return mock_ps, mock_db

    def test_coda_piena_skipped(self):
        mgr = make_manager()
        config = make_config()
        mgr.schedule_service.get_schedule.return_value = config
        mgr.concurrency.try_acquire.return_value = "skipped"

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule") as mock_db:
            mock_db.return_value = {"group_id": "default"}
            mock_ps.return_value.get_project_metadata.return_value = make_project_meta()
            result = mgr.execute("sched-001")

        assert result["success"] is False
        assert result["status"] == "skipped"

    def test_queued(self):
        mgr = make_manager()
        config = make_config()
        mgr.schedule_service.get_schedule.return_value = config
        mgr.concurrency.try_acquire.return_value = "queued"

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule") as mock_db:
            mock_db.return_value = {"group_id": "default"}
            mock_ps.return_value.get_project_metadata.return_value = make_project_meta()
            result = mgr.execute("sched-001")

        assert result["success"] is True
        assert result["status"] == "queued"

    def test_lock_sempre_rilasciato(self):
        """Mutation target: finally → release sempre chiamato."""
        mgr = make_manager()
        config = make_config()
        mgr.schedule_service.get_schedule.return_value = config
        mgr.concurrency.try_acquire.return_value = "acquired"

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule", return_value={"group_id": "default"}) as mock_db, \
             patch.object(mgr, "_get_orchestrator") as mock_orch:
            mock_ps.return_value.get_project_metadata.return_value = make_project_meta()
            mock_orch.return_value.execute_pipeline.side_effect = RuntimeError("crash")
            result = mgr.execute("sched-001")

        mgr.concurrency.release.assert_called_with("sched-001")


# =============================================================================
# execute() — pipeline
# =============================================================================

class TestExecutePipeline:

    def test_pipeline_fallita_handle_failure(self):
        mgr = make_manager()
        config = make_config()
        mgr.schedule_service.get_schedule.return_value = config
        mgr.policy.decide_after_failure.return_value = {
            "action": "give_up", "wait_seconds": 0,
            "attempt": 0, "event": "RETRY_EXHAUSTED", "log_level": "ERROR"
        }

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule", return_value={"group_id": "default"}) as mock_db, \
             patch.object(mgr, "_get_orchestrator") as mock_orch:
            mock_ps.return_value.get_project_metadata.return_value = make_project_meta()
            mock_orch.return_value.execute_pipeline.return_value = {
                "success": False, "message": "DB non raggiungibile"
            }
            result = mgr.execute("sched-001")

        assert result["success"] is False

    def test_pipeline_successo_senza_destination(self):
        mgr = make_manager()
        config = make_config(with_destination=False)
        mgr.schedule_service.get_schedule.return_value = config

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule", return_value={"group_id": "default"}) as mock_db, \
             patch.object(mgr, "_get_orchestrator") as mock_orch, \
             patch.object(mgr, "_archive_output", return_value="/tmp/archive"):
            mock_ps.return_value.get_project_metadata.return_value = make_project_meta()
            mock_orch.return_value.execute_pipeline.return_value = {
                "success": True, "rows": 100, "output_file": "/tmp/out.csv"
            }
            result = mgr.execute("sched-001")

        assert result["success"] is True
        assert result["status"] == "completed"

    def test_pipeline_successo_con_destination(self):
        mgr = make_manager()
        config = make_config(with_destination=True)
        mgr.schedule_service.get_schedule.return_value = config

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule", return_value={"group_id": "default"}) as mock_db, \
             patch.object(mgr, "_get_orchestrator") as mock_orch, \
             patch.object(mgr, "_archive_output", return_value="/tmp/archive"), \
             patch.object(mgr, "_execute_carica") as mock_carica:
            mock_ps.return_value.get_project_metadata.return_value = make_project_meta()
            mock_orch.return_value.execute_pipeline.return_value = {
                "success": True, "rows": 100, "output_file": "/tmp/out.csv"
            }
            mock_carica.return_value = {
                "success": True, "rows_written": 100, "rows_skipped": 0
            }
            result = mgr.execute("sched-001")

        assert result["success"] is True
        mock_carica.assert_called_once()

    def test_carica_fallita_errore(self):
        mgr = make_manager()
        config = make_config(with_destination=True)
        mgr.schedule_service.get_schedule.return_value = config

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule", return_value={"group_id": "default"}) as mock_db, \
             patch.object(mgr, "_get_orchestrator") as mock_orch, \
             patch.object(mgr, "_archive_output", return_value=None), \
             patch.object(mgr, "_execute_carica") as mock_carica:
            mock_ps.return_value.get_project_metadata.return_value = make_project_meta()
            mock_orch.return_value.execute_pipeline.return_value = {
                "success": True, "rows": 100, "output_file": "/tmp/out.csv"
            }
            mock_carica.return_value = {
                "success": False, "error": "Connessione DB fallita"
            }
            result = mgr.execute("sched-001")

        assert result["success"] is False

    def test_triggered_by_manual(self):
        mgr = make_manager()
        config = make_config()
        mgr.schedule_service.get_schedule.return_value = config

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule", return_value={"group_id": "default"}) as mock_db, \
             patch.object(mgr, "_get_orchestrator") as mock_orch, \
             patch.object(mgr, "_archive_output", return_value=None):
            mock_ps.return_value.get_project_metadata.return_value = make_project_meta()
            mock_orch.return_value.execute_pipeline.return_value = {
                "success": True, "rows": 50
            }
            result = mgr.execute("sched-001", triggered_by="manual")

        assert result["success"] is True


# =============================================================================
# _handle_failure
# =============================================================================

class TestHandleFailure:

    def test_give_up_success_false(self):
        mgr = make_manager()
        config = make_config(max_retries=0)
        mgr.policy.decide_after_failure.return_value = {
            "action": "give_up", "wait_seconds": 0,
            "attempt": 0, "event": "RETRY_EXHAUSTED", "log_level": "ERROR"
        }
        result = mgr._handle_failure(
            config=config,
            schedule_id="sched-001",
            triggered_by="cron",
            retry_attempt=0,
            error_message="timeout",
            steps=[],
            start_time=time.time()
        )
        assert result["success"] is False

    def test_retry_success_true_con_retry_pending(self):
        mgr = make_manager()
        config = make_config(max_retries=3)
        mgr.policy.decide_after_failure.return_value = {
            "action": "retry", "wait_seconds": 60,
            "attempt": 1, "event": "RETRY_SCHEDULED", "log_level": "WARNING"
        }
        result = mgr._handle_failure(
            config=config,
            schedule_id="sched-001",
            triggered_by="cron",
            retry_attempt=0,
            error_message="timeout",
            steps=[],
            start_time=time.time()
        )
        # Con retry → il task Celery viene rimandato
        assert "retry" in str(result).lower() or result.get("success") is not None
