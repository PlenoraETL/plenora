"""Contract tests for typed query session/execution context."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.estrai, pytest.mark.contract]


class _FakeResult:
    def __init__(self):
        self.cursor = MagicMock()
        self.cursor.description = [("id",)]
        self._chunks = [[(1,)], []]

    def fetchmany(self, size):
        return self._chunks.pop(0)


def _make_engine_factory(fake_result):
    fake_conn = MagicMock()
    fake_conn.execute.side_effect = [MagicMock(), fake_result]

    fake_ctx = MagicMock()
    fake_ctx.__enter__.return_value = fake_conn
    fake_ctx.__exit__.return_value = False

    fake_engine = MagicMock()
    fake_engine.connect.return_value = fake_ctx

    factory = MagicMock()
    factory.create_engine.return_value = fake_engine
    return factory, fake_conn


def test_session_context_carries_provider_timeout_metadata():
    from app.db.query_session import SessionContext

    connection = MagicMock()
    connection.type = "postgres"

    context = SessionContext.for_connection(connection, timeout_seconds=12)

    assert context.connection is connection
    assert context.provider == "postgres"
    assert context.timeout_seconds == 12
    assert context.timeout_milliseconds == 12_000
    assert context.statement_timeout_sql == "SET statement_timeout = :t"
    assert context.uses_native_call_timeout is False
    assert context.oracle_open_cursors is None


def test_query_executor_builds_execution_context_from_settings(monkeypatch):
    from app.db.query_executor import QueryExecutor
    from app.db.query_session import ExecutionContext
    import app.config

    monkeypatch.setattr(app.config.settings, "DB_QUERY_TIMEOUT", 9, raising=False)

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
    assert context.session.statement_timeout_sql == "SET SESSION max_execution_time = :t"


def test_execute_raw_uses_session_context_and_keeps_engine_factory_api(monkeypatch):
    from app.db.query_executor import QueryExecutor
    import app.config

    monkeypatch.setattr(app.config.settings, "DB_QUERY_TIMEOUT", 12, raising=False)
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
    assert str(calls[0].args[0]) == "SET statement_timeout = :t"
    assert calls[0].args[1] == {"t": 12_000}
    assert str(calls[1].args[0]) == "SELECT :limit"
    assert calls[1].args[1] == {"limit": 1}
