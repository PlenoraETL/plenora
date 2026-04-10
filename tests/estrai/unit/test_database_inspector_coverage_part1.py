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
# DefaultConnectionFactory — create_connection dispatch + each driver
# =============================================================================

class TestDefaultConnectionFactory:

    def test_create_connection_unsupported_type_raises_valueerror(self):
        """Lines 131-132: unknown db type raises ValueError."""
        factory = DefaultConnectionFactory()
        conn = make_db_conn("nosqlxyz")
        with pytest.raises(ValueError, match="nosqlxyz"):
            factory.create_connection(conn)

    @patch(f"{MODULE}.psycopg2", create=True)
    def test_connetti_postgres(self, mock_psycopg2):
        """Lines 137-138: imports psycopg2, calls connect with correct params."""
        sentinel = MagicMock()
        mock_psycopg2.connect.return_value = sentinel

        factory = DefaultConnectionFactory()
        conn = make_db_conn("postgres", host="pghost", port=5433, database="mydb",
                            username="u", password="p")

        # Patch the import inside the method
        with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_psycopg2 if name == "psycopg2" else __import__(name, *a, **kw)):
            result = factory.create_connection(conn)

        assert result == sentinel
        mock_psycopg2.connect.assert_called_once_with(
            host="pghost", port=5433, database="mydb",
            user="u", password="p", connect_timeout=10,
        )

    def test_connetti_mssql_driver18_succeeds(self):
        """Lines 154-167: pyodbc with Driver 18 works on first try."""
        mock_pyodbc = MagicMock()
        sentinel = MagicMock()
        mock_pyodbc.connect.return_value = sentinel
        mock_pyodbc.InterfaceError = type("InterfaceError", (Exception,), {})

        factory = DefaultConnectionFactory()
        conn = make_db_conn("mssql", host="sqlhost", port=1433)

        with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_pyodbc if name == "pyodbc" else __import__(name, *a, **kw)):
            result = factory.create_connection(conn)

        assert result == sentinel
        mock_pyodbc.connect.assert_called_once()
        call_args = mock_pyodbc.connect.call_args
        assert "ODBC Driver 18 for SQL Server" in call_args[0][0]

    def test_connetti_mssql_fallback_to_driver17(self):
        """Lines 168-169: Driver 18 fails → tries Driver 17."""
        mock_pyodbc = MagicMock()
        sentinel = MagicMock()
        mock_pyodbc.InterfaceError = type("InterfaceError", (Exception,), {})

        # First call (Driver 18) raises InterfaceError, second (Driver 17) succeeds
        mock_pyodbc.connect.side_effect = [
            mock_pyodbc.InterfaceError("no driver 18"),
            sentinel,
        ]

        factory = DefaultConnectionFactory()
        conn = make_db_conn("mssql")

        with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_pyodbc if name == "pyodbc" else __import__(name, *a, **kw)):
            result = factory.create_connection(conn)

        assert result == sentinel
        assert mock_pyodbc.connect.call_count == 2
        second_call_args = mock_pyodbc.connect.call_args_list[1]
        assert "ODBC Driver 17 for SQL Server" in second_call_args[0][0]

    def test_connetti_mssql_no_driver_raises_connection_error(self):
        """Line 171: both drivers fail → ConnectionError."""
        mock_pyodbc = MagicMock()
        mock_pyodbc.InterfaceError = type("InterfaceError", (Exception,), {})
        mock_pyodbc.connect.side_effect = mock_pyodbc.InterfaceError("no driver")

        factory = DefaultConnectionFactory()
        conn = make_db_conn("mssql")

        with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_pyodbc if name == "pyodbc" else __import__(name, *a, **kw)):
            with pytest.raises(ConnectionError, match="Nessun driver ODBC"):
                factory.create_connection(conn)

    def test_connetti_mysql(self):
        """Lines 175-176: imports pymysql, calls connect."""
        mock_pymysql = MagicMock()
        sentinel = MagicMock()
        mock_pymysql.connect.return_value = sentinel

        factory = DefaultConnectionFactory()
        conn = make_db_conn("mysql", host="myhost", port=3306,
                            database="mydb", username="u", password="p")

        with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_pymysql if name == "pymysql" else __import__(name, *a, **kw)):
            result = factory.create_connection(conn)

        assert result == sentinel
        mock_pymysql.connect.assert_called_once_with(
            host="myhost", port=3306, database="mydb",
            user="u", password="p", connect_timeout=10,
        )

    def test_connetti_oracle_oracledb(self):
        """Lines 191-197: oracledb available → uses it."""
        mock_oracledb = MagicMock()
        sentinel = MagicMock()
        mock_oracledb.connect.return_value = sentinel

        factory = DefaultConnectionFactory()
        conn = make_db_conn("oracle", host="orahost", port=1521, database="ORCL",
                            username="u", password="p")

        with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_oracledb if name == "oracledb" else __import__(name, *a, **kw)):
            result = factory.create_connection(conn)

        assert result == sentinel
        mock_oracledb.connect.assert_called_once_with(
            user="u", password="p", dsn="orahost:1521/ORCL",
        )

    def test_connetti_oracle_cx_oracle_fallback(self):
        """Lines 198-205: oracledb ImportError → falls back to cx_Oracle."""
        mock_cx = MagicMock()
        sentinel = MagicMock()
        mock_cx.connect.return_value = sentinel
        mock_cx.makedsn.return_value = "dsn_value"

        factory = DefaultConnectionFactory()
        conn = make_db_conn("oracle", host="orahost", port=1521, database="ORCL",
                            username="u", password="p")

        def fake_import(name, *a, **kw):
            if name == "oracledb":
                raise ImportError("no oracledb")
            if name == "cx_Oracle":
                return mock_cx
            return __import__(name, *a, **kw)

        with patch("builtins.__import__", side_effect=fake_import):
            result = factory.create_connection(conn)

        assert result == sentinel
        mock_cx.makedsn.assert_called_once_with("orahost", 1521, service_name="ORCL")
        mock_cx.connect.assert_called_once_with(
            user="u", password="p", dsn="dsn_value",
        )

    def test_connetti_sqlite(self):
        """Lines 209-210: imports sqlite3, calls connect."""
        mock_sqlite3 = MagicMock()
        sentinel = MagicMock()
        mock_sqlite3.connect.return_value = sentinel

        factory = DefaultConnectionFactory()
        conn = make_db_conn("sqlite", database="/tmp/test.db")

        with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_sqlite3 if name == "sqlite3" else __import__(name, *a, **kw)):
            result = factory.create_connection(conn)

        assert result == sentinel
        mock_sqlite3.connect.assert_called_once_with("/tmp/test.db", timeout=10)

    def test_create_connection_dispatches_to_correct_method(self):
        """Lines 122-133: dispatch dict routes to correct _connetti_ method."""
        factory = DefaultConnectionFactory()
        conn = make_db_conn("postgres")

        with patch.object(factory, "_connetti_postgres", return_value="pg_conn") as m:
            result = factory.create_connection(conn)
        assert result == "pg_conn"
        m.assert_called_once_with(conn)


# =============================================================================
# TypeMapper.map_sqlite — date heuristic (line 643)
# =============================================================================

class TestTypeMapperSqliteDateHeuristic:

    def test_date_heuristic(self):
        """Line 643: type containing 'date' but not in map → 'date' via heuristic."""
        assert TypeMapper.map_sqlite("MYDATE") == "date"

    def test_time_heuristic(self):
        """Line 643: type containing 'time' but not in map → 'date' via heuristic."""
        assert TypeMapper.map_sqlite("some_timestamp_value") == "date"

    def test_bool_heuristic(self):
        """Line 640-641: type containing 'bool' not in exact map → 'bool'."""
        assert TypeMapper.map_sqlite("MYBOOL") == "bool"

    def test_real_heuristic(self):
        """Lines 638: type containing 'float' → 'real'."""
        assert TypeMapper.map_sqlite("MYFLOAT") == "real"

    def test_double_heuristic(self):
        assert TypeMapper.map_sqlite("MYDOUBLE") == "real"

    def test_int_heuristic(self):
        """Line 636-637: type containing 'int' not in exact map → 'int'."""
        assert TypeMapper.map_sqlite("UNSIGNED BIG INT") == "int"

    def test_unknown_defaults_to_str(self):
        """Line 646: completely unknown type → 'str'."""
        assert TypeMapper.map_sqlite("ZZUNKNOWN") == "str"

    def test_parenthesized_type_stripped(self):
        """Lines 629-630: VARCHAR(255) → varchar → str."""
        assert TypeMapper.map_sqlite("VARCHAR(255)") == "str"


# =============================================================================
# _esegui_ispezione — ImportError branch (lines 854-855)
# =============================================================================

class TestEseguiIspezioneImportError:

    def test_import_error_is_re_raised_with_driver_name(self):
        """Lines 854-855: factory raises ImportError → re-raised with driver name."""
        factory = MagicMock()
        factory.create_connection.side_effect = ImportError("no psycopg2")

        inspector = DatabaseInspector(connection_factory=factory)
        conn = make_db_conn("postgres")

        with pytest.raises(ImportError, match="Driver PostgreSQL"):
            inspector.get_tables_and_columns(conn)

    def test_import_error_mssql(self):
        factory = MagicMock()
        factory.create_connection.side_effect = ImportError("no pyodbc")

        inspector = DatabaseInspector(connection_factory=factory)
        conn = make_db_conn("mssql")

        with pytest.raises(ImportError, match="Driver MS SQL"):
            inspector.get_tables_and_columns(conn)

    def test_import_error_oracle(self):
        factory = MagicMock()
        factory.create_connection.side_effect = ImportError("no oracledb")

        inspector = DatabaseInspector(connection_factory=factory)
        conn = make_db_conn("oracle")

        with pytest.raises(ImportError, match="Driver Oracle"):
            inspector.get_tables_and_columns(conn)


# =============================================================================
# _inspect_mysql — SRID fallback on exception (lines 901-904)
# =============================================================================

class TestInspectMysqlSridFallback:

    def test_mysql_srid_query_fails_falls_back_to_simple(self):
        """Lines 901-904: MySQL (non-MariaDB) SRID query fails → fallback to simple."""
        rows = [
            ("t1", "id", "int", "int(11)", None),
        ]

        mock_cursor = MagicMock()
        # fetchone for VERSION()
        mock_cursor.fetchone.return_value = ("8.0.32",)

        # execute sequence: VERSION, SRID query (fails), simple query (ok)
        call_count = {"n": 0}
        original_execute = mock_cursor.execute

        def execute_side_effect(query):
            call_count["n"] += 1
            if "ST_GEOMETRY_COLUMNS" in query:
                raise Exception("ST_GEOMETRY_COLUMNS not found")
            return original_execute(query)

        mock_cursor.execute = MagicMock(side_effect=execute_side_effect)
        mock_cursor.fetchall.return_value = rows

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        factory = MagicMock()
        factory.create_connection.return_value = mock_conn

        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_tables_and_columns(make_db_conn("mysql"))

        assert "t1" in result
        types = {c["name"]: c["type"] for c in result["t1"]}
        assert types["id"] == "int"
        # Connection must be closed
        mock_conn.close.assert_called_once()

    def test_mysql_import_error(self):
        """Lines 910-911: ImportError from create_connection → ImportError with pymysql message."""
        factory = MagicMock()
        factory.create_connection.side_effect = ImportError("no pymysql")

        inspector = DatabaseInspector(connection_factory=factory)
        conn = make_db_conn("mysql")

        with pytest.raises(ImportError, match="pymysql"):
            inspector.get_tables_and_columns(conn)


# =============================================================================
# _inspect_sqlite — RuntimeError wrapping (lines 945-946)
# =============================================================================

class TestInspectSqliteError:

    def test_sqlite_exception_wrapped_in_runtime_error(self):
        """Lines 945-946: any exception in SQLite inspection → RuntimeError."""
        factory = MagicMock()
        factory.create_connection.side_effect = RuntimeError("disk I/O error")

        inspector = DatabaseInspector(connection_factory=factory)
        conn = make_db_conn("sqlite")

        with pytest.raises(RuntimeError, match="Errore ispezionando SQLite"):
            inspector.get_tables_and_columns(conn)


# =============================================================================
# _inspect_mysql_views — full flow (lines 969-998)
# =============================================================================

class TestInspectMysqlViews:

    def _setup_mysql_view_mock(self, version_str, rows, srid_fails=False):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (version_str,)

        if srid_fails:
            call_count = {"n": 0}

            def execute_side_effect(query):
                call_count["n"] += 1
                if "ST_GEOMETRY_COLUMNS" in query:
                    raise Exception("not supported")

            mock_cursor.execute = MagicMock(side_effect=execute_side_effect)

        mock_cursor.fetchall.return_value = rows

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        factory = MagicMock()
        factory.create_connection.return_value = mock_conn

        return factory, mock_cursor, mock_conn

    def test_mysql_views_mariadb(self):
        """Lines 969-982: MariaDB uses simple view query."""
        rows = [
            ("v1", "col1", "varchar", "varchar(100)", None),
        ]
        factory, cursor, conn = self._setup_mysql_view_mock("10.11.2-MariaDB", rows)

        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_views_and_columns(make_db_conn("mysql"))

        assert "v1" in result
        assert result["v1"][0]["name"] == "col1"
        assert result["v1"][0]["type"] == "str"
        conn.close.assert_called_once()

    def test_mysql_views_mysql8_with_srid(self):
        """Lines 983-987: MySQL 8 with SRID query succeeds."""
        rows = [
            ("v2", "geom", "point", "point", 4326),
        ]
        factory, cursor, conn = self._setup_mysql_view_mock("8.0.32", rows)

        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_views_and_columns(make_db_conn("mysql"))

        assert "v2" in result
        assert result["v2"][0]["name"] == "geom"
        assert result["v2"][0]["type"] == "geometry:point:4326"

    def test_mysql_views_srid_fallback(self):
        """Lines 988-991: MySQL SRID view query fails → fallback to simple."""
        rows = [
            ("v3", "id", "int", "int(11)", None),
        ]
        factory, cursor, conn = self._setup_mysql_view_mock("8.0.32", rows, srid_fails=True)

        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_views_and_columns(make_db_conn("mysql"))

        assert "v3" in result
        assert result["v3"][0]["type"] == "int"

    def test_mysql_views_import_error(self):
        """Lines 997-998: ImportError → re-raised with pymysql message."""
        factory = MagicMock()
        factory.create_connection.side_effect = ImportError("no pymysql")

        inspector = DatabaseInspector(connection_factory=factory)

        with pytest.raises(ImportError, match="pymysql"):
            inspector.get_views_and_columns(make_db_conn("mysql"))


# =============================================================================
# _inspect_oracle_views — delegation (line 1002)
# =============================================================================

class TestInspectOracleViews:

    def test_oracle_views_uses_oracle_view_columns_query(self):
        """Line 1002: delegates to _esegui_ispezione with ORACLE_VIEW_COLUMNS."""
        rows = [
            ("V_TAB", "ID", "NUMBER", 10, 0),
            ("V_TAB", "NOME", "VARCHAR2", None, None),
        ]
        factory, _, _ = make_mock_factory(rows)
        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_views_and_columns(make_db_conn("oracle"))

        assert "V_TAB" in result
        types = {c["name"]: c["type"] for c in result["V_TAB"]}
        assert types["ID"] == "int"
        assert types["NOME"] == "str"
