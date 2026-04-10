"""
Test suite — DB_QUERY_TIMEOUT propagation in QueryExecutor.execute_raw()

Verifica che il timeout venga impostato correttamente per ogni tipo di DB:
  - PostgreSQL: SET statement_timeout = {ms}
  - MySQL:      SET SESSION max_execution_time = {ms}
  - Oracle:     callTimeout sul raw dbapi_connection
  - MSSQL:      nessun SET (timeout solo a livello di connect_args)
  - SQLite:     nessun SET

Tutti i test usano mock dell'engine factory — nessun DB reale richiesto.
"""

import pytest
from unittest.mock import MagicMock, patch, call
from dataclasses import dataclass


# ── Fixture connessioni ──────────────────────────────────────────────────────

@dataclass
class _Conn:
    type: str
    host: str = "localhost"
    port: int = 5432
    database: str = "testdb"
    username: str = "user"
    password: str = "pass"
    db_schema: str = ""


def _make_conn(db_type: str) -> _Conn:
    return _Conn(type=db_type)


# ── Helper: costruisce mock engine + connessione context manager ─────────────

def _make_engine_mock(raw_dbapi_conn=None):
    """
    Restituisce (mock_engine, mock_conn, executed_statements).

    mock_conn.execute() registra ogni chiamata in executed_statements
    come stringa del primo argomento.
    """
    executed = []

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = lambda stmt, *a, **kw: executed.append(str(stmt))

    # Simula cursor.description e fetchmany (usato da execute_raw)
    mock_result = MagicMock()
    mock_result.cursor.description = [("col1",), ("col2",)]
    mock_result.fetchall.return_value = [("v1", "v2")]
    # fetchmany: prima chiamata restituisce dati, seconda lista vuota (stop loop)
    mock_result.fetchmany.side_effect = [[("v1", "v2")], []]
    mock_conn.execute.side_effect = lambda stmt, *a, **kw: (
        executed.append(str(stmt)) or mock_result
    )

    # context manager: with engine.connect() as conn
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_ctx

    # raw dbapi connection per Oracle
    if raw_dbapi_conn is not None:
        mock_conn.connection.dbapi_connection = raw_dbapi_conn

    return mock_engine, mock_conn, executed


def _make_executor(mock_engine):
    """Costruisce QueryExecutor con engine factory mockata."""
    from app.db.query_executor import QueryExecutor

    mock_factory = MagicMock()
    mock_factory.create_engine.return_value = mock_engine

    executor = QueryExecutor.__new__(QueryExecutor)
    executor._engine_factory = mock_factory
    executor._logger = MagicMock()
    return executor


TIMEOUT_S  = 30
TIMEOUT_MS = TIMEOUT_S * 1000   # 30000


# ─────────────────────────────────────────────────────────────────────────────
# POSTGRESQL
# ─────────────────────────────────────────────────────────────────────────────

class TestTimeoutPostgres:

    @pytest.mark.parametrize("db_type", ["postgres", "postgresql"])
    def test_statement_timeout_set(self, db_type):
        """SET statement_timeout deve essere eseguito per postgres/postgresql."""
        mock_engine, _, executed = _make_engine_mock()
        executor = _make_executor(mock_engine)
        conn = _make_conn(db_type)

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = TIMEOUT_S
            executor.execute_raw(conn, "SELECT 1")

        timeout_stmts = [s for s in executed if "statement_timeout" in s.lower()]
        assert len(timeout_stmts) == 1
        assert str(TIMEOUT_MS) in timeout_stmts[0]

    @pytest.mark.parametrize("db_type", ["postgres", "postgresql"])
    def test_statement_timeout_value(self, db_type):
        """Il valore deve essere DB_QUERY_TIMEOUT * 1000 (ms)."""
        mock_engine, _, executed = _make_engine_mock()
        executor = _make_executor(mock_engine)
        conn = _make_conn(db_type)

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = 60
            executor.execute_raw(conn, "SELECT 1")

        timeout_stmts = [s for s in executed if "statement_timeout" in s.lower()]
        assert "60000" in timeout_stmts[0]

    @pytest.mark.parametrize("db_type", ["postgres", "postgresql"])
    def test_timeout_failure_does_not_raise(self, db_type):
        """Se SET statement_timeout fallisce la query deve proseguire comunque."""
        mock_engine, mock_conn, _ = _make_engine_mock()
        executor = _make_executor(mock_engine)
        conn = _make_conn(db_type)

        # Prima execute() (SET statement_timeout) lancia eccezione
        call_count = [0]
        mock_result = MagicMock()
        mock_result.cursor.description = [("col1",)]
        mock_result.fetchall.return_value = []
        mock_result.fetchmany.return_value = []

        def side_effect(stmt, *a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("permission denied")
            return mock_result

        mock_conn.execute.side_effect = side_effect

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = TIMEOUT_S
            rows, cols = executor.execute_raw(conn, "SELECT 1")

        assert cols == ["col1"]


# ─────────────────────────────────────────────────────────────────────────────
# MYSQL
# ─────────────────────────────────────────────────────────────────────────────

class TestTimeoutMySQL:

    def test_max_execution_time_set(self):
        """SET SESSION max_execution_time deve essere eseguito per mysql."""
        mock_engine, _, executed = _make_engine_mock()
        executor = _make_executor(mock_engine)
        conn = _make_conn("mysql")

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = TIMEOUT_S
            executor.execute_raw(conn, "SELECT 1")

        timeout_stmts = [s for s in executed if "max_execution_time" in s.lower()]
        assert len(timeout_stmts) == 1
        assert str(TIMEOUT_MS) in timeout_stmts[0]

    def test_max_execution_time_value(self):
        """Il valore deve essere DB_QUERY_TIMEOUT * 1000 (ms)."""
        mock_engine, _, executed = _make_engine_mock()
        executor = _make_executor(mock_engine)
        conn = _make_conn("mysql")

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = 45
            executor.execute_raw(conn, "SELECT 1")

        timeout_stmts = [s for s in executed if "max_execution_time" in s.lower()]
        assert "45000" in timeout_stmts[0]

    def test_session_scope(self):
        """Deve usare SESSION scope (non GLOBAL) per non impattare altre connessioni."""
        mock_engine, _, executed = _make_engine_mock()
        executor = _make_executor(mock_engine)
        conn = _make_conn("mysql")

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = TIMEOUT_S
            executor.execute_raw(conn, "SELECT 1")

        timeout_stmts = [s for s in executed if "max_execution_time" in s.lower()]
        assert "SESSION" in timeout_stmts[0]

    def test_timeout_failure_does_not_raise(self):
        """Se SET SESSION max_execution_time fallisce la query prosegue."""
        mock_engine, mock_conn, _ = _make_engine_mock()
        executor = _make_executor(mock_engine)
        conn = _make_conn("mysql")

        call_count = [0]
        mock_result = MagicMock()
        mock_result.cursor.description = [("id",)]
        mock_result.fetchall.return_value = [(1,)]
        mock_result.fetchmany.side_effect = [[(1,)], []]

        def side_effect(stmt, *a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Unknown system variable 'max_execution_time'")
            return mock_result

        mock_conn.execute.side_effect = side_effect

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = TIMEOUT_S
            rows, cols = executor.execute_raw(conn, "SELECT 1")

        assert cols == ["id"]
        assert rows == [(1,)]

    def test_no_statement_timeout_for_mysql(self):
        """MySQL NON deve ricevere SET statement_timeout (è sintassi PostgreSQL)."""
        mock_engine, _, executed = _make_engine_mock()
        executor = _make_executor(mock_engine)
        conn = _make_conn("mysql")

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = TIMEOUT_S
            executor.execute_raw(conn, "SELECT 1")

        pg_stmts = [s for s in executed if "statement_timeout" in s.lower()]
        assert len(pg_stmts) == 0


# ─────────────────────────────────────────────────────────────────────────────
# ORACLE
# ─────────────────────────────────────────────────────────────────────────────

class TestTimeoutOracle:

    def test_call_timeout_set(self):
        """callTimeout deve essere impostato sull'oggetto raw dbapi_connection."""
        raw_dbapi = MagicMock(spec=["callTimeout"])
        raw_dbapi.callTimeout = 0

        mock_engine, mock_conn, _ = _make_engine_mock(raw_dbapi_conn=raw_dbapi)
        executor = _make_executor(mock_engine)
        conn = _make_conn("oracle")

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = TIMEOUT_S
            executor.execute_raw(conn, "SELECT 1 FROM DUAL")

        assert raw_dbapi.callTimeout == TIMEOUT_MS

    def test_call_timeout_value(self):
        """Il valore di callTimeout deve essere DB_QUERY_TIMEOUT * 1000."""
        raw_dbapi = MagicMock(spec=["callTimeout"])
        raw_dbapi.callTimeout = 0

        mock_engine, mock_conn, _ = _make_engine_mock(raw_dbapi_conn=raw_dbapi)
        executor = _make_executor(mock_engine)
        conn = _make_conn("oracle")

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = 120
            executor.execute_raw(conn, "SELECT 1 FROM DUAL")

        assert raw_dbapi.callTimeout == 120_000

    def test_no_call_timeout_if_attr_missing(self):
        """Se callTimeout non è disponibile (oracledb < 1.2) non deve sollevare."""
        raw_dbapi = MagicMock(spec=[])  # nessun attributo callTimeout
        mock_engine, mock_conn, _ = _make_engine_mock(raw_dbapi_conn=raw_dbapi)
        executor = _make_executor(mock_engine)
        conn = _make_conn("oracle")

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = TIMEOUT_S
            # Non deve sollevare
            executor.execute_raw(conn, "SELECT 1 FROM DUAL")

    def test_no_sql_set_for_oracle(self):
        """Oracle NON deve ricevere SET statement_timeout via SQL."""
        raw_dbapi = MagicMock(spec=["callTimeout"])
        mock_engine, _, executed = _make_engine_mock(raw_dbapi_conn=raw_dbapi)
        executor = _make_executor(mock_engine)
        conn = _make_conn("oracle")

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = TIMEOUT_S
            executor.execute_raw(conn, "SELECT 1 FROM DUAL")

        timeout_sql = [s for s in executed if "statement_timeout" in s.lower()
                       or "max_execution_time" in s.lower()]
        assert len(timeout_sql) == 0


# ─────────────────────────────────────────────────────────────────────────────
# MSSQL e SQLite — nessun SET timeout atteso
# ─────────────────────────────────────────────────────────────────────────────

class TestTimeoutNoOp:

    @pytest.mark.parametrize("db_type", ["mssql", "sqlite"])
    def test_no_timeout_set_statement(self, db_type):
        """MSSQL e SQLite non devono ricevere SET timeout via SQL."""
        mock_engine, _, executed = _make_engine_mock()
        executor = _make_executor(mock_engine)
        conn = _make_conn(db_type)
        if db_type == "sqlite":
            conn.host = ""
            conn.database = ":memory:"

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = TIMEOUT_S
            executor.execute_raw(conn, "SELECT 1")

        timeout_stmts = [s for s in executed
                         if any(k in s.lower() for k in
                                ("statement_timeout", "max_execution_time", "calltimeout"))]
        assert len(timeout_stmts) == 0


# ─────────────────────────────────────────────────────────────────────────────
# ISOLAMENTO — un DB non deve ricevere il timeout degli altri
# ─────────────────────────────────────────────────────────────────────────────

class TestTimeoutIsolation:

    def test_mysql_does_not_get_postgres_syntax(self):
        mock_engine, _, executed = _make_engine_mock()
        executor = _make_executor(mock_engine)

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = TIMEOUT_S
            executor.execute_raw(_make_conn("mysql"), "SELECT 1")

        assert not any("statement_timeout" in s for s in executed)

    def test_postgres_does_not_get_mysql_syntax(self):
        mock_engine, _, executed = _make_engine_mock()
        executor = _make_executor(mock_engine)

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = TIMEOUT_S
            executor.execute_raw(_make_conn("postgres"), "SELECT 1")

        assert not any("max_execution_time" in s for s in executed)

    def test_mssql_gets_neither_syntax(self):
        mock_engine, _, executed = _make_engine_mock()
        executor = _make_executor(mock_engine)

        with patch("app.config.settings") as mock_settings:
            mock_settings.DB_QUERY_TIMEOUT = TIMEOUT_S
            executor.execute_raw(_make_conn("mssql"), "SELECT 1")

        assert not any("statement_timeout" in s or "max_execution_time" in s
                        for s in executed)
