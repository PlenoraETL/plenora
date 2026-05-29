"""Contract tests for typed query session/execution context."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.estrai, pytest.mark.contract]


class _FakeResult:
    def __init__(self, columns=None, chunks=None):
        self.cursor = MagicMock()
        self.cursor.description = [(column,) for column in (columns or ["id"])]
        self._chunks = list(chunks or [[(1,)], []])

    def fetchmany(self, size):
        return self._chunks.pop(0)


class _RawOracleConnection:
    def __init__(self):
        self.callTimeout = 0


def _make_engine_factory(fake_result, execute_side_effects=None):
    fake_conn = MagicMock()
    if execute_side_effects is None:
        execute_side_effects = [MagicMock(), fake_result]
    fake_conn.execute.side_effect = execute_side_effects

    fake_ctx = MagicMock()
    fake_ctx.__enter__.return_value = fake_conn
    fake_ctx.__exit__.return_value = False

    fake_engine = MagicMock()
    fake_engine.connect.return_value = fake_ctx

    factory = MagicMock()
    factory.create_engine.return_value = fake_engine
    return factory, fake_conn


def test_session_context_carries_provider_timeout_metadata():
    from app.core.query_execution.query_session import SessionContext

    connection = MagicMock()
    connection.type = "postgres"

    context = SessionContext.for_connection(connection, timeout_seconds=12)

    assert context.connection is connection
    assert context.provider == "postgres"
    assert context.timeout_seconds == 12
    assert context.timeout_milliseconds == 12_000
    # P0 (2026-05-26): literal-embedded value (psycopg3 non accetta
    # bind parameters per SET statement_timeout).
    assert context.statement_timeout_sql == "SET statement_timeout = 12000"
    assert context.uses_native_call_timeout is False
    assert context.oracle_open_cursors is None


def test_session_context_carries_mssql_noop_session_metadata():
    from app.core.query_execution.query_session import SessionContext

    connection = MagicMock()
    connection.type = "mssql"

    context = SessionContext.for_connection(connection, timeout_seconds=12)

    assert context.provider == "mssql"
    assert context.timeout_seconds == 12
    assert context.timeout_milliseconds == 12_000
    assert context.statement_timeout_sql is None
    assert context.uses_native_call_timeout is False
    assert context.oracle_open_cursors is None


def test_session_context_carries_oracle_native_timeout_metadata():
    from app.core.query_execution.query_session import SessionContext

    connection = MagicMock()
    connection.type = "oracle"

    context = SessionContext.for_connection(connection, timeout_seconds=12)

    assert context.provider == "oracle"
    assert context.timeout_seconds == 12
    assert context.timeout_milliseconds == 12_000
    assert context.statement_timeout_sql is None
    assert context.uses_native_call_timeout is True
    assert context.oracle_open_cursors == 32767


def test_query_executor_builds_execution_context_from_settings(monkeypatch):
    from app.core.query_execution.query_executor import QueryExecutor
    from app.core.query_execution.query_session import ExecutionContext

    monkeypatch.setenv("DB_QUERY_TIMEOUT", "9")

    executor = QueryExecutor(engine_factory=MagicMock(), logger=MagicMock())
    connection = MagicMock()
    connection.type = "mysql"

    context = executor._build_execution_context(
        connection,
        "SELECT :limit",
        {"limit": 5},
    )

    assert isinstance(context, ExecutionContext)
    assert context.connection is connection
    assert context.sql == "SELECT :limit"
    assert dict(context.params) == {"limit": 5}
    assert context.session.provider == "mysql"
    assert context.session.timeout_seconds == 9
    assert context.session.timeout_milliseconds == 9_000
    assert context.session.statement_timeout_sql == "SET SESSION max_execution_time = 9000"


def test_execute_raw_uses_session_context_and_keeps_engine_factory_api(monkeypatch):
    from app.core.query_execution.query_executor import QueryExecutor

    monkeypatch.setenv("DB_QUERY_TIMEOUT", "12")
    fake_result = _FakeResult()
    factory, fake_conn = _make_engine_factory(fake_result)

    executor = QueryExecutor(engine_factory=factory, logger=MagicMock())
    connection = MagicMock()
    connection.type = "postgres"

    rows, columns = executor.execute_raw(
        connection,
        "SELECT :limit",
        {"limit": 1},
    )

    factory.create_engine.assert_called_once_with(connection)
    assert rows == [(1,)]
    assert columns == ["id"]

    calls = fake_conn.execute.call_args_list
    # P0 (2026-05-26): SET con literal embedded, no params dict.
    assert str(calls[0].args[0]) == "SET statement_timeout = 12000"
    assert len(calls[0].args) == 1  # no params dict
    assert str(calls[1].args[0]) == "SELECT :limit"
    assert calls[1].args[1] == {"limit": 1}


def test_execute_raw_uses_session_context_for_mssql_without_session_statement(monkeypatch):
    from app.core.query_execution.query_executor import QueryExecutor

    monkeypatch.setenv("DB_QUERY_TIMEOUT", "12")
    fake_result = _FakeResult()
    factory, fake_conn = _make_engine_factory(fake_result, [fake_result])

    executor = QueryExecutor(engine_factory=factory, logger=MagicMock())
    connection = MagicMock()
    connection.type = "mssql"

    rows, columns = executor.execute_raw(
        connection,
        "SELECT :limit",
        {"limit": 1},
    )

    factory.create_engine.assert_called_once_with(connection)
    assert rows == [(1,)]
    assert columns == ["id"]

    fake_conn.execute.assert_called_once()
    assert str(fake_conn.execute.call_args.args[0]) == "SELECT :limit"
    assert fake_conn.execute.call_args.args[1] == {"limit": 1}


def test_execute_raw_uses_session_context_for_oracle_timeout_and_columns(monkeypatch):
    from app.core.query_execution.query_executor import QueryExecutor

    monkeypatch.setenv("DB_QUERY_TIMEOUT", "7")
    fake_result = _FakeResult(columns=["ID", "NAME"], chunks=[[(1, "alice")], []])
    factory, fake_conn = _make_engine_factory(fake_result, [MagicMock(), fake_result])
    raw = _RawOracleConnection()
    fake_conn.connection.dbapi_connection = raw

    executor = QueryExecutor(engine_factory=factory, logger=MagicMock())
    connection = MagicMock()
    connection.type = "oracle"

    rows, columns = executor.execute_raw(
        connection,
        "SELECT :name FROM dual",
        {"name": "alice"},
    )

    factory.create_engine.assert_called_once_with(connection)
    assert raw.callTimeout == 7_000
    assert rows == [(1, "alice")]
    assert columns == ["id", "name"]

    calls = fake_conn.execute.call_args_list
    assert str(calls[0].args[0]) == "ALTER SESSION SET OPEN_CURSORS = 32767"
    assert str(calls[1].args[0]) == "SELECT :name FROM dual"
    assert calls[1].args[1] == {"name": "alice"}
