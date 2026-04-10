"""
tests/estrai/unit/test_query_executor_unit.py
==============================================
Unit test su QueryExecutor, DefaultEngineFactory e helper puri.

NON richiede DB. Testa:
  - DefaultEngineFactory._costruisci_connection_string per ogni DB
  - DefaultEngineFactory._ottieni_connect_args
  - QueryExecutor._salvataggio_default per tutti i formati
  - ResultBuilder.success / .error / .to_dict
  - DataFrameBuilder.from_rows
  - NullLogger (non crasha)

Obiettivi coverage:
  - Rami if/elif per ogni tipo DB in _costruisci_connection_string
  - Fallback formato sconosciuto in _salvataggio_default → CSV
  - ResultBuilder.to_dict: campi presenti solo in success=True
"""

import tempfile
import os
import pytest
import pandas as pd
from pathlib import Path

pytestmark = pytest.mark.unit

from app.db.query_executor import (
    DefaultEngineFactory,
    QueryExecutor,
    ResultBuilder,
    DataFrameBuilder,
    NullLogger,
    DatabaseConnection,
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_conn(type_: str) -> DatabaseConnection:
    return DatabaseConnection(
        type=type_,
        host="localhost",
        port=5432,
        database="testdb",
        username="user",
        password="pass",
    )


# =============================================================================
# DefaultEngineFactory — _costruisci_connection_string
# =============================================================================

class TestConnectionString:

    @pytest.fixture
    def factory(self):
        return DefaultEngineFactory()

    def test_postgres(self, factory):
        conn = make_conn("postgres")
        s = factory._costruisci_connection_string(conn)
        assert s.startswith("postgresql+psycopg2://")
        assert "localhost" in s
        assert "testdb" in s

    def test_postgresql_alias(self, factory):
        """'postgresql' deve funzionare come alias di 'postgres'."""
        conn = make_conn("postgresql")
        s = factory._costruisci_connection_string(conn)
        assert s.startswith("postgresql+psycopg2://")

    def test_mysql(self, factory):
        conn = make_conn("mysql")
        s = factory._costruisci_connection_string(conn)
        assert s.startswith("mysql+pymysql://")

    def test_mssql(self, factory):
        conn = make_conn("mssql")
        s = factory._costruisci_connection_string(conn)
        assert s.startswith("mssql+pyodbc://")
        assert "ODBC+Driver+18" in s
        assert "TrustServerCertificate" in s

    def test_oracle(self, factory):
        conn = make_conn("oracle")
        s = factory._costruisci_connection_string(conn)
        assert s.startswith("oracle+oracledb://")
        assert "service_name=testdb" in s

    def test_sqlite(self, factory):
        conn = DatabaseConnection(
            type="sqlite", host="", port=0,
            database="/tmp/test.db", username="", password=""
        )
        s = factory._costruisci_connection_string(conn)
        assert s == "sqlite:////tmp/test.db"

    def test_tipo_non_supportato_raise(self, factory):
        """Mutation target: tipo sconosciuto → ValueError."""
        conn = make_conn("db2")
        with pytest.raises(ValueError, match="non supportato"):
            factory._costruisci_connection_string(conn)

    def test_password_con_caratteri_speciali_urlencode(self, factory):
        """Password con @ e / deve essere URL-encoded."""
        conn = DatabaseConnection(
            type="postgres", host="localhost", port=5432,
            database="db", username="user", password="p@ss/word"
        )
        s = factory._costruisci_connection_string(conn)
        assert "@" not in s.split("@")[0].split("//")[1]  # password encodata
        assert "p%40ss" in s or "p@" not in s.split("localhost")[0]

    # ── _ottieni_connect_args ──────────────────────────────────────────────

    def test_connect_args_postgres(self, factory):
        conn = make_conn("postgres")
        args = factory._ottieni_connect_args(conn)
        assert "connect_timeout" in args
        assert args["connect_timeout"] == 60
        assert "gssencmode" in args     # fix GSSAPI Windows

    def test_connect_args_mysql(self, factory):
        conn = make_conn("mysql")
        args = factory._ottieni_connect_args(conn)
        assert "connect_timeout" in args
        assert "read_timeout"    in args
        assert "write_timeout"   in args

    def test_connect_args_mssql(self, factory):
        conn = make_conn("mssql")
        args = factory._ottieni_connect_args(conn)
        assert "timeout" in args

    def test_connect_args_oracle_vuoto(self, factory):
        """Oracle non ha connect_args configurati (dict vuoto)."""
        conn = make_conn("oracle")
        args = factory._ottieni_connect_args(conn)
        assert args == {}

    def test_tipo_sconosciuto_args_vuoto(self, factory):
        conn = make_conn("db2")
        args = factory._ottieni_connect_args(conn)
        assert args == {}


# =============================================================================
# QueryExecutor._salvataggio_default
# =============================================================================

class TestSalvataggioDefault:

    @pytest.fixture
    def tmpdir_(self):
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    def _df(self):
        return pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})

    def test_salva_csv(self, tmpdir_):
        path = tmpdir_ / "output"
        saved = QueryExecutor._salvataggio_default(self._df(), path, "csv")
        assert saved.suffix == ".csv"
        assert saved.exists()
        content = saved.read_text()
        assert "id" in content
        assert "nome" in content

    def test_salva_json(self, tmpdir_):
        path = tmpdir_ / "output"
        saved = QueryExecutor._salvataggio_default(self._df(), path, "json")
        assert saved.suffix == ".json"
        assert saved.exists()

    def test_salva_xlsx(self, tmpdir_):
        path = tmpdir_ / "output"
        saved = QueryExecutor._salvataggio_default(self._df(), path, "xlsx")
        assert saved.suffix == ".xlsx"
        assert saved.exists()

    def test_formato_sconosciuto_fallback_csv(self, tmpdir_):
        """Mutation target: formato non riconosciuto → CSV come fallback."""
        path = tmpdir_ / "output"
        saved = QueryExecutor._salvataggio_default(self._df(), path, "parquet")
        assert saved.suffix == ".csv"   # fallback
        assert saved.exists()


# =============================================================================
# ResultBuilder
# =============================================================================

class TestResultBuilder:

    def test_success_fields(self):
        r = ResultBuilder.success(
            rows_count=42,
            columns=["id", "nome"],
            output_path="/tmp/out.csv",
            project_id="proj-1",
            timings={"total": 1.5},
        )
        assert r.success is True
        assert r.rows_count == 42
        assert r.columns == ["id", "nome"]
        assert r.output_path == "/tmp/out.csv"
        assert r.project_id == "proj-1"
        assert r.timings["total"] == 1.5

    def test_error_fields(self):
        r = ResultBuilder.error("timeout sul DB")
        assert r.success is False
        assert "timeout" in r.message.lower() or "Errore" in r.message
        assert r.rows_count == 0
        assert r.columns == []

    def test_to_dict_success_include_dettagli(self):
        """Mutation target: success=True → tutti i campi nel dict."""
        r = ResultBuilder.success(42, ["id"], "/tmp/out.csv", "p1", {})
        d = ResultBuilder.to_dict(r)
        assert d["success"] is True
        assert "rows_count"   in d
        assert "columns"      in d
        assert "output_path"  in d
        assert "project_id"   in d

    def test_to_dict_error_esclude_dettagli(self):
        """Mutation target: success=False → solo success + message."""
        r = ResultBuilder.error("errore")
        d = ResultBuilder.to_dict(r)
        assert d["success"] is False
        assert "message" in d
        assert "rows_count"  not in d
        assert "columns"     not in d
        assert "output_path" not in d


# =============================================================================
# DataFrameBuilder
# =============================================================================

class TestDataFrameBuilder:

    def test_from_rows_basic(self):
        rows = [(1, "Roma"), (2, "Milano")]
        cols = ["id", "citta"]
        df = DataFrameBuilder.from_rows(rows, cols)
        assert list(df.columns) == ["id", "citta"]
        assert len(df) == 2
        assert df.iloc[0]["citta"] == "Roma"

    def test_from_rows_vuoto(self):
        df = DataFrameBuilder.from_rows([], ["id", "nome"])
        assert len(df) == 0
        assert list(df.columns) == ["id", "nome"]


# =============================================================================
# NullLogger
# =============================================================================

class TestNullLogger:

    def test_info_non_crasha(self):
        logger = NullLogger()
        logger.info("messaggio info")

    def test_error_non_crasha(self):
        logger = NullLogger()
        logger.error("messaggio error")

    def test_debug_non_crasha(self):
        logger = NullLogger()
        logger.debug("messaggio debug")


# =============================================================================
# DefaultEngineFactory — tipo non supportato in create_engine
# =============================================================================

class TestEngineFactoryValidation:

    def test_tipo_non_supportato_raise_in_create_engine(self):
        factory = DefaultEngineFactory()
        conn = make_conn("db2")
        with pytest.raises(ValueError):
            factory.create_engine(conn)
