"""Contract tests BE-GO-READY-STORAGE-ENGINE-008-RECONCILIATION-OPERATIONAL-GUARDRAILS.

Pin the boundary contract for the two new helpers in
`app.shared.storage_reconciliation`:

  - `count_active_by_state_domain() -> list[ReconciliationStateCount]`
    aggregates `pending` and `lost` rows by `(state, domain)`.
    Terminals (`done` / `orphan_deleted`) are intentionally
    excluded — they are the audit trail, not operator concern.
  - `prune_terminal_older_than(*, older_than, limit) -> int`
    deletes ONLY `done` / `orphan_deleted` rows older than the
    cutoff, bounded by `limit` per call. PG `DELETE WHERE id IN
    (SELECT ... LIMIT n)` shape (PG does not support
    `DELETE ... LIMIT` directly).

Both helpers are best-effort on read/write errors (warning log +
graceful return) because the caller is the beat hook; an
exception in operational guardrails must not crash the tick.

Plus the typed dataclass `ReconciliationStateCount` (frozen).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Tuple

import pytest

from app.shared.storage_reconciliation import (
    ReconciliationStateCount,
    count_active_by_state_domain,
    prune_terminal_older_than,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount

    def fetchone(self):
        return None


def _patch_fetchall(monkeypatch, *, response, raise_exc: Exception | None = None):
    calls: List[Tuple[str, tuple]] = []

    def _fake_fetchall(sql, params=()):
        calls.append((sql, tuple(params)))
        if raise_exc is not None:
            raise raise_exc
        return list(response)

    import app.db.pg_connection as pg_conn

    monkeypatch.setattr(pg_conn, "fetchall", _fake_fetchall)
    return calls


def _patch_execute(monkeypatch, *, rowcount: int = 0, raise_exc: Exception | None = None):
    calls: List[Tuple[str, tuple]] = []

    def _fake_execute(sql, params=()):
        calls.append((sql, tuple(params)))
        if raise_exc is not None:
            raise raise_exc
        return _FakeResult(rowcount)

    import app.db.pg_connection as pg_conn

    monkeypatch.setattr(pg_conn, "execute", _fake_execute)
    return calls


# ---------------------------------------------------------------------------
# ReconciliationStateCount
# ---------------------------------------------------------------------------


def test_reconciliation_state_count_is_frozen():
    c = ReconciliationStateCount(state="pending", domain="file_source", count=3)
    with pytest.raises(Exception):
        c.count = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# count_active_by_state_domain
# ---------------------------------------------------------------------------


def test_count_active_by_state_domain_filters_terminals_in_sql(monkeypatch):
    calls = _patch_fetchall(monkeypatch, response=[])
    count_active_by_state_domain()
    assert len(calls) == 1
    sql, params = calls[0]
    # Hard SQL invariants: only pending/lost are aggregated; terminals
    # done/orphan_deleted are excluded by construction.
    assert "WHERE state IN ('pending', 'lost')" in sql
    assert "GROUP BY state, domain" in sql
    assert "ORDER BY state ASC, domain ASC" in sql
    # No params (terminals filtered server-side, no caller knobs).
    assert params == ()


def test_count_active_by_state_domain_returns_typed_rows(monkeypatch):
    _patch_fetchall(
        monkeypatch,
        response=[
            {"state": "pending", "domain": "file_source", "count": 4},
            {"state": "lost", "domain": "result_blob", "count": 2},
        ],
    )
    out = count_active_by_state_domain()
    assert isinstance(out, list)
    assert all(isinstance(c, ReconciliationStateCount) for c in out)
    assert out[0].state == "pending"
    assert out[0].domain == "file_source"
    assert out[0].count == 4
    assert out[1].state == "lost"
    assert out[1].count == 2


def test_count_active_by_state_domain_handles_tuple_rows(monkeypatch):
    _patch_fetchall(
        monkeypatch,
        response=[("pending", "file_source", 1)],
    )
    out = count_active_by_state_domain()
    assert len(out) == 1
    assert out[0] == ReconciliationStateCount(
        state="pending", domain="file_source", count=1,
    )


def test_count_active_by_state_domain_swallows_read_error(monkeypatch):
    _patch_fetchall(
        monkeypatch, response=[], raise_exc=RuntimeError("DB down"),
    )
    # Best-effort: must NOT raise; returns []
    assert count_active_by_state_domain() == []


def test_count_active_by_state_domain_empty_table(monkeypatch):
    _patch_fetchall(monkeypatch, response=[])
    assert count_active_by_state_domain() == []


# ---------------------------------------------------------------------------
# prune_terminal_older_than
# ---------------------------------------------------------------------------


def test_prune_terminal_older_than_issues_canonical_delete(monkeypatch):
    calls = _patch_execute(monkeypatch, rowcount=7)
    cutoff = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    out = prune_terminal_older_than(older_than=cutoff, limit=100)
    assert out == 7
    assert len(calls) == 1
    sql, params = calls[0]
    # Hard SQL invariants: terminals only, bounded LIMIT subquery,
    # oldest-first ordering.
    assert "DELETE FROM plenora.storage_reconciliation_log" in sql
    assert "WHERE id IN (" in sql
    assert "WHERE state IN ('done', 'orphan_deleted')" in sql
    assert "updated_at < ?" in sql
    assert "ORDER BY updated_at ASC" in sql
    assert "LIMIT ?" in sql
    # Params: cutoff first, limit last (matches placeholder order).
    assert params == (cutoff, 100)


def test_prune_terminal_older_than_rejects_non_positive_limit():
    cutoff = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="limit must be int > 0"):
        prune_terminal_older_than(older_than=cutoff, limit=0)
    with pytest.raises(ValueError, match="limit must be int > 0"):
        prune_terminal_older_than(older_than=cutoff, limit=-1)


def test_prune_terminal_older_than_swallows_db_error(monkeypatch):
    _patch_execute(
        monkeypatch, rowcount=0, raise_exc=RuntimeError("constraint"),
    )
    cutoff = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    # Best-effort: must NOT raise; returns 0
    assert prune_terminal_older_than(older_than=cutoff, limit=100) == 0


def test_prune_terminal_older_than_returns_zero_when_no_rows(monkeypatch):
    _patch_execute(monkeypatch, rowcount=0)
    cutoff = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    assert prune_terminal_older_than(older_than=cutoff, limit=10) == 0


def test_prune_terminal_older_than_does_not_touch_pending_or_lost(monkeypatch):
    """Source-bound: the SQL hard-codes the terminal state set so a
    bug that broadens the predicate to `pending` / `lost` is caught
    here. The check is a substring assertion on the canonical SQL,
    NOT a behavioral simulation (the helper does not see the rows;
    only the DB does)."""
    calls = _patch_execute(monkeypatch, rowcount=0)
    cutoff = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    prune_terminal_older_than(older_than=cutoff, limit=10)
    sql, _ = calls[0]
    # Must NEVER include pending/lost in the prune predicate.
    assert "'pending'" not in sql
    assert "'lost'" not in sql
