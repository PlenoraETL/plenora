from __future__ import annotations

import threading

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


class _StubConn:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture(autouse=True)
def _reset_transaction_state():
    import app.db.pg_connection as pg

    pg._transaction_state.active = False
    yield
    pg._transaction_state.active = False


def test_transaction_commits_and_returns_connection_to_pool(monkeypatch):
    import app.db.pg_connection as pg

    conn = _StubConn()
    returned: list[_StubConn] = []
    monkeypatch.setattr(pg, "get_connection", lambda: conn)
    monkeypatch.setattr(pg, "put_connection", returned.append)

    with pg.transaction() as yielded:
        assert yielded is conn

    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert returned == [conn]
    assert pg._transaction_state.active is False


def test_transaction_rolls_back_and_returns_connection_to_pool(monkeypatch):
    import app.db.pg_connection as pg

    conn = _StubConn()
    returned: list[_StubConn] = []
    monkeypatch.setattr(pg, "get_connection", lambda: conn)
    monkeypatch.setattr(pg, "put_connection", returned.append)

    with pytest.raises(ValueError, match="boom"):
        with pg.transaction():
            raise ValueError("boom")

    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert returned == [conn]
    assert pg._transaction_state.active is False


def test_nested_transaction_in_same_thread_fails_closed(monkeypatch):
    import app.db.pg_connection as pg

    conn = _StubConn()
    get_calls = 0
    returned: list[_StubConn] = []

    def _get_connection():
        nonlocal get_calls
        get_calls += 1
        return conn

    monkeypatch.setattr(pg, "get_connection", _get_connection)
    monkeypatch.setattr(pg, "put_connection", returned.append)

    with pg.transaction():
        with pytest.raises(RuntimeError, match="Nested pg_connection.transaction"):
            with pg.transaction():
                raise AssertionError("nested body must not run")

    assert get_calls == 1, "nested transaction must fail before pool checkout"
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert returned == [conn]
    assert pg._transaction_state.active is False


def test_nested_transaction_exception_rolls_back_outer_when_uncaught(monkeypatch):
    import app.db.pg_connection as pg

    conn = _StubConn()
    returned: list[_StubConn] = []
    monkeypatch.setattr(pg, "get_connection", lambda: conn)
    monkeypatch.setattr(pg, "put_connection", returned.append)

    with pytest.raises(RuntimeError, match="Nested pg_connection.transaction"):
        with pg.transaction():
            with pg.transaction():
                raise AssertionError("nested body must not run")

    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert returned == [conn]
    assert pg._transaction_state.active is False


def test_transaction_state_is_thread_local(monkeypatch):
    import app.db.pg_connection as pg

    conns = [_StubConn(), _StubConn()]
    returned: list[_StubConn] = []
    entered_first = threading.Event()
    release_first = threading.Event()
    results: list[str] = []
    lock = threading.Lock()

    def _get_connection():
        with lock:
            return conns.pop(0)

    def _put_connection(conn):
        returned.append(conn)

    monkeypatch.setattr(pg, "get_connection", _get_connection)
    monkeypatch.setattr(pg, "put_connection", _put_connection)

    def _hold_transaction():
        with pg.transaction():
            entered_first.set()
            release_first.wait(timeout=5)

    t = threading.Thread(target=_hold_transaction)
    t.start()
    assert entered_first.wait(timeout=5)

    with pg.transaction():
        results.append("second-entered")

    release_first.set()
    t.join(timeout=5)

    assert results == ["second-entered"]
    assert len(returned) == 2
    assert all(conn.commits == 1 for conn in returned)
    assert all(conn.rollbacks == 0 for conn in returned)
