"""
tests/estrai/unit/test_query_executor_coverage_extra.py
=======================================================
Extra coverage for query_executor.py — targets uncovered branches:
  - _ottieni_connect_args for all DB types
  - _costruisci_connection_string edge cases (sqlite, oracle, mysql, mssql)
  - execute_query with geom_column and different formats
  - execute_raw chunked fetchmany loop
  - ResultBuilder / DataFrameBuilder
  - create_executor factory
  - _salvataggio_default fallback
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd

pytestmark = pytest.mark.unit

from app.db.query_executor import (
    DefaultEngineFactory,
    QueryExecutor,
    DatabaseConnection,
    NullLogger,
    ResultBuilder,
    DataFrameBuilder,
    QueryResult,
    create_executor,
)


def make_conn(type_="postgres"):
    return DatabaseConnection(
        type=type_, host="localhost", port=5432,
        database="testdb", username="user", password="p@ss!word"
    )


# =============================================================================
# _ottieni_connect_args — all DB types
# =============================================================================

class TestOttieniConnectArgs:

    def test_postgres(self):
        factory = DefaultEngineFactory()
        args = factory._ottieni_connect_args(make_conn("postgres"))
        assert args["connect_timeout"] == 60
        assert args["gssencmode"] == "disable"

    def test_postgresql(self):
        factory = DefaultEngineFactory()
        args = factory._ottieni_connect_args(make_conn("postgresql"))
        assert args["connect_timeout"] == 60
        assert args["gssencmode"] == "disable"

    def test_mysql(self):
        factory = DefaultEngineFactory()
        args = factory._ottieni_connect_args(make_conn("mysql"))
        assert args["connect_timeout"] == 60
        assert args["read_timeout"] == 60
        assert args["write_timeout"] == 60

    def test_mssql(self):
        factory = DefaultEngineFactory()
        args = factory._ottieni_connect_args(make_conn("mssql"))
        assert args["timeout"] == 60

    def test_sqlite(self):
        factory = DefaultEngineFactory()
        args = factory._ottieni_connect_args(make_conn("sqlite"))
        assert args["timeout"] == 60

    def test_oracle_returns_empty(self):
        factory = DefaultEngineFactory()
        args = factory._ottieni_connect_args(make_conn("oracle"))
        assert args == {}

    def test_unknown_type_returns_empty(self):
        factory = DefaultEngineFactory()
        conn = DatabaseConnection(
            type="unknown", host="h", port=0, database="d",
            username="u", password="p"
        )
        args = factory._ottieni_connect_args(conn)
        assert args == {}


# =============================================================================
# _costruisci_connection_string — all types
# =============================================================================

class TestCostruisciConnectionString:

    def test_postgres_string(self):
        factory = DefaultEngineFactory()
        cs = factory._costruisci_connection_string(make_conn("postgres"))
        assert cs.startswith("postgresql+psycopg2://")
        assert "p%40ss%21word" in cs  # URL-encoded password

    def test_postgresql_alias(self):
        factory = DefaultEngineFactory()
        cs = factory._costruisci_connection_string(make_conn("postgresql"))
        assert cs.startswith("postgresql+psycopg2://")

    def test_mysql_string(self):
        factory = DefaultEngineFactory()
        cs = factory._costruisci_connection_string(make_conn("mysql"))
        assert cs.startswith("mysql+pymysql://")
        assert "testdb" in cs

    def test_mssql_string(self):
        factory = DefaultEngineFactory()
        cs = factory._costruisci_connection_string(make_conn("mssql"))
        assert "mssql+pyodbc://" in cs
        assert "ODBC+Driver+18" in cs
        assert "TrustServerCertificate=yes" in cs

    def test_oracle_string(self):
        factory = DefaultEngineFactory()
        cs = factory._costruisci_connection_string(make_conn("oracle"))
        assert "oracle+oracledb://" in cs
        assert "service_name=testdb" in cs

    def test_sqlite_string(self):
        factory = DefaultEngineFactory()
        cs = factory._costruisci_connection_string(make_conn("sqlite"))
        assert cs == "sqlite:///testdb"

    def test_unsupported_raises(self):
        factory = DefaultEngineFactory()
        conn = DatabaseConnection(
            type="cassandra", host="h", port=0, database="d",
            username="u", password="p"
        )
        with pytest.raises(ValueError, match="non supportato"):
            factory._costruisci_connection_string(conn)


# =============================================================================
# execute_raw — chunked fetchmany
# =============================================================================

class TestExecuteRawChunked:

    def test_fetchmany_multi_chunks(self):
        """fetchmany returns two chunks then empty — all rows collected."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()

        # cursor.description for column names
        mock_result.cursor.description = [("col_a",), ("col_b",)]

        # Two chunks of data, then empty to stop
        chunk1 = [(1, "a"), (2, "b")]
        chunk2 = [(3, "c")]
        mock_result.fetchmany.side_effect = [chunk1, chunk2, []]

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value = mock_conn

        factory = MagicMock()
        factory.create_engine.return_value = mock_engine

        executor = QueryExecutor(engine_factory=factory, logger=NullLogger())
        rows, cols = executor.execute_raw(make_conn(), "SELECT 1")

        assert cols == ["col_a", "col_b"]
        assert len(rows) == 3
        assert rows[0] == (1, "a")
        assert rows[2] == (3, "c")

    def test_fetchmany_empty_result(self):
        """fetchmany returns empty immediately."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()

        mock_result.cursor.description = [("id",)]
        mock_result.fetchmany.side_effect = [[]]

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value = mock_conn

        factory = MagicMock()
        factory.create_engine.return_value = mock_engine

        executor = QueryExecutor(engine_factory=factory, logger=NullLogger())
        rows, cols = executor.execute_raw(make_conn(), "SELECT 1")

        assert rows == []
        assert cols == ["id"]


# =============================================================================
# execute_query — success with geom_column + intermediate snapshot
# =============================================================================

class TestExecuteQueryGeom:

    @patch("app.db.query_executor.save_result_blob")
    @patch("app.db.query_executor.get_temp_output_path")
    def test_execute_query_with_geom_column(self, mock_temp, mock_save):
        """execute_query passes geom_column to the save function."""
        tmp_dir = Path(tempfile.mkdtemp())
        output_path = tmp_dir / "test"
        mock_temp.return_value = output_path

        saved_path = tmp_dir / "test.csv"

        def fake_save(df, output_path, format, geom_column=None):
            saved_path.write_text("fake")
            fake_save.geom_column = geom_column
            return saved_path

        fake_save.geom_column = None

        # Build executor with mock factory
        mock_engine = MagicMock()
        mock_conn_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.cursor.description = [("id",), ("geom",)]
        mock_result.fetchmany.side_effect = [[(1, "POINT(0 0)")], []]
        mock_conn_ctx.__enter__ = MagicMock(return_value=mock_conn_ctx)
        mock_conn_ctx.__exit__ = MagicMock(return_value=False)
        mock_conn_ctx.execute.return_value = mock_result
        mock_engine.connect.return_value = mock_conn_ctx

        factory = MagicMock()
        factory.create_engine.return_value = mock_engine

        executor = QueryExecutor(
            engine_factory=factory,
            save_function=fake_save,
            logger=NullLogger(),
        )

        result = executor.execute_query(
            connection=make_conn(),
            sql="SELECT id, geom FROM t",
            project_id="proj1",
            output_filename="out",
            output_format="geojson",
            geom_column="geom",
        )

        assert result["success"] is True
        assert result["rows_count"] == 1
        assert fake_save.geom_column == "geom"

        # Cleanup
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


# =============================================================================
# execute_query — error path
# =============================================================================

class TestExecuteQueryError:

    def test_execute_query_catches_exception(self):
        factory = MagicMock()
        factory.create_engine.side_effect = RuntimeError("connection refused")

        executor = QueryExecutor(
            engine_factory=factory,
            logger=NullLogger(),
        )

        result = executor.execute_query(
            connection=make_conn(),
            sql="SELECT 1",
            project_id="proj",
            output_filename="out",
        )

        assert result["success"] is False
        assert "connection refused" in result["message"]


# =============================================================================
# ResultBuilder
# =============================================================================

class TestResultBuilder:

    def test_success_result(self):
        r = ResultBuilder.success(
            rows_count=10,
            columns=["a", "b"],
            output_path="/tmp/out.csv",
            project_id="p1",
            timings={"total": 1.5},
        )
        assert r.success is True
        assert r.rows_count == 10
        assert "out.csv" in r.message

    def test_error_result(self):
        r = ResultBuilder.error("something failed")
        assert r.success is False
        assert "something failed" in r.message

    def test_to_dict_success(self):
        r = ResultBuilder.success(5, ["x"], "/out.csv", "p", {"t": 1})
        d = ResultBuilder.to_dict(r)
        assert d["success"] is True
        assert d["rows_count"] == 5
        assert d["columns"] == ["x"]

    def test_to_dict_error(self):
        r = ResultBuilder.error("boom")
        d = ResultBuilder.to_dict(r)
        assert d["success"] is False
        assert "rows_count" not in d


# =============================================================================
# DataFrameBuilder
# =============================================================================

class TestDataFrameBuilder:

    def test_from_rows(self):
        rows = [(1, "a"), (2, "b")]
        cols = ["id", "name"]
        df = DataFrameBuilder.from_rows(rows, cols)
        assert len(df) == 2
        assert list(df.columns) == cols

    def test_from_rows_empty(self):
        df = DataFrameBuilder.from_rows([], ["a"])
        assert len(df) == 0


# =============================================================================
# create_executor factory
# =============================================================================

class TestCreateExecutor:

    def test_defaults(self):
        executor = create_executor()
        assert isinstance(executor, QueryExecutor)

    def test_custom_params(self):
        factory = MagicMock()
        save_fn = MagicMock()
        executor = create_executor(
            engine_factory=factory,
            save_function=save_fn,
            results_dir=Path("/custom"),
            logger=NullLogger(),
        )
        assert executor.RESULTS_DIR == Path("/custom")


# =============================================================================
# _salvataggio_default — fallback save
# =============================================================================

class TestSalvataggioDefault:

    def test_csv(self, tmp_path):
        df = pd.DataFrame({"a": [1, 2]})
        path = QueryExecutor._salvataggio_default(df, tmp_path / "out", "csv")
        assert path.suffix == ".csv"
        assert path.exists()

    def test_json(self, tmp_path):
        df = pd.DataFrame({"a": [1]})
        path = QueryExecutor._salvataggio_default(df, tmp_path / "out", "json")
        assert path.suffix == ".json"
        assert path.exists()

    def test_xlsx(self, tmp_path):
        df = pd.DataFrame({"a": [1]})
        path = QueryExecutor._salvataggio_default(df, tmp_path / "out", "xlsx")
        assert path.suffix == ".xlsx"
        assert path.exists()

    def test_unknown_format_falls_back_to_csv(self, tmp_path):
        df = pd.DataFrame({"a": [1]})
        path = QueryExecutor._salvataggio_default(df, tmp_path / "out", "parquet_bad")
        assert path.suffix == ".csv"


# =============================================================================
# DefaultEngineFactory — dispose_all
# =============================================================================

class TestDisposeAll:

    def test_dispose_clears_cache(self):
        factory = DefaultEngineFactory()
        mock_engine = MagicMock()
        factory._engine_cache["key1"] = mock_engine
        factory.dispose_all()
        assert len(factory._engine_cache) == 0
        mock_engine.dispose.assert_called_once()

    def test_dispose_ignores_errors(self):
        factory = DefaultEngineFactory()
        mock_engine = MagicMock()
        mock_engine.dispose.side_effect = RuntimeError("fail")
        factory._engine_cache["key1"] = mock_engine
        factory.dispose_all()  # Should not raise
        assert len(factory._engine_cache) == 0


# =============================================================================
# DefaultEngineFactory — unsupported type
# =============================================================================

class TestUnsupportedType:

    def test_create_engine_unsupported(self):
        factory = DefaultEngineFactory()
        conn = DatabaseConnection(
            type="cassandra", host="h", port=0, database="d",
            username="u", password="p"
        )
        with pytest.raises(ValueError, match="non supportato"):
            factory.create_engine(conn)


# =============================================================================
# DefaultEngineFactory — engine caching
# =============================================================================

class TestEngineCaching:

    @patch.object(DefaultEngineFactory, "_costruisci_connection_string", return_value="sqlite:///test")
    @patch("app.db.query_executor.create_engine")
    def test_cache_hit(self, mock_create, mock_cs):
        factory = DefaultEngineFactory()
        conn = make_conn("sqlite")
        engine1 = factory.create_engine(conn)
        engine2 = factory.create_engine(conn)
        assert engine1 is engine2
        assert mock_create.call_count == 1  # Only created once
