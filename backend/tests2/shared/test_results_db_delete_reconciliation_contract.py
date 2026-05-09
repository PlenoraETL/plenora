"""
Contract test BE-GO-READY-STORAGE-ENGINE-007 —

result-blob delete reconciliation integration.
"""

from __future__ import annotations

import os

# Ensure DATABASE_URL set before any pg_connection import.
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

from contextlib import contextmanager
from typing import List, Tuple
from unittest.mock import MagicMock

import pytest

from app.core.results import cleanup as results_cleanup


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


# Helpers — fake transaction + cursor


class _FakeCursor:
    """Records every `execute` call + supports `fetchone`/`fetchall`
    with caller-controlled results.

    `record_pending` calls `cur.execute(INSERT...) + cur.fetchone()`
    expecting `{"id": <uuid>}` — the fixture stages a returning_id
    queue so the test can pin the IDs.
    """

    def __init__(
        self,
        *,
        select_storage_key=None,
        select_bulk_rows=None,
        delete_rowcount=1,
        returning_ids=None,
    ):
        self.executed: List[Tuple[str, tuple]] = []
        self._next_fetchone = []
        self._next_fetchall = []
        # `delete_result` flow: 1st fetchone returns the SELECT result,
        # subsequent fetchone's return record_pending RETURNING id rows.
        if select_storage_key is not None:
            self._next_fetchone.append(select_storage_key)
        # bulk path doesn't use fetchone on the cursor (uses module-level
        # fetchall before the tx). Stage record_pending RETURNING ids.
        for ret_id in (returning_ids or []):
            self._next_fetchone.append({"id": ret_id})
        self._delete_rowcount = delete_rowcount
        self._select_bulk_rows = select_bulk_rows
        self.rowcount = 0

    def execute(self, sql, params=()):
        self.executed.append((sql, tuple(params)))
        # Update rowcount on DELETE to match the contract.
        if "DELETE FROM results" in sql:
            self.rowcount = self._delete_rowcount

    def fetchone(self):
        if self._next_fetchone:
            return self._next_fetchone.pop(0)
        return None

    def fetchall(self):
        if self._next_fetchall:
            return self._next_fetchall.pop(0)
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _patched_transaction(cursor: _FakeCursor):
    """Build a context-manager that mimics `pg_connection.transaction`.

    Yields a `_FakeConn`; tracks whether commit was reached (no
    exception) so tests can assert atomicity behavior.
    """
    state = {"committed": False, "raised": None}

    @contextmanager
    def _cm():
        conn = _FakeConn(cursor)
        try:
            yield conn
            state["committed"] = True
        except Exception as e:
            state["raised"] = e
            raise

    return _cm, state


# delete_result (single)


def test_delete_result_records_pending_inside_transaction_and_marks_done(
    monkeypatch,
):
    """Happy path: S3-backed row → record_pending in tx, DELETE in tx,
    compensating_delete returns "ok" → mark_done."""
    cursor = _FakeCursor(
        select_storage_key={"storage_key": "results/p1/r1.bin"},
        delete_rowcount=1,
        returning_ids=["recon-1"],
    )
    cm_factory, state = _patched_transaction(cursor)
    monkeypatch.setattr(results_cleanup, "transaction", cm_factory)

    comp_calls = []
    def _fake_comp(storage_key, *, domain, reason, extras=None, **_):
        comp_calls.append((storage_key, domain, reason, dict(extras or {})))
        return "ok"

    mark_done_calls = []
    mark_failed_calls = []
    monkeypatch.setattr(
        "app.shared.storage_compensation.compensating_delete", _fake_comp
    )
    monkeypatch.setattr(
        "app.core.resources.storage_reconciliation.mark_done",
        lambda rid: mark_done_calls.append(rid),
    )
    monkeypatch.setattr(
        "app.core.resources.storage_reconciliation.mark_failed_attempt",
        lambda rid, *, error: mark_failed_calls.append((rid, error)),
    )

    deleted = results_cleanup.delete_result("r1")

    assert deleted is True
    assert state["committed"] is True

    # SQL trace inside the transaction: SELECT FOR UPDATE → record_pending
    # INSERT → DELETE.
    sqls = [s for s, _ in cursor.executed]
    assert any("SELECT storage_key FROM results" in s and "FOR UPDATE" in s
               for s in sqls)
    assert any("INSERT INTO storage_reconciliation_log" in s
               for s in sqls), "record_pending must INSERT inside tx"
    assert any("DELETE FROM results" in s for s in sqls)

    # SELECT must come before INSERT must come before DELETE.
    indices = {
        "select": next(i for i, (s, _) in enumerate(cursor.executed)
                       if "SELECT storage_key FROM results" in s),
        "insert": next(i for i, (s, _) in enumerate(cursor.executed)
                       if "INSERT INTO storage_reconciliation_log" in s),
        "delete": next(i for i, (s, _) in enumerate(cursor.executed)
                       if "DELETE FROM results" in s),
    }
    assert indices["select"] < indices["insert"] < indices["delete"]

    # Compensating delete called AFTER commit, with result_blob domain.
    assert len(comp_calls) == 1
    storage_key, domain, reason, extras = comp_calls[0]
    assert storage_key == "results/p1/r1.bin"
    assert domain == "result_blob"
    assert reason == "row_delete_post_commit"
    assert extras.get("result_id") == "r1"

    # Outcome OK → mark_done called with the freshly-issued reconciliation_id.
    assert mark_done_calls == ["recon-1"]
    assert mark_failed_calls == []


def test_delete_result_failed_compensation_marks_failed_attempt(monkeypatch):
    """outcome=failed → mark_failed_attempt; ADR-0011 posture
    preserved (deleted=True, no exception propagated)."""
    cursor = _FakeCursor(
        select_storage_key={"storage_key": "results/p1/r1.bin"},
        delete_rowcount=1,
        returning_ids=["recon-2"],
    )
    cm_factory, state = _patched_transaction(cursor)
    monkeypatch.setattr(results_cleanup, "transaction", cm_factory)

    mark_done_calls = []
    mark_failed_calls = []
    monkeypatch.setattr(
        "app.shared.storage_compensation.compensating_delete",
        lambda *a, **kw: "failed",
    )
    monkeypatch.setattr(
        "app.core.resources.storage_reconciliation.mark_done",
        lambda rid: mark_done_calls.append(rid),
    )
    monkeypatch.setattr(
        "app.core.resources.storage_reconciliation.mark_failed_attempt",
        lambda rid, *, error: mark_failed_calls.append((rid, error)),
    )

    deleted = results_cleanup.delete_result("r1")

    assert deleted is True
    assert state["committed"] is True
    assert mark_done_calls == []
    assert len(mark_failed_calls) == 1
    rid, error = mark_failed_calls[0]
    assert rid == "recon-2"
    assert "compensating_delete_outcome=failed" in error


def test_delete_result_skipped_outcome_marks_done(monkeypatch):
    """outcome=skipped (capability off) → mark_done. No orphan
    possible to reconcile."""
    cursor = _FakeCursor(
        select_storage_key={"storage_key": "results/p1/r1.bin"},
        delete_rowcount=1,
        returning_ids=["recon-3"],
    )
    cm_factory, _ = _patched_transaction(cursor)
    monkeypatch.setattr(results_cleanup, "transaction", cm_factory)

    mark_done_calls = []
    mark_failed_calls = []
    monkeypatch.setattr(
        "app.shared.storage_compensation.compensating_delete",
        lambda *a, **kw: "skipped",
    )
    monkeypatch.setattr(
        "app.core.resources.storage_reconciliation.mark_done",
        lambda rid: mark_done_calls.append(rid),
    )
    monkeypatch.setattr(
        "app.core.resources.storage_reconciliation.mark_failed_attempt",
        lambda rid, *, error: mark_failed_calls.append((rid, error)),
    )

    deleted = results_cleanup.delete_result("r1")

    assert deleted is True
    assert mark_done_calls == ["recon-3"]
    assert mark_failed_calls == []


def test_delete_result_legacy_bytea_row_skips_reconciliation(monkeypatch):
    """storage_key=None (BYTEA legacy) → skip record_pending and
    compensating_delete. Backward-compat invariato."""
    cursor = _FakeCursor(
        select_storage_key={"storage_key": None},
        delete_rowcount=1,
    )
    cm_factory, _ = _patched_transaction(cursor)
    monkeypatch.setattr(results_cleanup, "transaction", cm_factory)

    comp_called = MagicMock()
    monkeypatch.setattr(
        "app.shared.storage_compensation.compensating_delete", comp_called
    )

    deleted = results_cleanup.delete_result("r-legacy")

    assert deleted is True
    # No INSERT into reconciliation log for BYTEA path.
    sqls = [s for s, _ in cursor.executed]
    assert all("INSERT INTO storage_reconciliation_log" not in s
               for s in sqls)
    # Compensating delete NOT called for BYTEA path.
    comp_called.assert_not_called()


def test_delete_result_row_not_found_skips_reconciliation(monkeypatch):
    """SELECT returns None (row already gone) → DELETE rowcount 0 →
    deleted=False → no reconciliation, no compensating delete."""
    cursor = _FakeCursor(
        select_storage_key=None,  # SELECT returns None
        delete_rowcount=0,
    )
    cm_factory, _ = _patched_transaction(cursor)
    monkeypatch.setattr(results_cleanup, "transaction", cm_factory)

    comp_called = MagicMock()
    monkeypatch.setattr(
        "app.shared.storage_compensation.compensating_delete", comp_called
    )

    deleted = results_cleanup.delete_result("r-gone")

    assert deleted is False
    sqls = [s for s, _ in cursor.executed]
    assert all("INSERT INTO storage_reconciliation_log" not in s
               for s in sqls)
    comp_called.assert_not_called()


# delete_results_for_project (bulk)


def test_delete_results_for_project_records_pending_per_key_in_one_tx(
    monkeypatch,
):
    """Bulk path: pre-fetch (id, storage_key), record_pending N times
    in one transaction, DELETE all-by-project, then per-row
    compensating_delete + mark_done."""
    rows = [
        {"id": "r1", "storage_key": "results/p1/r1.bin"},
        {"id": "r2", "storage_key": "results/p1/r2.bin"},
    ]
    monkeypatch.setattr(
        results_cleanup, "fetchall", lambda *a, **kw: rows
    )

    cursor = _FakeCursor(
        delete_rowcount=2,
        returning_ids=["recon-r1", "recon-r2"],
    )
    cm_factory, state = _patched_transaction(cursor)
    monkeypatch.setattr(results_cleanup, "transaction", cm_factory)

    comp_calls = []
    def _fake_comp(storage_key, *, domain, reason, extras=None, **_):
        comp_calls.append((storage_key, domain, reason, dict(extras or {})))
        return "ok"

    mark_done_calls = []
    monkeypatch.setattr(
        "app.shared.storage_compensation.compensating_delete", _fake_comp
    )
    monkeypatch.setattr(
        "app.core.resources.storage_reconciliation.mark_done",
        lambda rid: mark_done_calls.append(rid),
    )
    monkeypatch.setattr(
        "app.core.resources.storage_reconciliation.mark_failed_attempt",
        lambda rid, *, error: None,
    )

    rowcount = results_cleanup.delete_results_for_project("p1")

    assert rowcount == 2
    assert state["committed"] is True

    # 2 INSERT pending + 1 DELETE inside the transaction.
    sqls = [s for s, _ in cursor.executed]
    insert_count = sum(
        1 for s in sqls
        if "INSERT INTO storage_reconciliation_log" in s
    )
    delete_count = sum(1 for s in sqls if "DELETE FROM results" in s)
    assert insert_count == 2
    assert delete_count == 1

    # All 2 inserts come BEFORE the delete (atomic batch).
    delete_idx = next(i for i, (s, _) in enumerate(cursor.executed)
                      if "DELETE FROM results" in s)
    insert_indices = [
        i for i, (s, _) in enumerate(cursor.executed)
        if "INSERT INTO storage_reconciliation_log" in s
    ]
    assert all(i < delete_idx for i in insert_indices)

    # Compensating delete called per key with correct domain + extras.
    assert len(comp_calls) == 2
    keys = sorted(c[0] for c in comp_calls)
    assert keys == ["results/p1/r1.bin", "results/p1/r2.bin"]
    for _, domain, reason, extras in comp_calls:
        assert domain == "result_blob"
        assert reason == "row_delete_post_commit"
        assert extras.get("project_id") == "p1"
        assert extras.get("result_id") in ("r1", "r2")

    # mark_done called with both reconciliation_ids.
    assert sorted(mark_done_calls) == ["recon-r1", "recon-r2"]


def test_delete_results_for_project_no_s3_rows_skips_reconciliation(
    monkeypatch,
):
    """Project with only BYTEA legacy rows (or empty): pre-fetch
    returns []; no record_pending, no compensating_delete."""
    monkeypatch.setattr(
        results_cleanup, "fetchall", lambda *a, **kw: []
    )

    cursor = _FakeCursor(delete_rowcount=3)
    cm_factory, _ = _patched_transaction(cursor)
    monkeypatch.setattr(results_cleanup, "transaction", cm_factory)

    comp_called = MagicMock()
    monkeypatch.setattr(
        "app.shared.storage_compensation.compensating_delete", comp_called
    )

    rowcount = results_cleanup.delete_results_for_project("p-empty")

    # Still deletes legacy BYTEA rows (rowcount reflects the DELETE).
    assert rowcount == 3
    sqls = [s for s, _ in cursor.executed]
    assert all("INSERT INTO storage_reconciliation_log" not in s
               for s in sqls)
    comp_called.assert_not_called()


def test_delete_results_for_project_per_key_failure_isolates_others(
    monkeypatch,
):
    """outcome=failed on row A doesn't prevent row B's mark_done.
    ADR-0011 posture preserved: bulk rowcount unchanged."""
    rows = [
        {"id": "rA", "storage_key": "results/p1/rA.bin"},
        {"id": "rB", "storage_key": "results/p1/rB.bin"},
    ]
    monkeypatch.setattr(
        results_cleanup, "fetchall", lambda *a, **kw: rows
    )

    cursor = _FakeCursor(
        delete_rowcount=2, returning_ids=["recon-rA", "recon-rB"]
    )
    cm_factory, _ = _patched_transaction(cursor)
    monkeypatch.setattr(results_cleanup, "transaction", cm_factory)

    def _comp_dispatch(storage_key, **_):
        return "failed" if "rA" in storage_key else "ok"

    mark_done_calls = []
    mark_failed_calls = []
    monkeypatch.setattr(
        "app.shared.storage_compensation.compensating_delete", _comp_dispatch
    )
    monkeypatch.setattr(
        "app.core.resources.storage_reconciliation.mark_done",
        lambda rid: mark_done_calls.append(rid),
    )
    monkeypatch.setattr(
        "app.core.resources.storage_reconciliation.mark_failed_attempt",
        lambda rid, *, error: mark_failed_calls.append((rid, error)),
    )

    rowcount = results_cleanup.delete_results_for_project("p1")

    assert rowcount == 2
    # rA failed: mark_failed_attempt; rB ok: mark_done.
    assert mark_done_calls == ["recon-rB"]
    assert len(mark_failed_calls) == 1
    assert mark_failed_calls[0][0] == "recon-rA"
    assert "compensating_delete_outcome=failed" in mark_failed_calls[0][1]


def test_result_cleanup_exports_live_only_at_canonical_boundary():
    assert results_cleanup.delete_result.__module__ == "app.core.results.cleanup"
    assert (
        results_cleanup.delete_results_for_project.__module__
        == "app.core.results.cleanup"
    )
