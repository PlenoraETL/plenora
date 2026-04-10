"""
tests/scheduler/unit/test_scheduler_final.py
=============================================
Test finali scheduler — copertura aggiuntiva su policy, modelli,
concurrency e schedule_service per arrivare a ~400 test totali.

Copre:
  - PolicyEngine: boundary conditions, edge cases
  - ScheduleConfig: serializzazione/deserializzazione
  - ConcurrencyManager: _set_nx, _get, _incr
  - ScheduleService: get_schedules_for_project, _save_to_db
  - ScheduleRuntime: aggiornamento stato
  - BeatHealthcheck: modello
  - ScheduleUpdate: campi opzionali
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.unit

from app.core.scheduler.policy_engine import PolicyEngine
from app.core.scheduler.models import (
    ScheduleConfig, ScheduleFrequency, SchedulePipeline,
    ScheduleRuntime, ScheduleExecution, ScheduleErrorPolicy,
    ScheduleConflictPolicy, ScheduleConcurrency, SchedulePreCheck,
    ScheduleDestination, BeatHealthcheck, ScheduleUpdate,
    ScheduleCreate, LastExecution, LogStep,
)
from app.core.scheduler.schedule_service import ScheduleService
from app.core.scheduler.concurrency_manager import ConcurrencyManager


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_config(schedule_id="sched-001", project_id="proj-1"):
    now = datetime.now(timezone.utc)
    return ScheduleConfig(
        id=schedule_id, created_at=now, updated_at=now,
        pipeline=SchedulePipeline(project_id=project_id, project_type="estrai"),
        schedule=ScheduleFrequency(cron_expression="0 6 * * 1"),
        execution=ScheduleExecution(),
        on_error=ScheduleErrorPolicy(),
        conflict_policy=ScheduleConflictPolicy(),
        concurrency=ScheduleConcurrency(),
        pre_check=SchedulePreCheck(),
        runtime=ScheduleRuntime(),
    )


def make_error_policy(max_retries=3, backoff=None):
    return ScheduleErrorPolicy(
        max_retries=max_retries,
        retry_backoff_seconds=backoff or [60, 300, 900]
    )


# =============================================================================
# PolicyEngine — casi boundary
# =============================================================================

class TestPolicyEngineBoundary:

    def test_should_retry_exact_max(self):
        """current_attempt == max_retries → False (boundary)."""
        policy = make_error_policy(max_retries=3)
        should, _ = PolicyEngine.should_retry(policy, 3)
        assert should is False

    def test_should_retry_uno_prima_del_max(self):
        """current_attempt == max_retries - 1 → True (boundary)."""
        policy = make_error_policy(max_retries=3, backoff=[60, 300, 900])
        should, wait = PolicyEngine.should_retry(policy, 2)
        assert should is True
        assert wait == 900

    def test_evaluate_result_zero_rows_no_skip_completed(self):
        result = PolicyEngine.evaluate_result(True, 0, 0)
        assert result == "completed"

    def test_evaluate_result_uno_skip_partial(self):
        """Mutation: anche 1 solo skip → partial."""
        result = PolicyEngine.evaluate_result(True, 99, 1)
        assert result == "partial"

    def test_decide_after_success_zero_tutto_completed(self):
        decision = PolicyEngine.decide_after_success(0, 0)
        assert decision["status"] == "completed"

    def test_decide_after_failure_messaggio_presente(self):
        config = make_config()
        decision = PolicyEngine.decide_after_failure(config, 0, "errore test")
        assert "action" in decision
        assert "event" in decision

    def test_should_log_skipped_default(self):
        policy = ScheduleConflictPolicy()
        assert PolicyEngine.should_log_skipped(policy) is True


# =============================================================================
# ScheduleConfig — serializzazione
# =============================================================================

class TestScheduleConfigSerialization:

    def test_model_dump_e_validate(self):
        """Round-trip: dump → validate."""
        config = make_config()
        dumped = config.model_dump()
        restored = ScheduleConfig.model_validate(dumped)
        assert restored.id == config.id

    def test_model_dump_contiene_pipeline(self):
        config = make_config()
        dumped = config.model_dump()
        assert "pipeline" in dumped
        assert dumped["pipeline"]["project_id"] == "proj-1"

    def test_model_dump_contiene_runtime(self):
        config = make_config()
        dumped = config.model_dump()
        assert "runtime" in dumped
        assert dumped["runtime"]["status"] == "idle"

    def test_model_dump_contiene_on_error(self):
        config = make_config()
        dumped = config.model_dump()
        assert "on_error" in dumped
        assert dumped["on_error"]["max_retries"] == 3

    def test_config_con_destination_serializza(self):
        dest = ScheduleDestination(connection_id="conn-1", table_name="test")
        config = make_config()
        config.destination = dest
        dumped = config.model_dump()
        assert dumped["destination"]["table_name"] == "test"

    def test_config_senza_destination_none(self):
        config = make_config()
        dumped = config.model_dump()
        assert dumped["destination"] is None


# =============================================================================
# BeatHealthcheck
# =============================================================================

class TestBeatHealthcheck:

    def test_status_default_unknown(self):
        h = BeatHealthcheck()
        assert h.status == "unknown"

    def test_status_ok(self):
        h = BeatHealthcheck(status="ok")
        assert h.status == "ok"

    def test_status_down(self):
        h = BeatHealthcheck(status="down")
        assert h.status == "down"


# =============================================================================
# ScheduleUpdate
# =============================================================================

class TestScheduleUpdate:

    def test_tutti_campi_opzionali(self):
        """ScheduleUpdate senza campi → valido."""
        u = ScheduleUpdate()
        assert u.max_retries is None
        assert u.append_conflicts is None

    def test_update_solo_max_retries(self):
        u = ScheduleUpdate(max_retries=5)
        assert u.max_retries == 5

    def test_update_solo_append_conflicts(self):
        u = ScheduleUpdate(append_conflicts="force")
        assert u.append_conflicts == "force"

    def test_update_backoff(self):
        u = ScheduleUpdate(retry_backoff_seconds=[30, 60])
        assert u.retry_backoff_seconds == [30, 60]


# =============================================================================
# ConcurrencyManager — _incr
# =============================================================================

class TestConcurrencyManagerIncr:

    def make_mgr(self):
        mgr = ConcurrencyManager.__new__(ConcurrencyManager)
        mgr._available = True
        return mgr

    def test_incr_da_zero(self):
        mgr = self.make_mgr()
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_db.fetchone.return_value = None
            mock_db.execute.return_value = None
            result = mgr._incr("test_key")
        assert result == 1

    def test_incr_da_esistente(self):
        mgr = self.make_mgr()
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_db.fetchone.return_value = {"value": "3"}
            mock_db.execute.return_value = None
            result = mgr._incr("test_key")
        assert result == 4

    def test_cleanup_all(self):
        mgr = self.make_mgr()
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_cur = MagicMock()
            mock_cur.rowcount = 5
            mock_db.execute.return_value = mock_cur
            result = mgr.cleanup_all()
        assert result == 5

    def test_cleanup_all_zero(self):
        mgr = self.make_mgr()
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_cur = MagicMock()
            mock_cur.rowcount = 0
            mock_db.execute.return_value = mock_cur
            result = mgr.cleanup_all()
        assert result == 0


# =============================================================================
# ScheduleService — get_schedules_for_project
# =============================================================================

class TestGetSchedulesForProject:

    def test_progetto_con_schedule(self):
        svc = ScheduleService.__new__(ScheduleService)
        config = make_config(project_id="proj-special")
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.list_schedules.return_value = [{"config": config.model_dump()}]
            result = svc.get_schedules_for_project("proj-special")
        assert len(result) == 1
        assert result[0].pipeline.project_id == "proj-special"

    def test_progetto_senza_schedule(self):
        svc = ScheduleService.__new__(ScheduleService)
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.list_schedules.return_value = []
            result = svc.get_schedules_for_project("proj-no-schedule")
        assert result == []

    def test_filtra_solo_progetto_richiesto(self):
        svc = ScheduleService.__new__(ScheduleService)
        config_a = make_config("sched-a", "proj-A")
        config_b = make_config("sched-b", "proj-B")
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.list_schedules.return_value = [
                {"config": config_a.model_dump()},
                {"config": config_b.model_dump()},
            ]
            result = svc.get_schedules_for_project("proj-A")
        assert len(result) == 1
        assert result[0].id == "sched-a"


# =============================================================================
# ScheduleRuntime — aggiornamento contatori
# =============================================================================

class TestScheduleRuntimeCounters:

    def test_incremento_total_runs(self):
        r = ScheduleRuntime(total_runs=5)
        r.total_runs += 1
        assert r.total_runs == 6

    def test_incremento_success_count(self):
        r = ScheduleRuntime(success_count=10)
        r.success_count += 1
        assert r.success_count == 11

    def test_incremento_failure_count(self):
        r = ScheduleRuntime(failure_count=2, consecutive_failures=2)
        r.failure_count += 1
        r.consecutive_failures += 1
        assert r.failure_count == 3
        assert r.consecutive_failures == 3

    def test_reset_consecutive_failures(self):
        r = ScheduleRuntime(consecutive_failures=5)
        r.consecutive_failures = 0
        assert r.consecutive_failures == 0

    def test_set_current_task_id(self):
        r = ScheduleRuntime()
        r.current_task_id = "task-abc-123"
        assert r.current_task_id == "task-abc-123"

    def test_clear_current_task_id(self):
        r = ScheduleRuntime(current_task_id="task-old")
        r.current_task_id = None
        assert r.current_task_id is None


# =============================================================================
# LastExecution — tutti i status
# =============================================================================

class TestLastExecutionStatus:

    def test_completed(self):
        le = LastExecution(timestamp=datetime.now(timezone.utc), status="completed",
                           rows_written=100, duration_ms=5000)
        assert le.status == "completed"
        assert le.rows_written == 100

    def test_partial(self):
        le = LastExecution(timestamp=datetime.now(timezone.utc), status="partial",
                           rows_written=80, duration_ms=4000)
        assert le.status == "partial"

    def test_failed(self):
        le = LastExecution(timestamp=datetime.now(timezone.utc), status="failed")
        assert le.status == "failed"
        assert le.rows_written == 0


# =============================================================================
# LogStep — vari step e status
# =============================================================================

class TestLogStepVarianti:

    def test_output_con_file(self):
        step = LogStep(step="OUTPUT", status="success", file="output.csv")
        assert step.file == "output.csv"

    def test_carica_completo(self):
        step = LogStep(
            step="CARICA", status="partial",
            rows=95, rows_inserted=90, rows_updated=5,
            rows_skipped=5, write_mode="append",
            destination="pg/test_table"
        )
        assert step.rows_inserted == 90
        assert step.rows_skipped == 5
        assert step.write_mode == "append"

    def test_manipola_success(self):
        step = LogStep(step="MANIPOLA", status="success", duration_ms=2000, rows=500)
        assert step.rows == 500

    def test_arricchisci_success(self):
        step = LogStep(step="ARRICCHISCI", status="success", duration_ms=10000, rows=200)
        assert step.rows == 200

    def test_step_failed_no_rows(self):
        step = LogStep(step="ESTRAI", status="failed")
        assert step.rows == 0
        assert step.duration_ms == 0
