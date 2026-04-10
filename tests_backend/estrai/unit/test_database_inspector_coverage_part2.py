"""
tests/estrai/unit/test_database_inspector_coverage.py
=====================================================
Additional unit tests for DatabaseInspector targeting uncovered lines.

Covers:
  - DefaultConnectionFactory: all driver methods, dispatch, errors
  - TypeMapper.map_sqlite: date heuristic
  - _esegui_ispezione: ImportError branch
  - _inspect_mysql: SRID fallback on exception, ImportError
  - _inspect_sqlite: RuntimeError wrapping
  - _inspect_mysql_views: full flow (MariaDB, MySQL+SRID, MySQL fallback)
  - _inspect_oracle_views: delegation
  - _inspect_sqlite_views: full flow + error
"""

import pytest
from unittest.mock import MagicMock, patch, call

pytestmark = pytest.mark.unit

from app.core.estrai.database_inspector import (
    DatabaseInspector,
    DatabaseConnection,
    DefaultConnectionFactory,
    SQLQueries,
    TypeMapper,
    ResultProcessor,
)

MODULE = "app.core.estrai.database_inspector"


def make_db_conn(type_: str, **overrides) -> DatabaseConnection:
    defaults = dict(
        type=type_, host="localhost", port=5432,
        database="testdb", username="user", password="pass",
    )
    defaults.update(overrides)
    return DatabaseConnection(**defaults)


def make_mock_factory(rows):
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_cursor.fetchone.return_value = rows[0] if rows else None

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    mock_factory = MagicMock()
    mock_factory.create_connection.return_value = mock_conn

    return mock_factory, mock_cursor, mock_conn




# =============================================================================
# _inspect_sqlite_views — full flow + error (lines 1012-1032)
# =============================================================================

class TestInspectSqliteViews:

    def test_sqlite_views_returns_views(self):
        """Lines 1012-1028: SQLite views inspection - happy path."""
        view_names = [("v_comuni",), ("v_strade",)]
        pragma_rows = [
            (0, "id", "INTEGER", 1, None, 1),
            (1, "nome", "TEXT", 0, None, 0),
        ]

        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [
            view_names,
            pragma_rows,
            pragma_rows,
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        factory = MagicMock()
        factory.create_connection.return_value = mock_conn

        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_views_and_columns(make_db_conn("sqlite"))

        assert "v_comuni" in result
        assert "v_strade" in result
        assert len(result["v_comuni"]) == 2
        assert result["v_comuni"][0]["name"] == "id"
        assert result["v_comuni"][0]["type"] == "int"
        assert result["v_comuni"][1]["name"] == "nome"
        assert result["v_comuni"][1]["type"] == "str"
        mock_conn.close.assert_called_once()

    def test_sqlite_views_empty(self):
        """No views → empty dict."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        factory = MagicMock()
        factory.create_connection.return_value = mock_conn

        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_views_and_columns(make_db_conn("sqlite"))

        assert result == {}

    def test_sqlite_views_error_wrapped_in_runtime_error(self):
        """Lines 1031-1032: exception → RuntimeError with 'viste SQLite' message."""
        factory = MagicMock()
        factory.create_connection.side_effect = RuntimeError("disk error")

        inspector = DatabaseInspector(connection_factory=factory)

        with pytest.raises(RuntimeError, match="Errore ispezionando viste SQLite"):
            inspector.get_views_and_columns(make_db_conn("sqlite"))
