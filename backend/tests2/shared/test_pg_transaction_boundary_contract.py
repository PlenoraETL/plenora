from __future__ import annotations

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


def test_unit_of_work_commits_and_returns_connection_to_pool(monkeypatch):
    import app.db.pg_connection as pg

    conn = _StubConn()
    returned: list[_StubConn] = []
    monkeypatch.setattr(pg, "get_connection", lambda: conn)
    monkeypatch.setattr(pg, "put_connection", returned.append)

    with pg.unit_of_work() as yielded:
        assert isinstance(yielded, pg.PgSession)

    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert returned == [conn]


def test_unit_of_work_rolls_back_and_returns_connection_to_pool(monkeypatch):
    import app.db.pg_connection as pg

    conn = _StubConn()
    returned: list[_StubConn] = []
    monkeypatch.setattr(pg, "get_connection", lambda: conn)
    monkeypatch.setattr(pg, "put_connection", returned.append)

    with pytest.raises(ValueError, match="boom"):
        with pg.unit_of_work():
            raise ValueError("boom")

    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert returned == [conn]


def test_nested_unit_of_work_uses_explicit_independent_sessions(monkeypatch):
    import app.db.pg_connection as pg

    conns = [_StubConn(), _StubConn()]
    returned: list[_StubConn] = []

    def _get_connection():
        return conns.pop(0)

    monkeypatch.setattr(pg, "get_connection", _get_connection)
    monkeypatch.setattr(pg, "put_connection", returned.append)

    with pg.unit_of_work() as outer:
        with pg.unit_of_work() as inner:
            assert outer is not inner

    assert len(returned) == 2
    assert all(conn.commits == 1 for conn in returned)
    assert all(conn.rollbacks == 0 for conn in returned)


def test_transaction_compat_alias_is_removed():
    import app.db.pg_connection as pg

    assert not hasattr(pg, "transaction")
