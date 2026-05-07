"""
Integration test - BE-GO-READY-STORAGE-ENGINE-005:

`delete_file_source` ←→ reconciliation log boundary.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Fixtures


_NO_ROW = object()


def _make_tx_patcher(
    *,
    current_storage_key,
    siblings,
    delete_rowcount,
    record_pending_id="recon-uuid-1",
):
    """Build a `transaction()` patcher whose mocked cursor yields
    fetchone responses in this order:

      1. SELECT storage_key FOR UPDATE -> {"storage_key": ...} or None
      2. (only if last-reference path) INSERT ... RETURNING id ->
         {"id": record_pending_id}

    And fetchall responses in this order:

      1. SELECT siblings FOR UPDATE -> [{"id": s} for s in siblings]
         (only when `current_storage_key` is truthy).
    """
    cursor = MagicMock()

    fetchone_responses = []
    fetchall_responses = []

    if current_storage_key is _NO_ROW:
        fetchone_responses.append(None)
    else:
        fetchone_responses.append({"storage_key": current_storage_key})
        if current_storage_key:
            fetchall_responses.append([{"id": s} for s in siblings])
            # If no siblings -> last-reference path -> record_pending
            # will INSERT ... RETURNING id -> 2nd fetchone response.
            if not siblings:
                fetchone_responses.append({"id": record_pending_id})

    cursor.fetchone = MagicMock(side_effect=fetchone_responses)
    cursor.fetchall = MagicMock(side_effect=fetchall_responses)
    cursor.rowcount = delete_rowcount

    @contextmanager
    def cursor_cm():
        yield cursor

    conn = MagicMock()
    conn.cursor = MagicMock(side_effect=cursor_cm)

    @contextmanager
    def tx_cm():
        yield conn

    return (
        patch("app.db.config.file_sources.transaction", side_effect=tx_cm),
        cursor,
        conn,
    )


def _executed_sqls(cursor):
    return [c.args[0] for c in cursor.execute.call_args_list]


# 1. Last-reference: record_pending called inside transaction


def test_last_reference_records_pending_inside_transaction():
    from app.db.config import file_sources as fs_db

    patcher, cursor, _conn = _make_tx_patcher(
        current_storage_key="file-sources/file_abc/x.csv",
        siblings=[],
        delete_rowcount=1,
        record_pending_id="aaaa1111-bbbb-2222-cccc-333344445555",
    )

    record_pending_calls = []
    mark_done_calls = []
    mark_failed_calls = []

    def _spy_record_pending(cur, **kwargs):
        from app.shared.storage_reconciliation import ReconciliationEntryRef
        record_pending_calls.append((cur, kwargs))
        return ReconciliationEntryRef(
            id="aaaa1111-bbbb-2222-cccc-333344445555",
            domain=kwargs["domain"],
            storage_key=kwargs["storage_key"],
            operation=kwargs["operation"],
        )

    with patcher, \
         patch("app.shared.cloud_backends.object_storage_enabled", return_value=True), \
         patch("app.shared.cloud_backends.get_runtime_object_storage", return_value=MagicMock(delete=MagicMock())), \
         patch("app.shared.storage_reconciliation.record_pending", side_effect=_spy_record_pending), \
         patch("app.shared.storage_reconciliation.mark_done", side_effect=lambda rid: mark_done_calls.append(rid)), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt", side_effect=lambda rid, **kw: mark_failed_calls.append((rid, kw))):
        out = fs_db.delete_file_source("file_abc")

    assert out is True
    # record_pending called exactly once with the canonical kwargs.
    assert len(record_pending_calls) == 1
    _cur, kwargs = record_pending_calls[0]
    assert _cur is cursor  # same transaction's cursor
    assert kwargs == {
        "domain": "file_source",
        "storage_key": "file-sources/file_abc/x.csv",
        "operation": "delete",
        "reason": "row_delete_post_commit",
        "extras": {"file_id": "file_abc"},
    }
    # Happy path: storage delete OK -> mark_done with the ref id.
    assert mark_done_calls == ["aaaa1111-bbbb-2222-cccc-333344445555"]
    assert mark_failed_calls == []


# 2. compensating_delete returns "ok" -> mark_done


def test_compensating_delete_ok_marks_done():
    from app.db.config import file_sources as fs_db

    patcher, cursor, _conn = _make_tx_patcher(
        current_storage_key="key1",
        siblings=[],
        delete_rowcount=1,
        record_pending_id="rec-OK",
    )

    mark_done_calls = []
    mark_failed_calls = []

    with patcher, \
         patch("app.shared.cloud_backends.object_storage_enabled", return_value=True), \
         patch("app.shared.cloud_backends.get_runtime_object_storage", return_value=MagicMock(delete=MagicMock())), \
         patch("app.shared.storage_reconciliation.mark_done", side_effect=lambda rid: mark_done_calls.append(rid)), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt", side_effect=lambda rid, **kw: mark_failed_calls.append((rid, kw))):
        fs_db.delete_file_source("file_abc")

    assert mark_done_calls == ["rec-OK"]
    assert mark_failed_calls == []


# 3. compensating_delete returns "failed" -> mark_failed_attempt + return True


def test_compensating_delete_failed_marks_failed_attempt_and_returns_true():
    """ADR-0011 posture preserved: storage failure does NOT undo
    the DB delete and does NOT propagate to the user. The new
    contract: it is now PERSISTED for the future reconciler."""
    from app.db.config import file_sources as fs_db

    patcher, _cursor, _conn = _make_tx_patcher(
        current_storage_key="key2",
        siblings=[],
        delete_rowcount=1,
        record_pending_id="rec-FAIL",
    )

    mark_done_calls = []
    mark_failed_calls = []

    failing_storage = MagicMock()
    failing_storage.delete = MagicMock(side_effect=RuntimeError("S3 5xx"))

    with patcher, \
         patch("app.shared.cloud_backends.object_storage_enabled", return_value=True), \
         patch("app.shared.cloud_backends.get_runtime_object_storage", return_value=failing_storage), \
         patch("app.shared.storage_reconciliation.mark_done", side_effect=lambda rid: mark_done_calls.append(rid)), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt", side_effect=lambda rid, **kw: mark_failed_calls.append((rid, kw))):
        out = fs_db.delete_file_source("file_abc")

    # ADR-0011: still True, the row was deleted from DB.
    assert out is True
    # New contract: failure is tracked, not silent.
    assert mark_done_calls == []
    assert len(mark_failed_calls) == 1
    rid, kw = mark_failed_calls[0]
    assert rid == "rec-FAIL"
    # Error message carries the outcome label (no leakage of repr/exc).
    assert kw == {"error": "compensating_delete_outcome=failed"}


# 4. Capability disabled -> compensating_delete returns "skipped" -> mark_done


def test_capability_disabled_marks_done():
    """When `OBJECT_STORAGE_ENABLED=False`, `compensating_delete`
    short-circuits and returns `"skipped"`. There is nothing to
    reconcile (no orphan possible because no storage backend is
    being targeted), so the entry transitions straight to `done`."""
    from app.db.config import file_sources as fs_db

    patcher, _cursor, _conn = _make_tx_patcher(
        current_storage_key="key3",
        siblings=[],
        delete_rowcount=1,
        record_pending_id="rec-SKIP",
    )

    mark_done_calls = []
    mark_failed_calls = []

    with patcher, \
         patch("app.shared.cloud_backends.object_storage_enabled", return_value=False), \
         patch("app.shared.storage_reconciliation.mark_done", side_effect=lambda rid: mark_done_calls.append(rid)), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt", side_effect=lambda rid, **kw: mark_failed_calls.append((rid, kw))):
        out = fs_db.delete_file_source("file_abc")

    assert out is True
    assert mark_done_calls == ["rec-SKIP"]
    assert mark_failed_calls == []


# 5. Shared blob (siblings exist) -> NO record_pending, NO mark_*


def test_shared_blob_path_does_not_record_pending():
    """`record_pending` MUST NOT be called when a sibling row keeps
    the storage_key alive: the blob is not an orphan candidate."""
    from app.db.config import file_sources as fs_db

    patcher, _cursor, _conn = _make_tx_patcher(
        current_storage_key="shared-key",
        siblings=["sibling-1"],
        delete_rowcount=1,
    )

    record_pending_calls = []
    mark_done_calls = []
    mark_failed_calls = []

    fake_storage = MagicMock()
    fake_storage.delete = MagicMock()

    with patcher, \
         patch("app.shared.cloud_backends.object_storage_enabled", return_value=True), \
         patch("app.shared.cloud_backends.get_runtime_object_storage", return_value=fake_storage), \
         patch("app.shared.storage_reconciliation.record_pending", side_effect=lambda cur, **kw: record_pending_calls.append(kw)), \
         patch("app.shared.storage_reconciliation.mark_done", side_effect=lambda rid: mark_done_calls.append(rid)), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt", side_effect=lambda rid, **kw: mark_failed_calls.append((rid, kw))):
        out = fs_db.delete_file_source("file_abc")

    assert out is True
    # The sibling exists, so no compensating_delete is run on storage.
    fake_storage.delete.assert_not_called()
    # And no reconciliation entry is recorded.
    assert record_pending_calls == []
    assert mark_done_calls == []
    assert mark_failed_calls == []


# 6. BYTEA path (storage_key None) -> NO record_pending


def test_bytea_path_does_not_record_pending():
    """Rows with `storage_key=None` (BYTEA blob in DB) have no
    object-storage cleanup at all. Reconciliation log must stay
    empty."""
    from app.db.config import file_sources as fs_db

    patcher, _cursor, _conn = _make_tx_patcher(
        current_storage_key=None,
        siblings=[],
        delete_rowcount=1,
    )

    record_pending_calls = []
    mark_done_calls = []
    mark_failed_calls = []

    with patcher, \
         patch("app.shared.cloud_backends.object_storage_enabled", return_value=True), \
         patch("app.shared.cloud_backends.get_runtime_object_storage", return_value=MagicMock(delete=MagicMock())), \
         patch("app.shared.storage_reconciliation.record_pending", side_effect=lambda cur, **kw: record_pending_calls.append(kw)), \
         patch("app.shared.storage_reconciliation.mark_done", side_effect=lambda rid: mark_done_calls.append(rid)), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt", side_effect=lambda rid, **kw: mark_failed_calls.append((rid, kw))):
        out = fs_db.delete_file_source("file_abc")

    assert out is True
    assert record_pending_calls == []
    assert mark_done_calls == []
    assert mark_failed_calls == []


# 7. record_pending happens BEFORE the DELETE inside the transaction


def test_record_pending_is_inside_transaction_before_delete():
    """Atomicity invariant: the reconciliation INSERT must commit
    in the same transaction as the row DELETE so a process crash
    between commit and post-commit storage delete still leaves
    a `pending` row that the future reconciler can pick up.

    Verified by letting the REAL `record_pending` run and inspecting
    the cursor's execute history for INSERT-before-DELETE ordering."""
    from app.db.config import file_sources as fs_db

    patcher, cursor, _conn = _make_tx_patcher(
        current_storage_key="key-order",
        siblings=[],
        delete_rowcount=1,
        record_pending_id="rec-ORDER",
    )

    with patcher, \
         patch("app.shared.cloud_backends.object_storage_enabled", return_value=True), \
         patch("app.shared.cloud_backends.get_runtime_object_storage", return_value=MagicMock(delete=MagicMock())), \
         patch("app.shared.storage_reconciliation.mark_done"), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt"):
        fs_db.delete_file_source("file_abc")

    # SQL order on the cursor: 4 statements.
    sqls = _executed_sqls(cursor)
    assert len(sqls) == 4, f"unexpected SQL count: {sqls}"
    assert "SELECT storage_key" in sqls[0]
    assert "id <> %s" in sqls[1]  # SELECT siblings FOR UPDATE
    # Crucial: INSERT happens BEFORE the DELETE — commit groups them
    # atomically (process death after commit cannot leak an orphan
    # without a corresponding pending row).
    assert "INSERT INTO plenora.storage_reconciliation_log" in sqls[2]
    assert "DELETE FROM file_sources" in sqls[3]
