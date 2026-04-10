"""
tests/scheduler/unit/test_scheduler_models.py
==============================================
Unit test sui modelli Pydantic dello Scheduler.

NON richiede backend, DB o Celery.

Copre:
  - ScheduleFrequency: default, enabled
  - ScheduleExecution: use_cache default
  - ScheduleDestination: campi, write_mode
  - ScheduleConcurrency: policy, max_queue_size
  - SchedulePreCheck: verify, timeout
  - ScheduleErrorPolicy: max_retries, backoff
  - ScheduleConflictPolicy: append_conflicts, log_skipped
  - ScheduleRuntime: status default, contatori
  - SchedulePipeline: project_type
  - ScheduleConfig: composizione completa
  - ScheduleCreate: validazione
  - LastExecution: campi
"""

import pytest
from datetime import datetime, timezone

pytestmark = pytest.mark.unit

from app.core.scheduler.models import (
    ScheduleFrequency, ScheduleExecution, ScheduleDestination,
    ScheduleConcurrency, SchedulePreCheck, ScheduleErrorPolicy,
    ScheduleConflictPolicy, ScheduleRuntime, SchedulePipeline,
    ScheduleConfig, ScheduleCreate, LastExecution,
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_config(**kwargs):
    defaults = {
        "id": "sched-test-001",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "pipeline": SchedulePipeline(project_id="proj-1", project_type="estrai"),
        "schedule": ScheduleFrequency(cron_expression="0 6 * * 1"),
    }
    defaults.update(kwargs)
    return ScheduleConfig(**defaults)


# =============================================================================
# ScheduleFrequency
# =============================================================================

class TestScheduleFrequency:

    def test_cron_obbligatorio(self):
        freq = ScheduleFrequency(cron_expression="0 6 * * 1")
        assert freq.cron_expression == "0 6 * * 1"

    def test_timezone_default_rome(self):
        freq = ScheduleFrequency(cron_expression="0 6 * * 1")
        assert freq.timezone == "Europe/Rome"

    def test_enabled_default_true(self):
        freq = ScheduleFrequency(cron_expression="0 6 * * 1")
        assert freq.enabled is True

    def test_enabled_false(self):
        freq = ScheduleFrequency(cron_expression="0 6 * * 1", enabled=False)
        assert freq.enabled is False

    def test_description_default_vuota(self):
        freq = ScheduleFrequency(cron_expression="* * * * *")
        assert freq.description == ""

    def test_cron_ogni_ora(self):
        freq = ScheduleFrequency(cron_expression="0 * * * *")
        assert "* * *" in freq.cron_expression

    def test_cron_ogni_notte(self):
        freq = ScheduleFrequency(cron_expression="0 3 * * *", description="Ogni notte alle 03:00")
        assert freq.description == "Ogni notte alle 03:00"


# =============================================================================
# ScheduleExecution
# =============================================================================

class TestScheduleExecution:

    def test_use_cache_default_false(self):
        ex = ScheduleExecution()
        assert ex.use_cache is False

    def test_use_cache_true(self):
        ex = ScheduleExecution(use_cache=True)
        assert ex.use_cache is True


# =============================================================================
# ScheduleDestination
# =============================================================================

class TestScheduleDestination:

    def test_campi_base(self):
        dest = ScheduleDestination(
            connection_id="conn-1",
            table_name="immobili",
        )
        assert dest.connection_id == "conn-1"
        assert dest.table_name == "immobili"

    def test_write_mode_default_create(self):
        dest = ScheduleDestination(connection_id="conn-1", table_name="test")
        assert dest.write_mode == "create"

    def test_write_mode_append(self):
        dest = ScheduleDestination(connection_id="conn-1", table_name="test", write_mode="append")
        assert dest.write_mode == "append"

    def test_batch_size_default(self):
        dest = ScheduleDestination(connection_id="conn-1", table_name="test")
        assert dest.batch_size == 1000

    def test_schema_name_opzionale(self):
        dest = ScheduleDestination(connection_id="conn-1", table_name="test")
        assert dest.schema_name is None

    def test_match_columns_vuoto(self):
        dest = ScheduleDestination(connection_id="conn-1", table_name="test")
        assert dest.match_columns == []

    def test_tutti_i_write_modes(self):
        for mode in ["create", "replace", "append", "upsert", "update", "truncate_insert"]:
            dest = ScheduleDestination(connection_id="conn-1", table_name="test", write_mode=mode)
            assert dest.write_mode == mode


# =============================================================================
# ScheduleConcurrency
# =============================================================================

class TestScheduleConcurrency:

    def test_policy_default_queue(self):
        c = ScheduleConcurrency()
        assert c.policy == "queue"

    def test_max_queue_size_default_3(self):
        c = ScheduleConcurrency()
        assert c.max_queue_size == 3

    def test_max_queue_size_custom(self):
        c = ScheduleConcurrency(max_queue_size=5)
        assert c.max_queue_size == 5


# =============================================================================
# SchedulePreCheck
# =============================================================================

class TestSchedulePreCheck:

    def test_verify_default_true(self):
        pc = SchedulePreCheck()
        assert pc.verify_destination_connection is True

    def test_timeout_default_30(self):
        pc = SchedulePreCheck()
        assert pc.timeout_seconds == 30

    def test_verify_false(self):
        pc = SchedulePreCheck(verify_destination_connection=False)
        assert pc.verify_destination_connection is False


# =============================================================================
# ScheduleErrorPolicy
# =============================================================================

class TestScheduleErrorPolicy:

    def test_max_retries_default_3(self):
        p = ScheduleErrorPolicy()
        assert p.max_retries == 3

    def test_backoff_default(self):
        p = ScheduleErrorPolicy()
        assert p.retry_backoff_seconds == [60, 300, 900]

    def test_custom_retries(self):
        p = ScheduleErrorPolicy(max_retries=5, retry_backoff_seconds=[30, 60, 120, 300, 600])
        assert p.max_retries == 5
        assert len(p.retry_backoff_seconds) == 5

    def test_zero_retries(self):
        p = ScheduleErrorPolicy(max_retries=0)
        assert p.max_retries == 0


# =============================================================================
# ScheduleConflictPolicy
# =============================================================================

class TestScheduleConflictPolicy:

    def test_default_skip(self):
        p = ScheduleConflictPolicy()
        assert p.append_conflicts == "skip"

    def test_log_skipped_default_true(self):
        p = ScheduleConflictPolicy()
        assert p.log_skipped_rows is True

    def test_force(self):
        p = ScheduleConflictPolicy(append_conflicts="force")
        assert p.append_conflicts == "force"

    def test_abort(self):
        p = ScheduleConflictPolicy(append_conflicts="abort")
        assert p.append_conflicts == "abort"


# =============================================================================
# ScheduleRuntime
# =============================================================================

class TestScheduleRuntime:

    def test_status_default_idle(self):
        r = ScheduleRuntime()
        assert r.status == "idle"

    def test_contatori_default_zero(self):
        r = ScheduleRuntime()
        assert r.total_runs == 0
        assert r.success_count == 0
        assert r.failure_count == 0
        assert r.consecutive_failures == 0

    def test_last_execution_default_none(self):
        r = ScheduleRuntime()
        assert r.last_execution is None

    def test_next_execution_default_none(self):
        r = ScheduleRuntime()
        assert r.next_execution is None

    def test_status_running(self):
        r = ScheduleRuntime(status="running")
        assert r.status == "running"

    def test_tutti_gli_status(self):
        for s in ["idle", "running", "queued", "completed", "partial", "failed", "disabled", "orphan"]:
            r = ScheduleRuntime(status=s)
            assert r.status == s


# =============================================================================
# SchedulePipeline
# =============================================================================

class TestSchedulePipeline:

    def test_project_id_obbligatorio(self):
        p = SchedulePipeline(project_id="proj-1")
        assert p.project_id == "proj-1"

    def test_project_type_default_estrai(self):
        p = SchedulePipeline(project_id="proj-1")
        assert p.project_type == "estrai"

    def test_project_type_manipola(self):
        p = SchedulePipeline(project_id="proj-1", project_type="manipola")
        assert p.project_type == "manipola"

    def test_project_type_arricchisci(self):
        p = SchedulePipeline(project_id="proj-1", project_type="arricchisci")
        assert p.project_type == "arricchisci"

    def test_project_name_default_vuoto(self):
        p = SchedulePipeline(project_id="proj-1")
        assert p.project_name == ""


# =============================================================================
# ScheduleConfig — composizione completa
# =============================================================================

class TestScheduleConfig:

    def test_config_base(self):
        config = make_config()
        assert config.id == "sched-test-001"
        assert config.pipeline.project_id == "proj-1"
        assert config.schedule.cron_expression == "0 6 * * 1"

    def test_destination_default_none(self):
        config = make_config()
        assert config.destination is None

    def test_execution_default(self):
        config = make_config()
        assert config.execution.use_cache is False

    def test_runtime_default_idle(self):
        config = make_config()
        assert config.runtime.status == "idle"

    def test_on_error_default(self):
        config = make_config()
        assert config.on_error.max_retries == 3

    def test_con_destination(self):
        dest = ScheduleDestination(connection_id="conn-1", table_name="test")
        config = make_config(destination=dest)
        assert config.destination is not None
        assert config.destination.table_name == "test"

    def test_depends_on_default_none(self):
        config = make_config()
        assert config.depends_on_pipeline is None


# =============================================================================
# ScheduleCreate
# =============================================================================

class TestScheduleCreate:

    def test_campi_minimi(self):
        sc = ScheduleCreate(project_id="proj-1", cron_expression="0 6 * * 1")
        assert sc.project_id == "proj-1"
        assert sc.cron_expression == "0 6 * * 1"

    def test_timezone_default(self):
        sc = ScheduleCreate(project_id="proj-1", cron_expression="0 6 * * 1")
        assert sc.timezone == "Europe/Rome"

    def test_use_cache_default_false(self):
        sc = ScheduleCreate(project_id="proj-1", cron_expression="0 6 * * 1")
        assert sc.use_cache is False

    def test_max_retries_default_3(self):
        sc = ScheduleCreate(project_id="proj-1", cron_expression="0 6 * * 1")
        assert sc.max_retries == 3

    def test_append_conflicts_default_skip(self):
        sc = ScheduleCreate(project_id="proj-1", cron_expression="0 6 * * 1")
        assert sc.append_conflicts == "skip"

    def test_custom_values(self):
        sc = ScheduleCreate(
            project_id="proj-1",
            cron_expression="0 3 * * *",
            timezone="UTC",
            use_cache=True,
            max_retries=1,
            append_conflicts="force",
        )
        assert sc.timezone == "UTC"
        assert sc.use_cache is True
        assert sc.max_retries == 1


# =============================================================================
# LastExecution
# =============================================================================

class TestLastExecution:

    def test_campi_base(self):
        le = LastExecution(
            timestamp=datetime.now(timezone.utc),
            status="completed",
            rows_written=100,
            duration_ms=5000,
        )
        assert le.status == "completed"
        assert le.rows_written == 100
        assert le.duration_ms == 5000

    def test_status_partial(self):
        le = LastExecution(
            timestamp=datetime.now(timezone.utc),
            status="partial",
        )
        assert le.status == "partial"

    def test_rows_default_zero(self):
        le = LastExecution(
            timestamp=datetime.now(timezone.utc),
            status="completed",
        )
        assert le.rows_written == 0

    def test_log_file_default_vuoto(self):
        le = LastExecution(
            timestamp=datetime.now(timezone.utc),
            status="failed",
        )
        assert le.log_file == ""
