"""Contract tests per il `_reconcile_storage` hook del beat scheduler.

BE-GO-READY-STORAGE-ENGINE-006-RECONCILIATION-BEAT-HOOK.

Pattern mock-based clonato da `test_beat_scheduler_archive_hook_contract.py`.
Nessuna integration con PG / object storage: `compensating_delete` e i
4 helper del modulo `storage_reconciliation` sono mockati. La suite
valida:

- gate cadence (`STORAGE_RECONCILE_INTERVAL_SECONDS`);
- gate batch_size <= 0 e interval <= 0 (escape hatch ops);
- pickup ordinato di pending+delete entries;
- happy path: outcome ok / skipped → `mark_orphan_deleted`;
- failure path con backoff: outcome failed → `mark_failed_attempt` con
  `next_retry_at = compute_next_retry_at(...)`;
- max-attempts: nuovo `attempts` >= cap → `mark_lost` (terminal);
- best-effort: errori per-row catturati senza rompere il tick;
- belt-and-suspenders: rows con `operation != "delete"` skipped.

Non e' nello scope:
- upload-intent / W2 race;
- result-blob / intermediate artifact integration;
- storage-side scan;
- DB reale.
"""

from __future__ import annotations

import os
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.shared.storage_reconciliation import PendingReconciliationRow
from workers.beat_scheduler import PlenoraBeatScheduler


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def _make_scheduler() -> PlenoraBeatScheduler:
    """Scheduler bypass __init__ con tutti gli slot time-gate azzerati.

    Estende _make_scheduler degli altri archive/cleanup contract per
    includere il nuovo slot `_last_storage_reconcile`.
    """
    scheduler = PlenoraBeatScheduler.__new__(PlenoraBeatScheduler)
    scheduler._last_stuck_check = 0.0
    scheduler._last_eseguibili_cleanup = 0.0
    scheduler._last_logs_retention = 0.0
    scheduler._last_logs_archive = 0.0
    scheduler._last_audit_archive = 0.0
    scheduler._last_schedule_logs_retention = 0.0
    scheduler._last_imdf_bundles_cleanup = 0.0
    scheduler._last_temp_orphan_cleanup = 0.0
    scheduler._last_storage_reconcile = 0.0
    return scheduler


def _settings_ns(**overrides) -> SimpleNamespace:
    """SimpleNamespace con i default reali del reconciler."""
    defaults = dict(
        STORAGE_RECONCILE_INTERVAL_SECONDS=900,
        STORAGE_RECONCILE_BATCH_SIZE=50,
        STORAGE_RECONCILE_MAX_ATTEMPTS=5,
        STORAGE_RECONCILE_BACKOFF_BASE_SECONDS=60,
    )
    defaults.update(overrides)

    class _Settings(SimpleNamespace):
        def get(self, key, default=None):
            return getattr(self, key, default)

    return _Settings(**defaults)


def _row(*, row_id="11111111-1111-1111-1111-111111111111",
         domain="file_source", storage_key="file-sources/x.csv",
         operation="delete", attempts=0):
    return PendingReconciliationRow(
        id=row_id,
        domain=domain,
        storage_key=storage_key,
        operation=operation,
        attempts=attempts,
    )


# Cadence gate


def test_reconcile_storage_skips_within_interval(monkeypatch):
    """Se il tempo dall'ultimo run e' minore di interval, skip.
    Niente fetch, niente compensating_delete."""
    sched = _make_scheduler()
    # Last run was 60s ago, interval=900 → skip.
    sched._last_storage_reconcile = time.time() - 60

    fetch_mock = MagicMock()

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               fetch_mock):
        sched._reconcile_storage()

    fetch_mock.assert_not_called()


def test_reconcile_storage_runs_when_interval_elapsed(monkeypatch):
    sched = _make_scheduler()
    sched._last_storage_reconcile = time.time() - 1000  # > 900s

    fetch_mock = MagicMock(return_value=[])

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               fetch_mock), \
         patch("app.shared.storage_compensation.compensating_delete"):
        sched._reconcile_storage()

    fetch_mock.assert_called_once()


def test_reconcile_storage_disabled_when_interval_le_zero(monkeypatch):
    sched = _make_scheduler()

    fetch_mock = MagicMock()

    with patch("app.config.settings",
               _settings_ns(STORAGE_RECONCILE_INTERVAL_SECONDS=0)), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               fetch_mock):
        sched._reconcile_storage()

    fetch_mock.assert_not_called()


def test_reconcile_storage_disabled_when_batch_size_le_zero(monkeypatch):
    sched = _make_scheduler()

    fetch_mock = MagicMock()

    with patch("app.config.settings",
               _settings_ns(STORAGE_RECONCILE_BATCH_SIZE=0)), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               fetch_mock):
        sched._reconcile_storage()

    fetch_mock.assert_not_called()


def test_reconcile_storage_updates_last_run_slot_even_on_empty_fetch():
    """Slot aggiornato sempre per evitare di re-eseguire un fetch
    fallito a ogni tick."""
    sched = _make_scheduler()
    before = sched._last_storage_reconcile

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]):
        sched._reconcile_storage()

    assert sched._last_storage_reconcile > before


# Pickup + per-row outcome dispatch


def test_reconcile_storage_passes_batch_size_to_fetch():
    sched = _make_scheduler()

    fetch_mock = MagicMock(return_value=[])

    with patch("app.config.settings",
               _settings_ns(STORAGE_RECONCILE_BATCH_SIZE=25)), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               fetch_mock):
        sched._reconcile_storage()

    fetch_mock.assert_called_once_with(limit=25)


def test_reconcile_storage_ok_outcome_marks_orphan_deleted():
    sched = _make_scheduler()
    row = _row(row_id="aaaa1111-bbbb-2222-cccc-333344445555")

    mark_orphan = MagicMock()
    mark_failed = MagicMock()
    mark_lost = MagicMock()

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[row]), \
         patch("app.shared.storage_reconciliation.mark_orphan_deleted",
               mark_orphan), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt",
               mark_failed), \
         patch("app.shared.storage_reconciliation.mark_lost", mark_lost), \
         patch("app.shared.storage_compensation.compensating_delete",
               return_value="ok") as comp_mock:
        sched._reconcile_storage()

    comp_mock.assert_called_once()
    # compensating_delete called with the row's storage_key + domain.
    args, kwargs = comp_mock.call_args
    assert args == ("file-sources/x.csv",)
    assert kwargs["domain"] == "file_source"
    assert kwargs["reason"] == "reconciler_retry"
    assert kwargs["extras"] == {"reconciliation_id": row.id}

    mark_orphan.assert_called_once_with(row.id)
    mark_failed.assert_not_called()
    mark_lost.assert_not_called()


def test_reconcile_storage_skipped_outcome_marks_orphan_deleted():
    """Capability disabled (object storage off) treated as success
    for reconciliation purposes — no orphan possible to delete."""
    sched = _make_scheduler()
    row = _row()

    mark_orphan = MagicMock()
    mark_failed = MagicMock()
    mark_lost = MagicMock()

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[row]), \
         patch("app.shared.storage_reconciliation.mark_orphan_deleted",
               mark_orphan), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt",
               mark_failed), \
         patch("app.shared.storage_reconciliation.mark_lost", mark_lost), \
         patch("app.shared.storage_compensation.compensating_delete",
               return_value="skipped"):
        sched._reconcile_storage()

    mark_orphan.assert_called_once_with(row.id)
    mark_failed.assert_not_called()
    mark_lost.assert_not_called()


def test_reconcile_storage_failed_outcome_marks_failed_attempt_with_backoff():
    """outcome=failed, attempts<max → mark_failed_attempt with
    next_retry_at = compute_next_retry_at(...)."""
    sched = _make_scheduler()
    # Row with attempts=1; new_attempts=2 < 5 (max).
    row = _row(attempts=1)

    mark_orphan = MagicMock()
    mark_failed = MagicMock()
    mark_lost = MagicMock()

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[row]), \
         patch("app.shared.storage_reconciliation.mark_orphan_deleted",
               mark_orphan), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt",
               mark_failed), \
         patch("app.shared.storage_reconciliation.mark_lost", mark_lost), \
         patch("app.shared.storage_compensation.compensating_delete",
               return_value="failed"):
        sched._reconcile_storage()

    mark_failed.assert_called_once()
    args, kwargs = mark_failed.call_args
    assert args == (row.id,)
    assert kwargs["error"] == "compensating_delete_failed"
    # next_retry_at must be a tz-aware datetime in the future.
    next_retry = kwargs["next_retry_at"]
    assert isinstance(next_retry, datetime)
    assert next_retry.tzinfo is not None
    assert next_retry > datetime.now(timezone.utc)
    mark_orphan.assert_not_called()
    mark_lost.assert_not_called()


def test_reconcile_storage_failed_outcome_at_max_attempts_marks_lost():
    """outcome=failed, attempts+1 >= max → mark_lost (terminal)."""
    sched = _make_scheduler()
    # Row with attempts=4; new_attempts=5 >= 5 (max) → lost.
    row = _row(attempts=4)

    mark_orphan = MagicMock()
    mark_failed = MagicMock()
    mark_lost = MagicMock()

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[row]), \
         patch("app.shared.storage_reconciliation.mark_orphan_deleted",
               mark_orphan), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt",
               mark_failed), \
         patch("app.shared.storage_reconciliation.mark_lost", mark_lost), \
         patch("app.shared.storage_compensation.compensating_delete",
               return_value="failed"):
        sched._reconcile_storage()

    mark_lost.assert_called_once()
    args, kwargs = mark_lost.call_args
    assert args == (row.id,)
    assert "max_attempts_exceeded" in kwargs["error"]
    assert "5" in kwargs["error"]
    mark_orphan.assert_not_called()
    mark_failed.assert_not_called()


def test_reconcile_storage_max_attempts_cap_respected_when_configured():
    """Non-default max_attempts (e.g. 3): row with attempts=2 →
    new_attempts=3 → mark_lost."""
    sched = _make_scheduler()
    row = _row(attempts=2)

    mark_lost = MagicMock()

    with patch("app.config.settings",
               _settings_ns(STORAGE_RECONCILE_MAX_ATTEMPTS=3)), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[row]), \
         patch("app.shared.storage_reconciliation.mark_orphan_deleted"), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt"), \
         patch("app.shared.storage_reconciliation.mark_lost", mark_lost), \
         patch("app.shared.storage_compensation.compensating_delete",
               return_value="failed"):
        sched._reconcile_storage()

    mark_lost.assert_called_once()


def test_reconcile_storage_skips_non_delete_operations():
    """Belt-and-suspenders: even if a row with operation!='delete'
    leaks past the SQL filter, the hook must not call
    compensating_delete on it."""
    sched = _make_scheduler()
    row = _row(operation="upload_intent")  # type: ignore[arg-type]

    comp_mock = MagicMock()

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[row]), \
         patch("app.shared.storage_compensation.compensating_delete",
               comp_mock), \
         patch("app.shared.storage_reconciliation.mark_orphan_deleted"), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt"), \
         patch("app.shared.storage_reconciliation.mark_lost"):
        sched._reconcile_storage()

    comp_mock.assert_not_called()


def test_reconcile_storage_processes_multiple_rows_independently():
    """Mix di outcome: row A ok, row B failed → mark_orphan(A) +
    mark_failed(B). Errori per-row non bloccano il batch."""
    sched = _make_scheduler()
    row_a = _row(row_id="aaaa-1", attempts=0)
    row_b = _row(row_id="bbbb-2", attempts=1, storage_key="results/y.bin",
                 domain="result")

    mark_orphan = MagicMock()
    mark_failed = MagicMock()

    def _comp_dispatch(storage_key, **_):
        return "ok" if storage_key == "file-sources/x.csv" else "failed"

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[row_a, row_b]), \
         patch("app.shared.storage_reconciliation.mark_orphan_deleted",
               mark_orphan), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt",
               mark_failed), \
         patch("app.shared.storage_reconciliation.mark_lost"), \
         patch("app.shared.storage_compensation.compensating_delete",
               side_effect=_comp_dispatch):
        sched._reconcile_storage()

    mark_orphan.assert_called_once_with("aaaa-1")
    mark_failed.assert_called_once()
    assert mark_failed.call_args.args[0] == "bbbb-2"


# Best-effort error propagation


def test_reconcile_storage_per_row_exception_does_not_break_batch():
    """If `compensating_delete` raises (despite its own contract), the
    hook catches and continues with the next row."""
    sched = _make_scheduler()
    row_a = _row(row_id="aaaa-1")
    row_b = _row(row_id="bbbb-2", storage_key="results/y.bin")

    mark_orphan = MagicMock()

    def _comp_dispatch(storage_key, **_):
        if storage_key == "file-sources/x.csv":
            raise RuntimeError("boom")
        return "ok"

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[row_a, row_b]), \
         patch("app.shared.storage_reconciliation.mark_orphan_deleted",
               mark_orphan), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt"), \
         patch("app.shared.storage_reconciliation.mark_lost"), \
         patch("app.shared.storage_compensation.compensating_delete",
               side_effect=_comp_dispatch):
        # Must not raise.
        sched._reconcile_storage()

    # Row B still processed despite row A blowing up.
    mark_orphan.assert_called_once_with("bbbb-2")


def test_reconcile_storage_fetch_error_does_not_raise():
    sched = _make_scheduler()

    with patch("app.config.settings", _settings_ns()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               side_effect=RuntimeError("PG down")), \
         patch("app.shared.storage_compensation.compensating_delete") as comp_mock:
        # Must not raise.
        sched._reconcile_storage()

    comp_mock.assert_not_called()


# tick() wiring


def test_reconcile_storage_called_from_tick():
    """tick() must call _reconcile_storage after the existing cleanup
    chain (when leadership is acquired)."""
    sched = _make_scheduler()

    # Bypass leader election + super().tick().
    with patch.object(sched, "_try_become_leader", return_value=True), \
         patch.object(sched, "_sync_from_db"), \
         patch.object(sched, "_emit_catchup_runs"), \
         patch.object(sched, "_write_heartbeat"), \
         patch.object(sched, "_check_stuck_tasks"), \
         patch.object(sched, "_cleanup_eseguibili_outputs"), \
         patch.object(sched, "_cleanup_imdf_bundles"), \
         patch.object(sched, "_archive_old_logs"), \
         patch.object(sched, "_cleanup_old_logs"), \
         patch.object(sched, "_cleanup_old_schedule_logs"), \
         patch.object(sched, "_archive_old_audit_actions"), \
         patch.object(sched, "_cleanup_orphan_temp_files"), \
         patch.object(sched, "_reconcile_storage") as recon_mock, \
         patch("celery.beat.Scheduler.tick", return_value=42.0):
        sched.tick()

    recon_mock.assert_called_once()


def test_reconcile_storage_skipped_when_not_leader():
    """In standby (non-leader), the tick returns early — none of the
    cleanup hooks run, including reconciler."""
    sched = _make_scheduler()

    with patch.object(sched, "_try_become_leader", return_value=False), \
         patch.object(sched, "_reconcile_storage") as recon_mock:
        sched.tick()

    recon_mock.assert_not_called()
