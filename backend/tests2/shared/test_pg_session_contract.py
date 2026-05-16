from __future__ import annotations

import importlib

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


class _StubCursor:
    def __init__(self, conn: "_StubConn") -> None:
        self._conn = conn
        self.rowcount = conn.rowcount

    def __enter__(self) -> "_StubCursor":
        self._conn.open_cursors += 1
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._conn.closed_cursors += 1

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._conn.calls.append(("execute", sql, params))
        if self._conn.execute_error is not None:
            raise self._conn.execute_error

    def executemany(self, sql: str, params_list: list[tuple]) -> None:
        self._conn.calls.append(("executemany", sql, tuple(params_list)))

    def fetchone(self):
        return self._conn.fetchone_row

    def fetchall(self):
        return self._conn.fetchall_rows


class _StubConn:
    def __init__(
        self,
        *,
        fetchone_row=None,
        fetchall_rows=None,
        rowcount: int = 0,
        execute_error: Exception | None = None,
        commit_error: Exception | None = None,
    ) -> None:
        self.fetchone_row = fetchone_row
        self.fetchall_rows = [] if fetchall_rows is None else fetchall_rows
        self.rowcount = rowcount
        self.execute_error = execute_error
        self.commit_error = commit_error
        self.calls: list[tuple[str, str, tuple]] = []
        self.commits = 0
        self.rollbacks = 0
        self.open_cursors = 0
        self.closed_cursors = 0

    def cursor(self) -> _StubCursor:
        return _StubCursor(self)

    def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollbacks += 1


def test_explicit_unit_of_work_commits_without_thread_local(monkeypatch):
    import app.db.pg_connection as pg

    conn = _StubConn(fetchone_row={"id": "u1"})
    returned: list[_StubConn] = []
    monkeypatch.setattr(pg, "get_connection", lambda: conn)
    monkeypatch.setattr(pg, "put_connection", returned.append)

    with pg.unit_of_work() as session:
        row = session.fetchone("SELECT * FROM users WHERE id = %s", ("u1",))

    assert isinstance(session, pg.PgSession)
    assert row == {"id": "u1"}
    assert conn.calls == [("execute", "SELECT * FROM users WHERE id = %s", ("u1",))]
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert returned == [conn]
    with pytest.raises(RuntimeError, match="PgSession is closed"):
        session.fetchone("SELECT 1")


def test_explicit_unit_of_work_rolls_back_and_releases_on_error(monkeypatch):
    import app.db.pg_connection as pg

    conn = _StubConn()
    returned: list[_StubConn] = []
    monkeypatch.setattr(pg, "get_connection", lambda: conn)
    monkeypatch.setattr(pg, "put_connection", returned.append)

    with pytest.raises(ValueError, match="boom"):
        with pg.unit_of_work() as session:
            assert isinstance(session, pg.PgSession)
            raise ValueError("boom")

    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert returned == [conn]


def test_explicit_unit_of_work_rolls_back_when_commit_fails(monkeypatch):
    import app.db.pg_connection as pg

    conn = _StubConn(commit_error=RuntimeError("commit failed"))
    returned: list[_StubConn] = []
    monkeypatch.setattr(pg, "get_connection", lambda: conn)
    monkeypatch.setattr(pg, "put_connection", returned.append)

    with pytest.raises(RuntimeError, match="commit failed"):
        with pg.unit_of_work() as session:
            session.execute("DELETE FROM logs WHERE id = %s", ("l1",))

    assert conn.commits == 1
    assert conn.rollbacks == 1
    assert returned == [conn]


def test_pg_session_rejects_legacy_placeholders_before_cursor_execute():
    import app.db.pg_connection as pg

    conn = _StubConn()
    session = pg.PgSession(conn)

    with pytest.raises(ValueError, match="native psycopg2 placeholders"):
        session.fetchone("SELECT * FROM users WHERE id = ?", ("u1",))

    assert conn.calls == []


def test_legacy_helpers_keep_commit_release_contract(monkeypatch):
    import app.db.pg_connection as pg

    pg = importlib.reload(pg)
    conns = [
        _StubConn(fetchone_row={"id": "one"}),
        _StubConn(fetchall_rows=[{"id": "a"}, {"id": "b"}]),
        _StubConn(rowcount=3),
        _StubConn(),
    ]
    returned: list[_StubConn] = []
    monkeypatch.setattr(pg, "get_connection", lambda: conns.pop(0))
    monkeypatch.setattr(pg, "put_connection", returned.append)

    assert pg.fetchone("SELECT * FROM t WHERE id = %s", ("one",)) == {"id": "one"}
    assert pg.fetchall("SELECT * FROM t") == [{"id": "a"}, {"id": "b"}]
    result = pg.execute("DELETE FROM t WHERE stale = %s", (True,))
    pg.executemany("INSERT INTO t(id) VALUES (%s)", [("x",), ("y",)])

    assert result.rowcount == 3
    assert result.fetchone() is None
    assert [conn.commits for conn in returned] == [1, 1, 1, 1]
    assert [conn.rollbacks for conn in returned] == [0, 0, 0, 0]
    assert len(returned) == 4
