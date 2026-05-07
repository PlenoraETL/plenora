"""Contract tests BE-GO-READY-STORAGE-ENGINE-008-UPLOAD-INTENT-PATTERN.

Lock the upload_intent self-clearing lifecycle that closes R-021.1:

1. `record_upload_intent` runs autocommit INSERT with
   `operation='upload_intent'` + `state='pending'`; returns the
   freshly-issued UUID; best-effort on DB error (returns None).
2. `fetch_due_stale_upload_intents` filters by
   `state='pending' AND operation='upload_intent' AND
   created_at < now - grace_seconds AND
   (next_retry_at IS NULL OR next_retry_at <= now)`.
3. `is_storage_key_referenced` returns
   `True` / `False` / `None` (read-error) — the three states
   the beat hook MUST branch on.
4. `dispatch_blob_storage` records the intent BEFORE upload on the
   object_storage path; on upload exception the intent is
   `mark_done`-cleared (no orphan); on the DB-bytea path the
   intent is NOT recorded.
5. Beat hook `_reconcile_storage` upload_intent branch:
   - referenced=True   -> `mark_done`;
   - referenced=False  -> `compensating_delete` + `mark_orphan_deleted`
                          (or backoff/lost on failure);
   - referenced=None   -> `mark_failed_attempt` (skip delete to
                          avoid data loss on read-error).
6. Settings.DEFAULTS expose the 2 new tunables.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# Settings.DEFAULTS — 2 new tunables


def test_settings_defaults_expose_upload_intent_tunables():
    from app.config import Settings

    assert "STORAGE_RECONCILE_UPLOAD_INTENT_GRACE_SECONDS" in Settings.DEFAULTS
    assert "STORAGE_RECONCILE_UPLOAD_INTENT_BATCH_SIZE" in Settings.DEFAULTS

    grace = Settings.DEFAULTS["STORAGE_RECONCILE_UPLOAD_INTENT_GRACE_SECONDS"]
    batch = Settings.DEFAULTS["STORAGE_RECONCILE_UPLOAD_INTENT_BATCH_SIZE"]
    assert isinstance(grace, int) and grace > 0
    assert isinstance(batch, int) and batch > 0


# record_upload_intent


def _capture_pg_fetchone(monkeypatch, *, return_id="11111111-1111-1111-1111-111111111111"):
    """Patch app.db.pg_connection.fetchone with a fake returning the id.

    `record_upload_intent` needs INSERT ... RETURNING id. The production
    `pg_connection.execute` wrapper intentionally discards returned rows,
    so this path must use `fetchone`.
    """
    calls = []

    def _fake(sql, params=()):
        calls.append((sql, params))
        return {"id": return_id}

    import app.db.pg_connection as pg_conn

    monkeypatch.setattr(pg_conn, "fetchone", _fake)
    return calls


def test_record_upload_intent_inserts_pending_row(monkeypatch):
    from app.shared.storage_reconciliation import record_upload_intent

    calls = _capture_pg_fetchone(monkeypatch, return_id="abc-1234")
    intent_id = record_upload_intent(
        domain="file_source",
        storage_key="file-sources/x/y.csv",
        reason="upload_intent_pre_dispatch",
        extras={"file_id": "fs-123", "size_bytes": 200},
    )

    assert intent_id == "abc-1234"
    assert len(calls) == 1
    sql, params = calls[0]
    assert "INSERT INTO plenora.storage_reconciliation_log" in sql
    assert "'upload_intent'" in sql
    assert "'pending'" in sql
    assert "RETURNING id" in sql
    # First three positional params: domain, storage_key, reason.
    assert params[0] == "file_source"
    assert params[1] == "file-sources/x/y.csv"
    assert params[2] == "upload_intent_pre_dispatch"
    # Last param: JSON-serialized extras (not None).
    assert params[3] is not None and "fs-123" in params[3]


def test_record_upload_intent_handles_none_extras(monkeypatch):
    from app.shared.storage_reconciliation import record_upload_intent

    calls = _capture_pg_fetchone(monkeypatch)
    record_upload_intent(
        domain="file_source",
        storage_key="key/abc",
        reason="upload_intent_pre_dispatch",
        extras=None,
    )
    assert len(calls) == 1
    _, params = calls[0]
    assert params[3] is None


def test_record_upload_intent_returns_none_on_db_error(monkeypatch):
    """Best-effort: DB error -> warning + return None. Caller must
    proceed with upload (W2 race for that single dispatch reverts to
    pre-008 behavior — acceptable, ADR-0011 posture preserved)."""
    from app.shared.storage_reconciliation import record_upload_intent

    def _raise(sql, params=()):
        raise RuntimeError("DB unreachable")

    import app.db.pg_connection as pg_conn

    monkeypatch.setattr(pg_conn, "fetchone", _raise)
    out = record_upload_intent(
        domain="file_source",
        storage_key="key/abc",
        reason="upload_intent_pre_dispatch",
    )
    assert out is None


# fetch_due_stale_upload_intents


def test_fetch_due_stale_upload_intents_filters_correctly():
    from app.shared import storage_reconciliation as recon

    captured = {}

    def _fake(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch("app.db.pg_connection.fetchall", _fake):
        recon.fetch_due_stale_upload_intents(limit=10, grace_seconds=600)

    sql = captured["sql"]
    assert "state = 'pending'" in sql
    assert "operation = 'upload_intent'" in sql
    assert "created_at <" in sql
    assert "next_retry_at IS NULL OR next_retry_at <=" in sql
    assert "LIMIT" in sql

    # params: (created_at_cutoff, now, limit). cutoff < now by grace_seconds.
    cutoff, now_v, limit_v = captured["params"]
    assert isinstance(cutoff, datetime)
    assert isinstance(now_v, datetime)
    assert (now_v - cutoff).total_seconds() == pytest.approx(600, abs=1)
    assert limit_v == 10


def test_fetch_due_stale_upload_intents_returns_typed_rows():
    from app.shared import storage_reconciliation as recon

    db_rows = [
        {
            "id": "ui-1",
            "domain": "file_source",
            "storage_key": "file-sources/x/y.csv",
            "operation": "upload_intent",
            "attempts": 0,
        },
    ]
    with patch("app.db.pg_connection.fetchall", lambda sql, params=(): db_rows):
        out = recon.fetch_due_stale_upload_intents(limit=10, grace_seconds=600)

    assert len(out) == 1
    assert isinstance(out[0], recon.PendingReconciliationRow)
    assert out[0].id == "ui-1"
    assert out[0].operation == "upload_intent"


def test_fetch_due_stale_upload_intents_rejects_invalid_args():
    from app.shared import storage_reconciliation as recon

    with pytest.raises(ValueError):
        recon.fetch_due_stale_upload_intents(limit=0, grace_seconds=600)
    with pytest.raises(ValueError):
        recon.fetch_due_stale_upload_intents(limit=10, grace_seconds=-1)


def test_fetch_due_stale_upload_intents_swallows_db_error():
    from app.shared import storage_reconciliation as recon

    def _raise(sql, params=()):
        raise RuntimeError("DB unreachable")

    with patch("app.db.pg_connection.fetchall", _raise):
        out = recon.fetch_due_stale_upload_intents(limit=10, grace_seconds=600)

    assert out == []


# is_storage_key_referenced — three-valued result


def test_is_storage_key_referenced_returns_true_when_row_exists():
    from app.shared.storage_reconciliation import is_storage_key_referenced

    with patch("app.db.pg_connection.fetchall", lambda sql, params=(): [(1,)]):
        assert is_storage_key_referenced("file-sources/x/y.csv") is True


def test_is_storage_key_referenced_returns_false_when_no_row():
    from app.shared.storage_reconciliation import is_storage_key_referenced

    with patch("app.db.pg_connection.fetchall", lambda sql, params=(): []):
        assert is_storage_key_referenced("file-sources/missing.csv") is False


def test_is_storage_key_referenced_returns_none_on_db_error():
    """Critical: must NOT return False on DB error — that would
    silently widen the orphan scope and could lead to data loss
    (compensating_delete on a key whose live status is unknown)."""
    from app.shared.storage_reconciliation import is_storage_key_referenced

    def _raise(sql, params=()):
        raise RuntimeError("DB unreachable")

    with patch("app.db.pg_connection.fetchall", _raise):
        result = is_storage_key_referenced("file-sources/x/y.csv")

    assert result is None


def test_is_storage_key_referenced_uses_indexed_column():
    """SQL must hit `plenora.file_sources.storage_key` (indexed by
    `idx_file_sources_storage_key_notnull`)."""
    from app.shared.storage_reconciliation import is_storage_key_referenced

    captured = {}

    def _fake(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch("app.db.pg_connection.fetchall", _fake):
        is_storage_key_referenced("file-sources/x/y.csv")

    assert "plenora.file_sources" in captured["sql"]
    assert "storage_key" in captured["sql"]
    assert captured["params"] == ("file-sources/x/y.csv",)


# dispatch_blob_storage wiring


def _settings_get_stub(**overrides):
    """Return an object with `.get(key, default)` mimicking settings."""
    base = {"STORAGE_POLICY": "object_storage", "FILE_SOURCE_S3_THRESHOLD_BYTES": 1024}
    base.update(overrides)

    class _S:
        DEFAULTS = base

        def get(self, key, default=None):
            return base.get(key, default)
    return _S()


def test_dispatch_records_upload_intent_before_upload(monkeypatch):
    """object_storage branch: record_upload_intent runs BEFORE
    storage.upload_bytes; on success the intent stays pending."""
    from app.services import file_storage_dispatch

    record_calls = []
    upload_calls = []
    mark_done_calls = []

    def _fake_record_upload_intent(**kwargs):
        record_calls.append(kwargs)
        # simulate success: return an intent_id
        return "intent-uuid-1"

    fake_storage = MagicMock()

    def _fake_upload(raw, key):
        upload_calls.append((raw, key))
        # the order check below ensures record happened first
        assert record_calls, "record_upload_intent must run BEFORE upload"

    fake_storage.upload_bytes = _fake_upload

    def _fake_get_runtime():
        return fake_storage

    def _fake_mark_done(intent_id):
        mark_done_calls.append(intent_id)

    monkeypatch.setattr("app.config.settings", _settings_get_stub())
    monkeypatch.setattr(
        "app.shared.storage_reconciliation.record_upload_intent",
        _fake_record_upload_intent,
    )
    monkeypatch.setattr(
        "app.shared.storage_reconciliation.mark_done",
        _fake_mark_done,
    )
    monkeypatch.setattr(
        "app.shared.cloud_backends.get_runtime_object_storage",
        _fake_get_runtime,
    )

    log = MagicMock()
    out = file_storage_dispatch.dispatch_blob_storage(
        "file-1", b"x" * 2048, "f.csv", logger=log,
    )

    assert len(record_calls) == 1
    assert record_calls[0]["domain"] == "file_source"
    assert record_calls[0]["storage_key"].startswith("file-sources/file-1/")
    assert record_calls[0]["reason"] == "upload_intent_pre_dispatch"
    assert len(upload_calls) == 1
    # On upload success, mark_done is NOT called — reconciler closes it.
    assert mark_done_calls == []
    assert out["data"] is None
    assert out["storage_key"].startswith("file-sources/file-1/")


def test_dispatch_marks_done_when_upload_fails(monkeypatch):
    """object_storage branch: upload exception -> mark_done(intent_id)
    -> exception re-raised. No orphan possible (nothing uploaded)."""
    from app.services import file_storage_dispatch

    mark_done_calls = []

    def _fake_record_upload_intent(**kwargs):
        return "intent-uuid-2"

    fake_storage = MagicMock()
    fake_storage.upload_bytes = MagicMock(side_effect=RuntimeError("S3 down"))

    def _fake_get_runtime():
        return fake_storage

    def _fake_mark_done(intent_id):
        mark_done_calls.append(intent_id)

    monkeypatch.setattr("app.config.settings", _settings_get_stub())
    monkeypatch.setattr(
        "app.shared.storage_reconciliation.record_upload_intent",
        _fake_record_upload_intent,
    )
    monkeypatch.setattr(
        "app.shared.storage_reconciliation.mark_done",
        _fake_mark_done,
    )
    monkeypatch.setattr(
        "app.shared.cloud_backends.get_runtime_object_storage",
        _fake_get_runtime,
    )

    log = MagicMock()
    with pytest.raises(RuntimeError, match="S3 down"):
        file_storage_dispatch.dispatch_blob_storage(
            "file-2", b"x" * 2048, "f.csv", logger=log,
        )

    # Intent must be cleared because nothing was uploaded.
    assert mark_done_calls == ["intent-uuid-2"]


def test_dispatch_does_not_record_intent_on_db_bytea_path(monkeypatch):
    """STORAGE_POLICY=db: no upload, no upload_intent. Intent is only
    relevant for the object_storage path."""
    from app.services import file_storage_dispatch

    record_calls = []

    def _fake_record_upload_intent(**kwargs):
        record_calls.append(kwargs)
        return "intent-uuid-x"

    monkeypatch.setattr(
        "app.config.settings",
        _settings_get_stub(STORAGE_POLICY="db"),
    )
    monkeypatch.setattr(
        "app.shared.storage_reconciliation.record_upload_intent",
        _fake_record_upload_intent,
    )

    log = MagicMock()
    out = file_storage_dispatch.dispatch_blob_storage(
        "file-3", b"data", "f.csv", logger=log,
    )

    assert record_calls == []
    assert out["data"] == b"data"
    assert out["storage_key"] is None


def test_dispatch_proceeds_when_record_upload_intent_returns_none(monkeypatch):
    """Best-effort: record_upload_intent failure (returns None) does
    NOT block the upload. The dispatch continues; intent_id stays None
    and on upload exception mark_done is NOT called (no intent to close)."""
    from app.services import file_storage_dispatch

    upload_calls = []
    mark_done_calls = []

    def _fake_record_upload_intent(**kwargs):
        return None  # simulate DB unreachable

    fake_storage = MagicMock()
    fake_storage.upload_bytes = MagicMock(
        side_effect=lambda raw, key: upload_calls.append(key)
    )

    def _fake_mark_done(intent_id):
        mark_done_calls.append(intent_id)

    monkeypatch.setattr("app.config.settings", _settings_get_stub())
    monkeypatch.setattr(
        "app.shared.storage_reconciliation.record_upload_intent",
        _fake_record_upload_intent,
    )
    monkeypatch.setattr(
        "app.shared.storage_reconciliation.mark_done",
        _fake_mark_done,
    )
    monkeypatch.setattr(
        "app.shared.cloud_backends.get_runtime_object_storage",
        lambda: fake_storage,
    )

    log = MagicMock()
    out = file_storage_dispatch.dispatch_blob_storage(
        "file-4", b"x" * 2048, "f.csv", logger=log,
    )

    assert len(upload_calls) == 1
    assert mark_done_calls == []
    assert out["storage_key"].startswith("file-sources/file-4/")


# Beat hook _reconcile_storage upload_intent branch


def _make_scheduler():
    from workers.beat_scheduler import PlenoraBeatScheduler

    sched = PlenoraBeatScheduler.__new__(PlenoraBeatScheduler)
    sched._last_storage_reconcile = 0.0
    return sched


def _settings_stub(**kwargs):
    defaults = {
        "STORAGE_RECONCILE_INTERVAL_SECONDS": 900,
        "STORAGE_RECONCILE_BATCH_SIZE": 50,
        "STORAGE_RECONCILE_MAX_ATTEMPTS": 5,
        "STORAGE_RECONCILE_BACKOFF_BASE_SECONDS": 60,
        "STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS": 30,
        "STORAGE_RECONCILE_PRUNE_BATCH_SIZE": 500,
        "STORAGE_RECONCILE_UPLOAD_INTENT_GRACE_SECONDS": 600,
        "STORAGE_RECONCILE_UPLOAD_INTENT_BATCH_SIZE": 50,
    }
    defaults.update(kwargs)
    ns = SimpleNamespace(**defaults)
    ns.get = lambda key, default=None: getattr(ns, key, default)
    return ns


def _ui_row(*, id="ui-1", attempts=0, storage_key="file-sources/abc/x.csv"):
    from app.shared.storage_reconciliation import PendingReconciliationRow
    return PendingReconciliationRow(
        id=id,
        domain="file_source",
        storage_key=storage_key,
        operation="upload_intent",
        attempts=attempts,
    )


def test_reconcile_upload_intent_referenced_marks_done(monkeypatch):
    """referenced=True (DB row exists) -> mark_done. No delete attempted."""
    sched = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)

    mark_done_calls = []
    delete_calls = []

    with patch("app.config.settings", new=_settings_stub()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.fetch_due_stale_upload_intents",
               return_value=[_ui_row(id="ui-1")]), \
         patch("app.shared.storage_reconciliation.is_storage_key_referenced",
               return_value=True), \
         patch("app.shared.storage_reconciliation.mark_done",
               side_effect=lambda i: mark_done_calls.append(i)), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               return_value=0), \
         patch("app.shared.storage_compensation.compensating_delete",
               side_effect=lambda *a, **kw: delete_calls.append((a, kw)) or "ok"):
        sched._reconcile_storage()

    assert mark_done_calls == ["ui-1"]
    assert delete_calls == []  # NO compensating_delete on referenced=True


def test_reconcile_upload_intent_not_referenced_calls_compensating_delete(monkeypatch):
    """referenced=False -> compensating_delete + mark_orphan_deleted."""
    sched = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)

    delete_calls = []
    mark_orphan_calls = []

    with patch("app.config.settings", new=_settings_stub()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.fetch_due_stale_upload_intents",
               return_value=[_ui_row(id="ui-2")]), \
         patch("app.shared.storage_reconciliation.is_storage_key_referenced",
               return_value=False), \
         patch("app.shared.storage_reconciliation.mark_orphan_deleted",
               side_effect=lambda i: mark_orphan_calls.append(i)), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               return_value=0), \
         patch("app.shared.storage_compensation.compensating_delete",
               side_effect=lambda *a, **kw: delete_calls.append((a, kw)) or "ok"):
        sched._reconcile_storage()

    assert len(delete_calls) == 1
    assert mark_orphan_calls == ["ui-2"]
    # storage_key passed positionally, domain + reason in kwargs
    args, kwargs = delete_calls[0]
    assert args[0] == "file-sources/abc/x.csv"
    assert kwargs["domain"] == "file_source"
    assert kwargs["reason"] == "upload_intent_orphan"


def test_reconcile_upload_intent_read_error_marks_failed_attempt(monkeypatch):
    """referenced=None (read-error) -> mark_failed_attempt + backoff.
    NEVER calls compensating_delete (data-loss risk)."""
    sched = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)

    delete_calls = []
    mark_failed_calls = []

    with patch("app.config.settings", new=_settings_stub()), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.fetch_due_stale_upload_intents",
               return_value=[_ui_row(id="ui-3", attempts=1)]), \
         patch("app.shared.storage_reconciliation.is_storage_key_referenced",
               return_value=None), \
         patch("app.shared.storage_reconciliation.mark_failed_attempt",
               side_effect=lambda i, **kw: mark_failed_calls.append((i, kw))), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               return_value=0), \
         patch("app.shared.storage_compensation.compensating_delete",
               side_effect=lambda *a, **kw: delete_calls.append((a, kw)) or "ok"):
        sched._reconcile_storage()

    assert delete_calls == []  # critical: no delete on read-error
    assert len(mark_failed_calls) == 1
    intent_id, kwargs = mark_failed_calls[0]
    assert intent_id == "ui-3"
    assert "ref_check_failed" in kwargs["error"]
    assert kwargs["next_retry_at"] is not None


def test_reconcile_upload_intent_read_error_at_max_attempts_promotes_to_lost(monkeypatch):
    """attempts+1 >= max_attempts -> mark_lost (not retry forever)."""
    sched = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)

    mark_lost_calls = []

    # MAX_ATTEMPTS=2, attempts=1 -> new_attempts=2 -> >= max -> lost
    with patch("app.config.settings",
               new=_settings_stub(STORAGE_RECONCILE_MAX_ATTEMPTS=2)), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.fetch_due_stale_upload_intents",
               return_value=[_ui_row(id="ui-4", attempts=1)]), \
         patch("app.shared.storage_reconciliation.is_storage_key_referenced",
               return_value=None), \
         patch("app.shared.storage_reconciliation.mark_lost",
               side_effect=lambda i, **kw: mark_lost_calls.append((i, kw))), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               return_value=0), \
         patch("app.shared.storage_compensation.compensating_delete",
               return_value="ok"):
        sched._reconcile_storage()

    assert len(mark_lost_calls) == 1
    intent_id, kwargs = mark_lost_calls[0]
    assert intent_id == "ui-4"
    assert "max_attempts" in kwargs["error"]


def test_reconcile_upload_intent_disabled_when_grace_zero(monkeypatch):
    """grace <= 0 OR batch <= 0 disables the branch (delete branch
    still runs)."""
    sched = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)

    fetch_calls = []

    def _fetch_ui(*a, **kw):
        fetch_calls.append(1)
        return []

    with patch("app.config.settings",
               new=_settings_stub(STORAGE_RECONCILE_UPLOAD_INTENT_GRACE_SECONDS=0)), \
         patch("app.shared.storage_reconciliation.fetch_due_pending_deletes",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.fetch_due_stale_upload_intents",
               _fetch_ui), \
         patch("app.shared.storage_reconciliation.count_active_by_state_domain",
               return_value=[]), \
         patch("app.shared.storage_reconciliation.prune_terminal_older_than",
               return_value=0), \
         patch("app.shared.storage_compensation.compensating_delete",
               return_value="ok"):
        sched._reconcile_storage()

    # fetch_due_stale_upload_intents must NOT have been called.
    assert fetch_calls == []
