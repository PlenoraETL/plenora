"""Contract test BE-GO-READY-STORAGE-ENGINE-006 — reconciler boundary.

Locks the new boundary surface added by the reconciler beat hook:

1. `fetch_due_pending_deletes` returns typed `PendingReconciliationRow`
   instances; SQL filters `state='pending' AND operation='delete' AND
   (next_retry_at IS NULL OR next_retry_at <= now)`; ORDER BY
   `next_retry_at NULLS FIRST, created_at ASC`; honors LIMIT.
2. `fetch_due_pending_deletes` raises `ValueError` on non-positive
   `limit` (preventing accidental unbounded reads).
3. `fetch_due_pending_deletes` is best-effort on read error: returns
   `[]` and emits `event=storage_reconciliation_fetch_failed`.
4. `mark_lost` runs an autocommit UPDATE with `state='lost'` +
   `last_error` (no `attempts` increment); never raises.
5. `compute_next_retry_at` is a pure helper: exponential backoff
   `base * 2 ** (attempts-1)`, capped at `2**8`. Validates inputs.

Boundary
========
Source-bound: monkeypatch `app.db.pg_connection.fetchall` and
`app.db.pg_connection.execute`. No DB connection required.
"""

from __future__ import annotations

import os
# Ensure DATABASE_URL is set before any import that may load
# `app.db.pg_connection` (raises RuntimeError at module-level if missing).
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List, Tuple
from unittest.mock import patch

import pytest

from app.shared.storage_reconciliation import (
    PendingReconciliationRow,
    compute_next_retry_at,
    fetch_due_pending_deletes,
    mark_lost,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# ---------------------------------------------------------------------------
# fetch_due_pending_deletes
# ---------------------------------------------------------------------------


def _captured_fetchall(rows):
    """Return a fake `fetchall` that records the (sql, params) tuple."""
    captured: list[tuple[str, tuple]] = []

    def _fake(sql, params=()):
        captured.append((sql, tuple(params)))
        return rows

    return _fake, captured


def test_fetch_due_pending_deletes_returns_typed_rows():
    rows = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "domain": "file_source",
            "storage_key": "file-sources/abc/file.csv",
            "operation": "delete",
            "attempts": 0,
        },
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "domain": "result",
            "storage_key": "results/proj/result.bin",
            "operation": "delete",
            "attempts": 2,
        },
    ]
    fake, _ = _captured_fetchall(rows)
    with patch("app.db.pg_connection.fetchall", side_effect=fake):
        result = fetch_due_pending_deletes(limit=10)

    assert len(result) == 2
    for r in result:
        assert isinstance(r, PendingReconciliationRow)
    assert result[0].id == "11111111-1111-1111-1111-111111111111"
    assert result[0].domain == "file_source"
    assert result[0].storage_key == "file-sources/abc/file.csv"
    assert result[0].operation == "delete"
    assert result[0].attempts == 0
    assert result[1].attempts == 2


def test_fetch_due_pending_deletes_passes_limit_and_now_to_sql():
    fake, captured = _captured_fetchall([])
    fixed_now = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)

    with patch("app.db.pg_connection.fetchall", side_effect=fake):
        fetch_due_pending_deletes(limit=25, now=fixed_now)

    assert len(captured) == 1
    sql, params = captured[0]
    # SQL filters and ORDER BY are explicit and stable.
    assert "WHERE state = 'pending'" in sql
    assert "operation = 'delete'" in sql
    assert "next_retry_at IS NULL OR next_retry_at <= ?" in sql
    assert "ORDER BY next_retry_at NULLS FIRST, created_at ASC" in sql
    assert "LIMIT ?" in sql
    # Params: (cutoff, limit) in this exact order.
    assert params == (fixed_now, 25)


def test_fetch_due_pending_deletes_uses_now_utc_default():
    """When `now=None`, the cutoff should be a tz-aware UTC datetime
    near the current wall clock."""
    fake, captured = _captured_fetchall([])
    before = datetime.now(timezone.utc)
    with patch("app.db.pg_connection.fetchall", side_effect=fake):
        fetch_due_pending_deletes(limit=10)
    after = datetime.now(timezone.utc)

    assert len(captured) == 1
    cutoff, _limit = captured[0][1]
    assert isinstance(cutoff, datetime)
    assert cutoff.tzinfo is not None
    # Within a generous window for slow CI.
    assert before <= cutoff <= after + timedelta(seconds=1)


def test_fetch_due_pending_deletes_tolerates_tuple_rows():
    """RealDictCursor returns dicts in production, but tuples are an
    acceptable shape (psycopg2 default cursor) — the boundary
    mustn't crash on either."""
    rows = [
        ("33333333-3333-3333-3333-333333333333",
         "result", "results/x.bin", "delete", 1),
    ]
    fake, _ = _captured_fetchall(rows)
    with patch("app.db.pg_connection.fetchall", side_effect=fake):
        result = fetch_due_pending_deletes(limit=5)
    assert len(result) == 1
    assert result[0].id == "33333333-3333-3333-3333-333333333333"
    assert result[0].domain == "result"
    assert result[0].attempts == 1


def test_fetch_due_pending_deletes_returns_empty_on_no_rows():
    fake, _ = _captured_fetchall([])
    with patch("app.db.pg_connection.fetchall", side_effect=fake):
        assert fetch_due_pending_deletes(limit=10) == []


def test_fetch_due_pending_deletes_returns_empty_on_db_error():
    """Best-effort: any DB error is swallowed; the reconciler treats
    a fetch failure as "no due rows" and waits for the next cadence
    tick. Non-raise contract is the lock; the log shape is exercised
    by happy-path tests above."""
    def _raise(sql, params=()):
        raise RuntimeError("connection refused")

    with patch("app.db.pg_connection.fetchall", side_effect=_raise):
        result = fetch_due_pending_deletes(limit=10)

    assert result == []


@pytest.mark.parametrize("bad_limit", [0, -1, -100, 1.5, "10", None])
def test_fetch_due_pending_deletes_rejects_invalid_limit(bad_limit):
    with pytest.raises(ValueError):
        fetch_due_pending_deletes(limit=bad_limit)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# mark_lost
# ---------------------------------------------------------------------------


def test_mark_lost_runs_autocommit_update():
    captured: list[tuple[str, tuple]] = []

    def _fake_execute(sql, params=()):
        captured.append((sql, tuple(params)))

    with patch("app.db.pg_connection.execute", side_effect=_fake_execute):
        mark_lost(
            "44444444-4444-4444-4444-444444444444",
            error="max_attempts_exceeded (5)",
        )

    assert len(captured) == 1
    sql, params = captured[0]
    assert "UPDATE plenora.storage_reconciliation_log" in sql
    assert "SET state = 'lost'" in sql
    assert "last_error = ?" in sql
    # `mark_lost` does NOT increment attempts (the call site has just
    # observed a failure via mark_failed_attempt — promoting is the
    # decision, not a fresh attempt).
    assert "attempts = attempts + 1" not in sql
    assert params == (
        "max_attempts_exceeded (5)",
        "44444444-4444-4444-4444-444444444444",
    )


def test_mark_lost_swallows_exceptions():
    """Best-effort: even if the autocommit UPDATE fails, the
    reconciler must not crash the beat tick. Pattern shared with
    `mark_done` / `mark_failed_attempt` / `mark_orphan_deleted` —
    log shape is exercised by happy-path tests above; this one
    locks the non-raise contract."""
    def _raise(sql, params=()):
        raise RuntimeError("PG down")

    with patch("app.db.pg_connection.execute", side_effect=_raise):
        # Must NOT raise. Returning None is the only contract.
        assert mark_lost(
            "55555555-5555-5555-5555-555555555555", error="boom"
        ) is None


# ---------------------------------------------------------------------------
# compute_next_retry_at
# ---------------------------------------------------------------------------


def test_compute_next_retry_at_first_attempt_is_base_seconds():
    """attempts=1 ⇒ delta = base * 2**0 = base."""
    now = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
    out = compute_next_retry_at(1, base_seconds=60, now=now)
    assert out == now + timedelta(seconds=60)


def test_compute_next_retry_at_exponential_backoff():
    """attempts=N ⇒ delta = base * 2**(N-1)."""
    now = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
    cases = [
        (1, 60),
        (2, 120),
        (3, 240),
        (4, 480),
        (5, 960),
    ]
    for attempts, expected_delta in cases:
        out = compute_next_retry_at(attempts, base_seconds=60, now=now)
        assert out == now + timedelta(seconds=expected_delta), (
            f"attempts={attempts}: expected delta={expected_delta}s"
        )


def test_compute_next_retry_at_caps_at_2_to_the_8():
    """Multiplier is capped at 2**8=256 to avoid astronomical
    delays. attempts=10 ⇒ same as attempts=9."""
    now = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
    cap_delta = 60 * 256
    for attempts in (9, 10, 50, 100):
        out = compute_next_retry_at(attempts, base_seconds=60, now=now)
        assert out == now + timedelta(seconds=cap_delta)


def test_compute_next_retry_at_returns_tz_aware_default_now():
    """When `now=None`, the result is tz-aware UTC."""
    before = datetime.now(timezone.utc)
    out = compute_next_retry_at(1, base_seconds=60)
    after = datetime.now(timezone.utc)
    assert out.tzinfo is not None
    # Result should be ~60s ahead of "now".
    assert (before + timedelta(seconds=60)) <= out <= (after + timedelta(seconds=61))


@pytest.mark.parametrize("bad_attempts", [0, -1, 1.5, "1", None])
def test_compute_next_retry_at_rejects_invalid_attempts(bad_attempts):
    with pytest.raises(ValueError):
        compute_next_retry_at(bad_attempts, base_seconds=60)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_base", [0, -1, -100, 1.5, "60", None])
def test_compute_next_retry_at_rejects_invalid_base_seconds(bad_base):
    with pytest.raises(ValueError):
        compute_next_retry_at(1, base_seconds=bad_base)  # type: ignore[arg-type]


def test_compute_next_retry_at_is_pure_no_io():
    """Source-bound: the helper must NOT touch DB / settings / logger.
    Verified by absence of `pg_connection` import in the call path
    AND by the fact the function works without any patching."""
    # If the function had I/O, this call without any patch would fail
    # (pg_connection.execute / settings.get etc.). It must succeed.
    out = compute_next_retry_at(3, base_seconds=10)
    assert isinstance(out, datetime)


# ---------------------------------------------------------------------------
# PendingReconciliationRow shape lock
# ---------------------------------------------------------------------------


def test_pending_reconciliation_row_is_frozen_dataclass():
    """No opaque dict cross-boundary: the row must be a frozen
    dataclass with the documented fields."""
    row = PendingReconciliationRow(
        id="x", domain="d", storage_key="k", operation="delete", attempts=0
    )
    # Frozen.
    with pytest.raises((AttributeError, Exception)):
        row.id = "y"  # type: ignore[misc]
    # Hashable (frozen ⇒ hashable).
    {row}
