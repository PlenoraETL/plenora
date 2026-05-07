"""
Contract tests BE-GO-READY-STORAGE-ENGINE-008-RECONCILIATION-OPERATIONAL-GUARDRAILS.

Verify the beat-hook integration of the two new guardrails:
"""

from __future__ import annotations

import os
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.shared.storage_reconciliation import (
    PendingReconciliationRow,
    ReconciliationStateCount,
)
from workers.beat_scheduler import PlenoraBeatScheduler


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def _make_scheduler() -> PlenoraBeatScheduler:
    s = PlenoraBeatScheduler.__new__(PlenoraBeatScheduler)
    s._last_stuck_check = 0.0
    s._last_eseguibili_cleanup = 0.0
    s._last_logs_retention = 0.0
    s._last_logs_archive = 0.0
    s._last_audit_archive = 0.0
    s._last_schedule_logs_retention = 0.0
    s._last_imdf_bundles_cleanup = 0.0
    s._last_temp_orphan_cleanup = 0.0
    s._last_storage_reconcile = 0.0
    return s


def _settings_ns(**overrides) -> SimpleNamespace:
    defaults = dict(
        STORAGE_RECONCILE_INTERVAL_SECONDS=900,
        STORAGE_RECONCILE_BATCH_SIZE=50,
        STORAGE_RECONCILE_MAX_ATTEMPTS=5,
        STORAGE_RECONCILE_BACKOFF_BASE_SECONDS=60,
        STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS=30,
        STORAGE_RECONCILE_PRUNE_BATCH_SIZE=500,
    )
    defaults.update(overrides)

    class _Settings(SimpleNamespace):
        def get(self, key, default=None):
            return getattr(self, key, default)

    return _Settings(**defaults)


# Summary log


def test_summary_log_emitted_when_active_rows_exist(caplog):
    sched = _make_scheduler()
    sched._last_storage_reconcile = time.time() - 1000  # past interval

    counts = [
        ReconciliationStateCount(state="pending", domain="file_source", count=3),
        ReconciliationStateCount(state="lost", domain="result_blob", count=2),
    ]

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=counts), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               return_value=0), \
         patch("app.shared.storage_compensation.compensating_delete"), \
         caplog.at_level("INFO", logger="workers.beat_scheduler"):
        sched._reconcile_storage()

    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "event=storage_reconciliation_summary" in msgs
    assert "pending_total=3" in msgs
    assert "lost_total=2" in msgs
    assert "pending.file_source=3" in msgs
    assert "lost.result_blob=2" in msgs


def test_summary_log_skipped_when_no_active_rows(caplog):
    """No `pending` / `lost` rows -> no summary log line. Avoids
    log noise on healthy systems (the guardrail surfaces backlog,
    not "everything ok")."""
    sched = _make_scheduler()
    sched._last_storage_reconcile = time.time() - 1000

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               return_value=0), \
         patch("app.shared.storage_compensation.compensating_delete"), \
         caplog.at_level("INFO", logger="workers.beat_scheduler"):
        sched._reconcile_storage()

    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "event=storage_reconciliation_summary" not in msgs


def test_summary_log_emits_even_when_fetch_fails(caplog):
    """Fetch failure must not silence the backlog visibility — it's
    precisely the scenario where ops needs to see the counts."""
    sched = _make_scheduler()
    sched._last_storage_reconcile = time.time() - 1000

    counts = [
        ReconciliationStateCount(state="lost", domain="file_source", count=7),
    ]

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               side_effect=RuntimeError("DB blip")), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=counts), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               return_value=0), \
         patch("app.shared.storage_compensation.compensating_delete"), \
         caplog.at_level("INFO", logger="workers.beat_scheduler"):
        sched._reconcile_storage()

    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "lost_total=7" in msgs
    assert "lost.file_source=7" in msgs


def test_summary_failure_does_not_crash_tick(caplog):
    """Best-effort: an exception inside the summary block must NOT
    propagate up and crash the beat tick."""
    sched = _make_scheduler()
    sched._last_storage_reconcile = time.time() - 1000

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               side_effect=RuntimeError("count blew up")), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               return_value=0), \
         patch("app.shared.storage_compensation.compensating_delete"):
        # Must NOT raise.
        sched._reconcile_storage()


# Prune


def test_prune_invoked_with_settings_values():
    sched = _make_scheduler()
    sched._last_storage_reconcile = time.time() - 1000

    prune_spy = MagicMock(return_value=0)

    with patch("app.config.settings",
               _settings_ns(STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS=7,
                            STORAGE_RECONCILE_PRUNE_BATCH_SIZE=250)), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               prune_spy), \
         patch("app.shared.storage_compensation.compensating_delete"):
        sched._reconcile_storage()

    prune_spy.assert_called_once()
    kwargs = prune_spy.call_args.kwargs
    assert kwargs["limit"] == 250
    # `older_than` is a UTC datetime ~7 days ago. Verify the field is
    # supplied and tz-aware; do not pin exact value (clock).
    older_than = kwargs["older_than"]
    assert older_than.tzinfo is not None


def test_prune_skipped_when_retention_days_le_zero():
    sched = _make_scheduler()
    sched._last_storage_reconcile = time.time() - 1000

    prune_spy = MagicMock(return_value=0)

    with patch("app.config.settings",
               _settings_ns(STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS=0)), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               prune_spy), \
         patch("app.shared.storage_compensation.compensating_delete"):
        sched._reconcile_storage()

    prune_spy.assert_not_called()


def test_prune_skipped_when_prune_batch_size_le_zero():
    sched = _make_scheduler()
    sched._last_storage_reconcile = time.time() - 1000

    prune_spy = MagicMock(return_value=0)

    with patch("app.config.settings",
               _settings_ns(STORAGE_RECONCILE_PRUNE_BATCH_SIZE=0)), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               prune_spy), \
         patch("app.shared.storage_compensation.compensating_delete"):
        sched._reconcile_storage()

    prune_spy.assert_not_called()


def test_prune_log_emitted_when_rows_pruned(caplog):
    sched = _make_scheduler()
    sched._last_storage_reconcile = time.time() - 1000

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               return_value=42), \
         patch("app.shared.storage_compensation.compensating_delete"), \
         caplog.at_level("INFO", logger="workers.beat_scheduler"):
        sched._reconcile_storage()

    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "event=storage_reconciliation_prune_tick" in msgs
    assert "pruned=42" in msgs


def test_prune_log_skipped_when_zero_pruned(caplog):
    """Quiet path: prune ran, found nothing to delete -> no log
    line. Avoids log noise on healthy systems."""
    sched = _make_scheduler()
    sched._last_storage_reconcile = time.time() - 1000

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               return_value=0), \
         patch("app.shared.storage_compensation.compensating_delete"), \
         caplog.at_level("INFO", logger="workers.beat_scheduler"):
        sched._reconcile_storage()

    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "event=storage_reconciliation_prune_tick" not in msgs


def test_prune_failure_does_not_crash_tick():
    sched = _make_scheduler()
    sched._last_storage_reconcile = time.time() - 1000

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               side_effect=RuntimeError("prune blew up")), \
         patch("app.shared.storage_compensation.compensating_delete"):
        # Must NOT raise.
        sched._reconcile_storage()


# Existing transition flow preserved


def test_existing_transitions_still_run_alongside_guardrails():
    """When the batch contains a row, `compensating_delete` is still
    invoked and the appropriate transition fires (mark_orphan_deleted
    on success). Guardrails (summary + prune) run AFTER, not
    instead of."""
    sched = _make_scheduler()
    sched._last_storage_reconcile = time.time() - 1000

    row = PendingReconciliationRow(
        id="rid-1",
        domain="file_source",
        storage_key="file-sources/x.csv",
        operation="delete",
        attempts=0,
    )

    mark_orphan_deleted = MagicMock()
    prune_spy = MagicMock(return_value=0)

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[row]), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               prune_spy), \
         patch("app.shared.storage_reconciliation.mark_orphan_deleted",
               mark_orphan_deleted), \
         patch("app.shared.storage_compensation.compensating_delete",
               return_value="ok"):
        sched._reconcile_storage()

    mark_orphan_deleted.assert_called_once_with("rid-1")
    prune_spy.assert_called_once()


# Cadence gate still applies to guardrails


def test_guardrails_skipped_when_within_interval():
    """If the cadence gate aborts, neither summary nor prune run."""
    sched = _make_scheduler()
    sched._last_storage_reconcile = time.time() - 60  # < 900s

    count_spy = MagicMock()
    prune_spy = MagicMock()

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes"), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               count_spy), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               prune_spy):
        sched._reconcile_storage()

    count_spy.assert_not_called()
    prune_spy.assert_not_called()
