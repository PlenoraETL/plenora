"""Contract tests for PostgreSQL schema-aware ESTRAI introspection."""

from __future__ import annotations

import pytest

from app.core.estrai.database_inspector import DatabaseConnection, DatabaseInspector


pytestmark = [pytest.mark.unit, pytest.mark.estrai, pytest.mark.contract, pytest.mark.regression]


class CapturingCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []
        self.closed = False

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class CapturingConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


class CapturingFactory:
    def __init__(self, cursor):
        self.cursor = cursor

    def create_connection(self, _conn):
        return CapturingConnection(self.cursor)


def _postgres_conn(schema):
    return DatabaseConnection(
        type="postgres",
        host="localhost",
        port=5432,
        database="db",
        username="user",
        password="secret",
        db_schema=schema,
    )


def _inspector(cursor):
    return DatabaseInspector(connection_factory=CapturingFactory(cursor))


def test_postgres_table_introspection_uses_connection_schema_param():
    cursor = CapturingCursor([
        ("roads", "id", "integer", "int4", None, None),
    ])

    result = _inspector(cursor).get_tables_and_columns(_postgres_conn("gis"))

    query, params = cursor.executed[0]
    assert "WHERE c.table_schema = %s" in query
    assert "c.table_schema = 'public'" not in query
    assert params == ("gis",)
    assert result == {"roads": [{"name": "id", "type": "int"}]}


def test_postgres_view_introspection_uses_connection_schema_param():
    cursor = CapturingCursor([
        ("roads_view", "name", "text", "text", None, None),
    ])

    result = _inspector(cursor).get_views_and_columns(_postgres_conn("gis"))

    query, params = cursor.executed[0]
    assert "WHERE c.table_schema = %s" in query
    assert "c.table_schema = 'public'" not in query
    assert params == ("gis",)
    assert result == {"roads_view": [{"name": "name", "type": "str"}]}


def test_postgres_introspection_defaults_to_public_when_schema_is_blank():
    cursor = CapturingCursor([])

    _inspector(cursor).get_tables_and_columns(_postgres_conn("  "))

    _query, params = cursor.executed[0]
    assert params == ("public",)
