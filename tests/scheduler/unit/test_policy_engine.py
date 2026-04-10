"""
tests/scheduler/unit/test_policy_engine.py
==========================================
Unit test su PolicyEngine — motore decisionale retry/conflict.

NON richiede backend, DB o Celery.

Copre:
  - should_retry: max_retries raggiunto, backoff list, fallback ultimo
  - resolve_conflict: skip/force/abort
  - should_log_skipped
  - evaluate_result: success, partial, failed
  - decide_after_failure: retry, give_up
  - decide_after_success: completed, partial
"""

import pytest
pytestmark = pytest.mark.unit

from app.core.scheduler.policy_engine import PolicyEngine
from app.core.scheduler.models import (
    ScheduleErrorPolicy, ScheduleConflictPolicy, ScheduleConfig,
    SchedulePipeline, ScheduleFrequency, ScheduleExecution,
    ScheduleConcurrency, SchedulePreCheck, ScheduleRuntime,
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_error_policy(max_retries=3, backoff=None):
    return ScheduleErrorPolicy(
        max_retries=max_retries,
        retry_backoff_seconds=backoff or [60, 300, 900]
    )


def make_conflict_policy(append_conflicts="skip", log_skipped=True):
    return ScheduleConflictPolicy(
        append_conflicts=append_conflicts,
        log_skipped_rows=log_skipped
    )


def make_config(max_retries=3, backoff=None, append_conflicts="skip"):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return ScheduleConfig(
        id="sched-test",
        created_at=now,
        updated_at=now,
        pipeline=SchedulePipeline(project_id="proj-1", project_type="estrai"),
        schedule=ScheduleFrequency(cron_expression="0 6 * * 1", timezone="Europe/Rome"),
        execution=ScheduleExecution(),
        on_error=make_error_policy(max_retries, backoff),
        conflict_policy=make_conflict_policy(append_conflicts),
        concurrency=ScheduleConcurrency(),
        pre_check=SchedulePreCheck(),
        runtime=ScheduleRuntime(),
    )


# =============================================================================
# should_retry
# =============================================================================

class TestShouldRetry:

    def test_primo_tentativo_retry(self):
        policy = make_error_policy(max_retries=3, backoff=[60, 300, 900])
        should, wait = PolicyEngine.should_retry(policy, current_attempt=0)
        assert should is True
        assert wait == 60

    def test_secondo_tentativo_retry(self):
        policy = make_error_policy(max_retries=3, backoff=[60, 300, 900])
        should, wait = PolicyEngine.should_retry(policy, current_attempt=1)
        assert should is True
        assert wait == 300

    def test_terzo_tentativo_retry(self):
        policy = make_error_policy(max_retries=3, backoff=[60, 300, 900])
        should, wait = PolicyEngine.should_retry(policy, current_attempt=2)
        assert should is True
        assert wait == 900

    def test_max_retries_raggiunto_no_retry(self):
        """Mutation target: current_attempt >= max_retries → False."""
        policy = make_error_policy(max_retries=3)
        should, wait = PolicyEngine.should_retry(policy, current_attempt=3)
        assert should is False
        assert wait == 0

    def test_max_retries_superato(self):
        policy = make_error_policy(max_retries=3)
        should, wait = PolicyEngine.should_retry(policy, current_attempt=10)
        assert should is False

    def test_zero_retries_mai_retry(self):
        """max_retries=0 → nessun retry."""
        policy = make_error_policy(max_retries=0)
        should, wait = PolicyEngine.should_retry(policy, current_attempt=0)
        assert should is False

    def test_backoff_fallback_ultimo_valore(self):
        """Più retry che backoff → usa ultimo valore."""
        policy = make_error_policy(max_retries=5, backoff=[60, 300])
        should, wait = PolicyEngine.should_retry(policy, current_attempt=3)
        assert should is True
        assert wait == 300  # ultimo valore

    def test_backoff_vuoto_default_60(self):
        """Backoff list vuota → fallback 60s."""
        policy = make_error_policy(max_retries=3, backoff=[])
        should, wait = PolicyEngine.should_retry(policy, current_attempt=0)
        assert should is True
        assert wait == 60

    def test_un_solo_retry(self):
        policy = make_error_policy(max_retries=1, backoff=[120])
        should, wait = PolicyEngine.should_retry(policy, current_attempt=0)
        assert should is True
        assert wait == 120
        should2, _ = PolicyEngine.should_retry(policy, current_attempt=1)
        assert should2 is False


# =============================================================================
# resolve_conflict
# =============================================================================

class TestResolveConflict:

    def test_skip(self):
        policy = make_conflict_policy("skip")
        assert PolicyEngine.resolve_conflict(policy) == "skip"

    def test_force(self):
        policy = make_conflict_policy("force")
        assert PolicyEngine.resolve_conflict(policy) == "force"

    def test_abort(self):
        policy = make_conflict_policy("abort")
        assert PolicyEngine.resolve_conflict(policy) == "abort"

    def test_default_skip(self):
        """Default è skip."""
        policy = ScheduleConflictPolicy()
        assert PolicyEngine.resolve_conflict(policy) == "skip"


# =============================================================================
# should_log_skipped
# =============================================================================

class TestShouldLogSkipped:

    def test_log_skipped_true(self):
        policy = make_conflict_policy(log_skipped=True)
        assert PolicyEngine.should_log_skipped(policy) is True

    def test_log_skipped_false(self):
        policy = make_conflict_policy(log_skipped=False)
        assert PolicyEngine.should_log_skipped(policy) is False

    def test_default_true(self):
        policy = ScheduleConflictPolicy()
        assert PolicyEngine.should_log_skipped(policy) is True


# =============================================================================
# evaluate_result
# =============================================================================

class TestEvaluateResult:

    def test_success_no_skip_completed(self):
        result = PolicyEngine.evaluate_result(success=True, rows_written=100, rows_skipped=0)
        assert result == "completed"

    def test_success_con_skip_partial(self):
        """Mutation target: rows_skipped > 0 → partial."""
        result = PolicyEngine.evaluate_result(success=True, rows_written=90, rows_skipped=10)
        assert result == "partial"

    def test_failure_failed(self):
        result = PolicyEngine.evaluate_result(success=False, rows_written=0)
        assert result == "failed"

    def test_failure_con_skip_comunque_failed(self):
        """Anche con righe skipped, se success=False → failed."""
        result = PolicyEngine.evaluate_result(success=False, rows_written=50, rows_skipped=50)
        assert result == "failed"

    def test_zero_righe_success_completed(self):
        result = PolicyEngine.evaluate_result(success=True, rows_written=0, rows_skipped=0)
        assert result == "completed"

    def test_tutti_skip_partial(self):
        result = PolicyEngine.evaluate_result(success=True, rows_written=0, rows_skipped=100)
        assert result == "partial"


# =============================================================================
# decide_after_failure
# =============================================================================

class TestDecideAfterFailure:

    def test_primo_fallimento_retry(self):
        config = make_config(max_retries=3, backoff=[60, 300, 900])
        decision = PolicyEngine.decide_after_failure(config, current_attempt=0, error_message="timeout")
        assert decision["action"] == "retry"
        assert decision["wait_seconds"] == 60
        assert decision["event"] == "RETRY_SCHEDULED"
        assert decision["log_level"] == "WARNING"

    def test_retry_esauriti_give_up(self):
        config = make_config(max_retries=3)
        decision = PolicyEngine.decide_after_failure(config, current_attempt=3, error_message="fatal")
        assert decision["action"] == "give_up"
        assert decision["event"] == "RETRY_EXHAUSTED"
        assert decision["log_level"] == "ERROR"

    def test_attempt_incrementato(self):
        """Mutation target: attempt nel result = current_attempt + 1."""
        config = make_config(max_retries=3)
        decision = PolicyEngine.decide_after_failure(config, current_attempt=1, error_message="err")
        assert decision["attempt"] == 2

    def test_zero_retries_subito_give_up(self):
        config = make_config(max_retries=0)
        decision = PolicyEngine.decide_after_failure(config, current_attempt=0, error_message="err")
        assert decision["action"] == "give_up"

    def test_secondo_retry_backoff_300(self):
        config = make_config(max_retries=3, backoff=[60, 300, 900])
        decision = PolicyEngine.decide_after_failure(config, current_attempt=1, error_message="err")
        assert decision["wait_seconds"] == 300


# =============================================================================
# decide_after_success
# =============================================================================

class TestDecideAfterSuccess:

    def test_tutto_ok_completed(self):
        decision = PolicyEngine.decide_after_success(rows_written=100, rows_skipped=0)
        assert decision["status"] == "completed"
        assert decision["event"] == "PIPELINE_COMPLETED"
        assert decision["log_level"] == "INFO"

    def test_con_skip_partial(self):
        decision = PolicyEngine.decide_after_success(rows_written=90, rows_skipped=10)
        assert decision["status"] == "partial"
        assert decision["event"] == "PIPELINE_PARTIAL"
        assert decision["log_level"] == "WARNING"

    def test_zero_righe_completed(self):
        decision = PolicyEngine.decide_after_success(rows_written=0, rows_skipped=0)
        assert decision["status"] == "completed"

    def test_tutti_skip_partial(self):
        decision = PolicyEngine.decide_after_success(rows_written=0, rows_skipped=100)
        assert decision["status"] == "partial"
