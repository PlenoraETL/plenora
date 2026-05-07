"""
Contract tests BE-GO-READY-STORAGE-ENGINE-006-RECONCILIATION-BEAT-HOOK.

Lock the boundary additions and the beat hook wiring:
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# Settings.DEFAULTS — 4 new tunables


def test_settings_defaults_expose_storage_reconcile_tunables():
    from app.config import Settings

    assert "STORAGE_RECONCILE_INTERVAL_SECONDS" in Settings.DEFAULTS
    assert "STORAGE_RECONCILE_BATCH_SIZE" in Settings.DEFAULTS
    assert "STORAGE_RECONCILE_MAX_ATTEMPTS" in Settings.DEFAULTS
    assert "STORAGE_RECONCILE_BACKOFF_BASE_SECONDS" in Settings.DEFAULTS

    assert isinstance(Settings.DEFAULTS["STORAGE_RECONCILE_INTERVAL_SECONDS"], int)
    assert Settings.DEFAULTS["STORAGE_RECONCILE_INTERVAL_SECONDS"] > 0
    assert Settings.DEFAULTS["STORAGE_RECONCILE_BATCH_SIZE"] > 0
    assert Settings.DEFAULTS["STORAGE_RECONCILE_MAX_ATTEMPTS"] >= 1
    assert Settings.DEFAULTS["STORAGE_RECONCILE_BACKOFF_BASE_SECONDS"] > 0


# compute_next_retry_at — pure backoff helper


def test_compute_next_retry_at_first_attempt_no_backoff():
    """attempts=1 (first failure) → now + base_seconds (no exponent)."""
    from app.shared.storage_reconciliation import compute_next_retry_at

    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
    out = compute_next_retry_at(1, base_seconds=60, now=now)
    assert out == now + timedelta(seconds=60)


def test_compute_next_retry_at_exponential_growth():
    """attempts=N → base * 2**(N-1) seconds."""
    from app.shared.storage_reconciliation import compute_next_retry_at

    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
    assert compute_next_retry_at(2, base_seconds=60, now=now) == now + timedelta(seconds=120)
    assert compute_next_retry_at(3, base_seconds=60, now=now) == now + timedelta(seconds=240)
    assert compute_next_retry_at(4, base_seconds=60, now=now) == now + timedelta(seconds=480)


def test_compute_next_retry_at_capped_at_24h_or_2_8():
    """Exponent capped (2**8) so backoff never explodes."""
    from app.shared.storage_reconciliation import compute_next_retry_at

    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
    # attempts=20 → exponent capped → not larger than attempts=9 (2**8).
    out_20 = compute_next_retry_at(20, base_seconds=60, now=now)
    out_9 = compute_next_retry_at(9, base_seconds=60, now=now)
    assert out_20 == out_9


def test_compute_next_retry_at_rejects_invalid_attempts():
    from app.shared.storage_reconciliation import compute_next_retry_at

    with pytest.raises(ValueError):
        compute_next_retry_at(0, base_seconds=60)
    with pytest.raises(ValueError):
        compute_next_retry_at(-1, base_seconds=60)


def test_compute_next_retry_at_rejects_invalid_base_seconds():
    from app.shared.storage_reconciliation import compute_next_retry_at

    with pytest.raises(ValueError):
        compute_next_retry_at(1, base_seconds=0)
    with pytest.raises(ValueError):
        compute_next_retry_at(1, base_seconds=-5)


# fetch_due_pending_deletes — read-only fetch


def _capture_fetchall(monkeypatch, return_rows):
    captured = {"sql": None, "params": None}

    def _fake(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return return_rows

    import app.db.pg_connection as pg_conn

    monkeypatch.setattr(pg_conn, "fetchall", _fake)
    return captured


def test_fetch_due_pending_deletes_filters_pending_delete_due():
    """SQL must filter state='pending' AND operation='delete' AND
    next_retry_at IS NULL OR <= now."""
    from app.shared import storage_reconciliation as recon

    captured = {}
    rows = []

    def _fake(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return rows

    with patch("app.db.pg_connection.fetchall", _fake):
        recon.fetch_due_pending_deletes(limit=10)

    sql = captured["sql"]
    assert "state = 'pending'" in sql
    assert "operation = 'delete'" in sql
    assert "next_retry_at IS NULL OR next_retry_at <=" in sql
    assert "LIMIT" in sql


def test_fetch_due_pending_deletes_returns_typed_rows():
    """Maps DB rows to PendingReconciliationRow with expected fields."""
    from app.shared import storage_reconciliation as recon

    db_rows = [
        {
            "id": "rec-1",
            "domain": "file_source",
            "storage_key": "file-sources/x/y.csv",
            "operation": "delete",
            "attempts": 0,
        },
        {
            "id": "rec-2",
            "domain": "result_blob",
            "storage_key": "results/p/r.bin",
            "operation": "delete",
            "attempts": 3,
        },
    ]
    with patch("app.db.pg_connection.fetchall", lambda sql, params=(): db_rows):
        out = recon.fetch_due_pending_deletes(limit=10)

    assert len(out) == 2
    assert isinstance(out[0], recon.PendingReconciliationRow)
    assert out[0].id == "rec-1"
    assert out[0].domain == "file_source"
    assert out[0].storage_key == "file-sources/x/y.csv"
    assert out[0].operation == "delete"
    assert out[0].attempts == 0
    assert out[1].attempts == 3


def test_fetch_due_pending_deletes_zero_or_negative_limit_raises():
    from app.shared import storage_reconciliation as recon

    with pytest.raises(ValueError):
        recon.fetch_due_pending_deletes(limit=0)
    with pytest.raises(ValueError):
        recon.fetch_due_pending_deletes(limit=-1)


def test_fetch_due_pending_deletes_swallows_db_error_returns_empty():
    """Best-effort: DB error → log warning, return [] (don't crash beat)."""
    from app.shared import storage_reconciliation as recon

    def _raise(sql, params=()):
        raise RuntimeError("DB unreachable")

    with patch("app.db.pg_connection.fetchall", _raise):
        out = recon.fetch_due_pending_deletes(limit=10)

    assert out == []


def test_fetch_due_pending_deletes_handles_tuple_rows():
    """Tolerates non-dict cursor flavors."""
    from app.shared import storage_reconciliation as recon

    db_rows = [
        ("rec-tuple", "file_source", "key/abc", "delete", 2),
    ]
    with patch("app.db.pg_connection.fetchall", lambda sql, params=(): db_rows):
        out = recon.fetch_due_pending_deletes(limit=5)

    assert len(out) == 1
    assert out[0].id == "rec-tuple"
    assert out[0].attempts == 2


# mark_lost — terminal autocommit


def _capture_pg_execute(monkeypatch):
    calls = []

    def _fake(sql, params=()):
        calls.append((sql, params))

        class _R:
            rowcount = 1
        return _R()

    import app.db.pg_connection as pg_conn

    monkeypatch.setattr(pg_conn, "execute", _fake)
    return calls


def test_mark_lost_issues_update_to_state_lost(monkeypatch):
    from app.shared.storage_reconciliation import mark_lost

    calls = _capture_pg_execute(monkeypatch)
    mark_lost("rec-99", error="max_attempts_exceeded (5)")
    assert len(calls) == 1
    sql, params = calls[0]
    assert "UPDATE plenora.storage_reconciliation_log" in sql
    assert "state = 'lost'" in sql
    assert "last_error = ?" in sql
    assert params == ("max_attempts_exceeded (5)", "rec-99")


def test_mark_lost_swallows_exception(monkeypatch):
    from app.shared.storage_reconciliation import mark_lost

    def _raise(*a, **kw):
        raise RuntimeError("DB unreachable")

    import app.db.pg_connection as pg_conn

    monkeypatch.setattr(pg_conn, "execute", _raise)
    # Must NOT raise.
    assert mark_lost("rec-z", error="boom") is None


# Beat hook _reconcile_storage — wiring + dispatch


def _make_scheduler():
    from workers.beat_scheduler import PlenoraBeatScheduler

    sched = PlenoraBeatScheduler.__new__(PlenoraBeatScheduler)
    sched._last_storage_reconcile = 0.0
    return sched


def _settings_stub(**kwargs):
    """SimpleNamespace + .get() callable per emulare Settings."""
    defaults = {
        "STORAGE_RECONCILE_INTERVAL_SECONDS": 900,
        "STORAGE_RECONCILE_BATCH_SIZE": 50,
        "STORAGE_RECONCILE_MAX_ATTEMPTS": 5,
        "STORAGE_RECONCILE_BACKOFF_BASE_SECONDS": 60,
    }
    defaults.update(kwargs)
    ns = SimpleNamespace(**defaults)
    ns.get = lambda key, default=None: getattr(ns, key, default)
    return ns


def test_reconcile_storage_skips_when_interval_zero(monkeypatch):
    """Cap <= 0 -> hook disabled (escape hatch)."""
    sched = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)

    fetch_calls = []

    def _fetch(*a, **kw):
        fetch_calls.append(1)
        return []

    with patch("app.config.settings", new=_settings_stub(STORAGE_RECONCILE_INTERVAL_SECONDS=0)):
        with patch(
            "app.shared.storage_reconciliation.fetch_due_pending_deletes", _fetch
        ):
            sched._reconcile_storage()

    assert fetch_calls == []  # never even invoked


def test_reconcile_storage_skips_when_gate_not_elapsed(monkeypatch):
    """Time gate: tick non rifa il fetch entro STORAGE_RECONCILE_INTERVAL_SECONDS."""
    sched = _make_scheduler()
    now = 1_000_000.0
    sched._last_storage_reconcile = now - 10  # solo 10s fa, gate=900s
    monkeypatch.setattr(time, "time", lambda: now)

    fetch_calls = []

    def _fetch(*a, **kw):
        fetch_calls.append(1)
        return []

    with patch("app.config.settings", new=_settings_stub()):
        with patch(
            "app.shared.storage_reconciliation.fetch_due_pending_deletes", _fetch
        ):
            sched._reconcile_storage()

    assert fetch_calls == []


def test_reconcile_storage_advances_gate_after_run(monkeypatch):
    """`_last_storage_reconcile` viene aggiornato al tick attuale anche
    su empty fetch — evita flooding di fetch a ogni tick."""
    sched = _make_scheduler()
    now = 1_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)

    with patch("app.config.settings", new=_settings_stub()):
        with patch(
            "app.shared.storage_reconciliation.fetch_due_pending_deletes",
            lambda **kw: [],
        ):
            sched._reconcile_storage()

    assert sched._last_storage_reconcile == now


def test_reconcile_storage_marks_orphan_deleted_on_ok(monkeypatch):
    """outcome='ok' → mark_orphan_deleted."""
    sched = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)

    from app.shared.storage_reconciliation import PendingReconciliationRow

    pending = [
        PendingReconciliationRow(
            id="rec-1", domain="file_source",
            storage_key="key/x", operation="delete", attempts=0,
        ),
    ]

    orphan_calls = []
    fail_calls = []
    lost_calls = []

    def _comp(storage_key, *, domain, reason, **kw):
        return "ok"

    with patch("app.config.settings", new=_settings_stub()):
        with patch(
            "app.shared.storage_reconciliation.fetch_due_pending_deletes",
            lambda **kw: pending,
        ), patch(
            "app.shared.storage_compensation.compensating_delete", _comp
        ), patch(
            "app.shared.storage_reconciliation.mark_orphan_deleted",
            lambda rid: orphan_calls.append(rid),
        ), patch(
            "app.shared.storage_reconciliation.mark_failed_attempt",
            lambda rid, **kw: fail_calls.append((rid, kw)),
        ), patch(
            "app.shared.storage_reconciliation.mark_lost",
            lambda rid, **kw: lost_calls.append((rid, kw)),
        ):
            sched._reconcile_storage()

    assert orphan_calls == ["rec-1"]
    assert fail_calls == []
    assert lost_calls == []


def test_reconcile_storage_marks_orphan_deleted_on_skipped(monkeypatch):
    """outcome='skipped' (capability off) → mark_orphan_deleted."""
    sched = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)

    from app.shared.storage_reconciliation import PendingReconciliationRow

    pending = [
        PendingReconciliationRow(
            id="rec-skip", domain="result_blob",
            storage_key="results/p/r.bin", operation="delete", attempts=2,
        ),
    ]

    orphan_calls = []

    with patch("app.config.settings", new=_settings_stub()):
        with patch(
            "app.shared.storage_reconciliation.fetch_due_pending_deletes",
            lambda **kw: pending,
        ), patch(
            "app.shared.storage_compensation.compensating_delete",
            lambda *a, **kw: "skipped",
        ), patch(
            "app.shared.storage_reconciliation.mark_orphan_deleted",
            lambda rid: orphan_calls.append(rid),
        ):
            sched._reconcile_storage()

    assert orphan_calls == ["rec-skip"]


def test_reconcile_storage_marks_failed_attempt_on_failure_below_cap(monkeypatch):
    """outcome='failed' + attempts+1 < max → mark_failed_attempt with next_retry_at."""
    sched = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)

    from app.shared.storage_reconciliation import PendingReconciliationRow

    pending = [
        PendingReconciliationRow(
            id="rec-fail", domain="file_source",
            storage_key="key/x", operation="delete", attempts=1,
        ),
    ]

    fail_calls = []
    lost_calls = []

    with patch("app.config.settings", new=_settings_stub(STORAGE_RECONCILE_MAX_ATTEMPTS=5)):
        with patch(
            "app.shared.storage_reconciliation.fetch_due_pending_deletes",
            lambda **kw: pending,
        ), patch(
            "app.shared.storage_compensation.compensating_delete",
            lambda *a, **kw: "failed",
        ), patch(
            "app.shared.storage_reconciliation.mark_failed_attempt",
            lambda rid, **kw: fail_calls.append((rid, kw)),
        ), patch(
            "app.shared.storage_reconciliation.mark_lost",
            lambda rid, **kw: lost_calls.append((rid, kw)),
        ):
            sched._reconcile_storage()

    # 1 + 1 = 2 < 5 → failed_attempt path.
    assert len(fail_calls) == 1
    rid, kw = fail_calls[0]
    assert rid == "rec-fail"
    assert "next_retry_at" in kw
    assert isinstance(kw["next_retry_at"], datetime)
    assert "error" in kw
    assert lost_calls == []


def test_reconcile_storage_marks_lost_on_max_attempts(monkeypatch):
    """outcome='failed' + (attempts+1) >= max_attempts → mark_lost."""
    sched = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)

    from app.shared.storage_reconciliation import PendingReconciliationRow

    pending = [
        PendingReconciliationRow(
            id="rec-lost", domain="file_source",
            storage_key="key/x", operation="delete", attempts=4,  # next=5
        ),
    ]

    fail_calls = []
    lost_calls = []

    with patch("app.config.settings", new=_settings_stub(STORAGE_RECONCILE_MAX_ATTEMPTS=5)):
        with patch(
            "app.shared.storage_reconciliation.fetch_due_pending_deletes",
            lambda **kw: pending,
        ), patch(
            "app.shared.storage_compensation.compensating_delete",
            lambda *a, **kw: "failed",
        ), patch(
            "app.shared.storage_reconciliation.mark_failed_attempt",
            lambda rid, **kw: fail_calls.append((rid, kw)),
        ), patch(
            "app.shared.storage_reconciliation.mark_lost",
            lambda rid, **kw: lost_calls.append((rid, kw)),
        ):
            sched._reconcile_storage()

    assert fail_calls == []
    assert len(lost_calls) == 1
    rid, kw = lost_calls[0]
    assert rid == "rec-lost"
    assert "max_attempts_exceeded" in kw["error"]


def test_reconcile_storage_skips_when_batch_size_zero(monkeypatch):
    """STORAGE_RECONCILE_BATCH_SIZE <= 0 → no fetch (disabled)."""
    sched = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)

    fetch_calls = []
    with patch("app.config.settings", new=_settings_stub(STORAGE_RECONCILE_BATCH_SIZE=0)):
        with patch(
            "app.shared.storage_reconciliation.fetch_due_pending_deletes",
            lambda **kw: fetch_calls.append(1) or [],
        ):
            sched._reconcile_storage()

    assert fetch_calls == []


def test_reconcile_storage_swallows_compensating_delete_exception(monkeypatch):
    """Even if compensating_delete (contract violation) raises, the
    per-row try/except swallows the exception and the loop continues
    to next row. The raising row stays `pending` (unchanged) for the
    next tick to retry — no spurious mark_failed_attempt that would
    risk double-counting attempts."""
    sched = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)

    from app.shared.storage_reconciliation import PendingReconciliationRow

    pending = [
        PendingReconciliationRow(
            id="rec-A", domain="file_source",
            storage_key="key/A", operation="delete", attempts=0,
        ),
        PendingReconciliationRow(
            id="rec-B", domain="file_source",
            storage_key="key/B", operation="delete", attempts=0,
        ),
    ]

    orphan_calls = []
    fail_calls = []
    lost_calls = []

    def _comp(storage_key, *, domain, reason, **kw):
        if storage_key == "key/A":
            raise RuntimeError("network blip")
        return "ok"

    with patch("app.config.settings", new=_settings_stub()):
        with patch(
            "app.shared.storage_reconciliation.fetch_due_pending_deletes",
            lambda **kw: pending,
        ), patch(
            "app.shared.storage_compensation.compensating_delete", _comp
        ), patch(
            "app.shared.storage_reconciliation.mark_orphan_deleted",
            lambda rid: orphan_calls.append(rid),
        ), patch(
            "app.shared.storage_reconciliation.mark_failed_attempt",
            lambda rid, **kw: fail_calls.append((rid, kw)),
        ), patch(
            "app.shared.storage_reconciliation.mark_lost",
            lambda rid, **kw: lost_calls.append((rid, kw)),
        ):
            # Must NOT raise.
            sched._reconcile_storage()

    # rec-B succeeded normally.
    assert "rec-B" in orphan_calls
    # rec-A raised: per-row try/except swallowed it; no mark_*
    # call was issued for rec-A (entry stays pending, next tick
    # retries). Don't double-count attempts on internal blip.
    assert "rec-A" not in orphan_calls
    assert all(rid != "rec-A" for rid, _ in fail_calls)
    assert all(rid != "rec-A" for rid, _ in lost_calls)


# Source-bound: tick wires _reconcile_storage


def test_tick_wires_reconcile_storage_call():
    """`PlenoraBeatScheduler.tick()` source must invoke
    `_reconcile_storage()`. Source-bound check (tick imports DB at
    runtime; this test is hermetic)."""
    from pathlib import Path

    src_path = (
        Path(__file__).resolve().parent.parent.parent
        / "workers" / "beat_scheduler.py"
    )
    src = src_path.read_text(encoding="utf-8")
    assert "self._reconcile_storage()" in src
    assert "def _reconcile_storage" in src
    assert "_last_storage_reconcile" in src
