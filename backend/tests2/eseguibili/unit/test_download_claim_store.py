from __future__ import annotations

from contextlib import contextmanager

from app.core.eseguibili import download_claim_store as store


class _Cursor:
    def __init__(self, row=None, rowcount=0):
        self.row = row
        self.rowcount = rowcount
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return self.row


class _Session:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def execute(self, sql, params):
        return self._cursor.execute(sql, params)


def _patch_uow(monkeypatch, cursor):
    @contextmanager
    def fake_uow():
        yield _Session(cursor)

    monkeypatch.setattr(store, "unit_of_work", fake_uow)


def test_try_acquire_is_atomic_and_takes_over_only_expired_lease(monkeypatch):
    cursor = _Cursor(row={"task_id": "t1"})
    _patch_uow(monkeypatch, cursor)

    assert store.try_acquire(
        task_id="t1", group_id="g1", owner_token="token", ttl_seconds=300,
    ) is True

    sql, params = cursor.calls[0]
    assert "ON CONFLICT (task_id, group_id) DO UPDATE" in sql
    assert "lease_expires_at <= now()" in sql
    assert params == ("t1", "g1", "token", 300)


def test_try_acquire_reports_active_competing_lease(monkeypatch):
    cursor = _Cursor(row=None)
    _patch_uow(monkeypatch, cursor)

    assert store.try_acquire(
        task_id="t1", group_id="g1", owner_token="token",
    ) is False


def test_refresh_and_release_are_token_scoped(monkeypatch):
    cursor = _Cursor(rowcount=1)
    _patch_uow(monkeypatch, cursor)

    assert store.refresh(
        task_id="t1", group_id="g1", owner_token="token",
    ) is True
    assert store.release(
        task_id="t1", group_id="g1", owner_token="token",
    ) is True

    assert "owner_token = %s" in cursor.calls[0][0]
    assert "owner_token = %s" in cursor.calls[1][0]


def test_purge_expired_is_bounded_and_skip_locked(monkeypatch):
    cursor = _Cursor(rowcount=7)
    _patch_uow(monkeypatch, cursor)

    assert store.purge_expired(limit=50_000) == 7

    sql, params = cursor.calls[0]
    assert "lease_expires_at <= now()" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "DELETE FROM eseguibili_download_claims" in sql
    assert params == (10_000,)
