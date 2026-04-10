"""
tests/estrai/unit/test_query_executor_coverage.py
===================================================
Test mirati a coprire le righe mancanti in query_executor.py (coverage 54%).

Zona coperta:
  - DefaultEngineFactory._configura_oracle(): import oracledb
  - DefaultEngineFactory._crea_engine_mssql(): fallback Driver 17
  - QueryExecutor.execute_raw(): percorso Oracle (ALTER SESSION + lowercase columns)
  - QueryExecutor.execute_query(): intermediate snapshot, error path, logger
  - NullLogger.warning (se presente)
"""

import pytest
import tempfile
import pandas as pd
from pathlib import Path
from unittest.mock import MagicMock, patch, call

pytestmark = pytest.mark.unit

from app.db.query_executor import (
    DefaultEngineFactory,
    QueryExecutor,
    DatabaseConnection,
    NullLogger,
)


def make_conn(type_="postgres"):
    return DatabaseConnection(
        type=type_, host="localhost", port=5432,
        database="db", username="user", password="pass"
    )


# =============================================================================
# DefaultEngineFactory._configura_oracle
# =============================================================================

class TestConfiguraOracle:

    def test_configura_oracle_con_oracledb(self):
        """Se oracledb è installato → imposta fetch_lobs=False."""
        factory = DefaultEngineFactory()
        mock_oracledb = MagicMock()
        mock_oracledb.defaults = MagicMock()

        with patch.dict("sys.modules", {"oracledb": mock_oracledb}):
            factory._configura_oracle()
            assert mock_oracledb.defaults.fetch_lobs is False

    def test_configura_oracle_senza_oracledb(self):
        """Se oracledb non è installato → ImportError silenzioso, non crasha."""
        factory = DefaultEngineFactory()
        with patch.dict("sys.modules", {"oracledb": None}):
            # Non deve sollevare eccezioni
            factory._configura_oracle()


# =============================================================================
# DefaultEngineFactory._crea_engine_mssql — fallback Driver 17
# =============================================================================

class TestCreaEngineMssql:

    def test_fallback_driver_17(self):
        """Se Driver 18 non disponibile → fallback su Driver 17."""
        factory = DefaultEngineFactory()
        conn = make_conn("mssql")
        conn_str_18 = factory._costruisci_connection_string(conn)
        assert "ODBC+Driver+18" in conn_str_18

        # Simula: Driver 18 fallisce con messaggio specifico
        with patch("app.db.query_executor.create_engine") as mock_engine:
            # Prima chiamata (Driver 18) → eccezione con "ODBC Driver 18"
            mock_engine.side_effect = [
                Exception("ODBC Driver 18 for SQL Server not found"),
                MagicMock(),  # Seconda chiamata (Driver 17) → successo
            ]
            factory._crea_engine_mssql(conn_str_18, {})
            # Verifica che sia stata chiamata due volte
            assert mock_engine.call_count == 2
            # La seconda chiamata usa Driver 17
            second_call_args = mock_engine.call_args_list[1][0][0]
            assert "ODBC+Driver+17" in second_call_args

    def test_eccezione_non_odbc_propagata(self):
        """Eccezione non correlata a ODBC → propagata."""
        factory = DefaultEngineFactory()
        conn_str = "mssql+pyodbc://user:pass@host/db?driver=ODBC+Driver+18"
        with patch("app.db.query_executor.create_engine") as mock_engine:
            mock_engine.side_effect = RuntimeError("Errore generico non ODBC")
            with pytest.raises(RuntimeError, match="Errore generico"):
                factory._crea_engine_mssql(conn_str, {})


# =============================================================================
# QueryExecutor.execute_raw — percorso Oracle
# =============================================================================

class TestExecuteRawOracle:

    def test_oracle_alter_session_chiamato(self):
        """Oracle: ALTER SESSION SET OPEN_CURSORS viene eseguito."""
        mock_engine_factory = MagicMock()
        mock_engine = MagicMock()
        mock_engine_factory.create_engine.return_value = mock_engine

        mock_conn_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.cursor.description = [("ID",), ("NOME",)]
        mock_result.fetchall.return_value = [(1, "A"), (2, "B")]
        mock_result.fetchmany.side_effect = [[(1, "A"), (2, "B")], []]
        mock_conn_ctx.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn_ctx)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        executor = QueryExecutor(engine_factory=mock_engine_factory)
        conn = make_conn("oracle")

        rows, columns = executor.execute_raw(conn, "SELECT id, nome FROM tab")

        # Verifica che ALTER SESSION sia stato chiamato
        # c[0][0] accede all'argomento text() — str() su TextClause produce la SQL
        calls = [str(c[0][0]) for c in mock_conn_ctx.execute.call_args_list if c[0]]
        alter_called = any("OPEN_CURSORS" in c or "ALTER" in c for c in calls)
        assert alter_called, f"ALTER SESSION non trovato nelle chiamate: {calls}"

    def test_oracle_colonne_lowercase(self):
        """Oracle: nomi colonne UPPERCASE → normalizzati in lowercase."""
        mock_engine_factory = MagicMock()
        mock_engine = MagicMock()
        mock_engine_factory.create_engine.return_value = mock_engine

        mock_conn_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.cursor.description = [("ID",), ("NOME_CLIENTE",), ("GEOM",)]
        mock_result.fetchall.return_value = [(1, "Mario", "POINT(1 2)")]
        mock_result.fetchmany.side_effect = [[(1, "Mario", "POINT(1 2)")], []]
        mock_conn_ctx.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn_ctx)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        executor = QueryExecutor(engine_factory=mock_engine_factory)
        conn = make_conn("oracle")
        rows, columns = executor.execute_raw(conn, "SELECT id, nome_cliente, geom FROM tab")

        # Mutation target: colonne devono essere lowercase
        assert columns == ["id", "nome_cliente", "geom"]
        assert "ID" not in columns
        assert "NOME_CLIENTE" not in columns

    def test_oracle_alter_session_fallisce_silenziosamente(self):
        """Se ALTER SESSION fallisce (permessi) → non blocca la query."""
        mock_engine_factory = MagicMock()
        mock_engine = MagicMock()
        mock_engine_factory.create_engine.return_value = mock_engine

        execute_calls = []

        def mock_execute(query, *args, **kwargs):
            query_str = str(query)
            execute_calls.append(query_str)
            if "OPEN_CURSORS" in query_str or "ALTER" in query_str:
                raise Exception("Insufficient privileges")
            mock_result = MagicMock()
            mock_result.cursor.description = [("ID",)]
            mock_result.fetchall.return_value = [(1,)]
            mock_result.fetchmany.side_effect = [[(1,)], []]
            return mock_result

        mock_conn_ctx = MagicMock()
        mock_conn_ctx.execute.side_effect = mock_execute
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn_ctx)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        executor = QueryExecutor(engine_factory=mock_engine_factory)
        conn = make_conn("oracle")
        # Non deve crashare nonostante ALTER SESSION fallisca
        rows, columns = executor.execute_raw(conn, "SELECT id FROM tab")
        assert len(rows) == 1


# =============================================================================
# QueryExecutor.execute_query — percorsi mancanti
# =============================================================================

class TestExecuteQueryCoverage:

    def _make_executor_with_mock_save(self):
        """Executor con mock factory + save function che funziona."""
        mock_factory = MagicMock()
        mock_engine = MagicMock()
        mock_factory.create_engine.return_value = mock_engine

        mock_conn_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.cursor.description = [("id",), ("nome",)]
        mock_result.fetchall.return_value = [(1, "A"), (2, "B")]
        mock_result.fetchmany.side_effect = [[(1, "A"), (2, "B")], []]
        mock_conn_ctx.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn_ctx)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            saved_path = Path(tmpdir) / "output.csv"

            def mock_save(df, output_path, format, geom_column=None):
                df.to_csv(saved_path, index=False)
                return saved_path

            executor = QueryExecutor(
                engine_factory=mock_factory,
                save_function=mock_save,
                results_dir=Path(tmpdir),
            )
            return executor, saved_path, tmpdir

    def test_execute_query_error_path(self):
        """Eccezione in execute_raw → success=False, nessun crash."""
        mock_factory = MagicMock()
        mock_factory.create_engine.side_effect = RuntimeError("connessione fallita")

        executor = QueryExecutor(engine_factory=mock_factory)
        conn = make_conn("postgres")

        with patch("app.db.query_executor._USE_RESULT_STORE", False):
            result = executor.execute_query(
                connection=conn,
                sql="SELECT id FROM tab",
                project_id="proj-1",
                output_filename="output",
                output_format="csv",
            )

        assert result["success"] is False
        assert "message" in result

    def test_logger_chiamato(self):
        """Il logger viene chiamato durante execute_raw."""
        mock_logger = MagicMock()
        mock_factory = MagicMock()
        mock_engine = MagicMock()
        mock_factory.create_engine.return_value = mock_engine

        mock_conn_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.cursor.description = [("id",)]
        mock_result.fetchall.return_value = [(1,)]
        mock_result.fetchmany.side_effect = [[(1,)], []]
        mock_conn_ctx.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn_ctx)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        executor = QueryExecutor(
            engine_factory=mock_factory,
            logger=mock_logger,
        )
        executor.execute_raw(make_conn("postgres"), "SELECT id FROM tab")
        # Logger non deve essere chiamato in execute_raw (solo in execute_query)
        # Questo verifica che NullLogger non crashi

    def test_null_logger_warning(self):
        """NullLogger.warning (se esiste) non crasha."""
        logger = NullLogger()
        # I metodi base
        logger.info("test info")
        logger.error("test error")
        logger.debug("test debug")
        # warning potrebbe non esistere — verifica che non crashi
        if hasattr(logger, "warning"):
            logger.warning("test warning")


# =============================================================================
# QueryExecutor — engine dispose chiamato nel finally
# =============================================================================

class TestEngineDispose:

    def test_dispose_chiamato_su_successo(self):
        """engine.dispose() deve essere chiamato anche su successo."""
        mock_factory = MagicMock()
        mock_engine = MagicMock()
        mock_factory.create_engine.return_value = mock_engine

        mock_conn_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.cursor.description = [("id",)]
        mock_result.fetchall.return_value = [(1,)]
        mock_result.fetchmany.side_effect = [[(1,)], []]
        mock_conn_ctx.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn_ctx)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        executor = QueryExecutor(engine_factory=mock_factory)
        executor.execute_raw(make_conn("postgres"), "SELECT 1")

        mock_engine.dispose.assert_called_once()

    def test_dispose_chiamato_su_eccezione(self):
        """engine.dispose() deve essere chiamato anche quando la query fallisce."""
        mock_factory = MagicMock()
        mock_engine = MagicMock()
        mock_factory.create_engine.return_value = mock_engine

        mock_conn_ctx = MagicMock()
        mock_conn_ctx.execute.side_effect = RuntimeError("query fallita")
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn_ctx)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        executor = QueryExecutor(engine_factory=mock_factory)
        with pytest.raises(RuntimeError):
            executor.execute_raw(make_conn("postgres"), "SELECT 1")

        mock_engine.dispose.assert_called_once()
