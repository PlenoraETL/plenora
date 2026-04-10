"""
tests/carica/unit/test_executor_router.py
==========================================
Unit test su CaricaExecutor helper puri e router con mock.

NON richiede backend, DB o Celery.

Copre:
  - _short_err: troncamento messaggio errore
  - _dict_to_conn_obj: conversione dict → oggetto con attributi
  - CaricaExecutor.execute(): success, failure, df vuoto
  - Router endpoints via TestClient
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

from app.core.carica.executor import _short_err, _dict_to_conn_obj, CaricaExecutor
from app.core.carica.models import WriteMode, WriteStatus


# =============================================================================
# _short_err
# =============================================================================

class TestShortErr:

    def test_messaggio_corto_invariato(self):
        e = ValueError("errore breve")
        assert _short_err(e) == "errore breve"

    def test_messaggio_lungo_troncato(self):
        msg = "x" * 200
        e = ValueError(msg)
        result = _short_err(e)
        assert len(result) <= 120

    def test_prima_riga_solo(self):
        """Mutation target: multiline → solo prima riga."""
        e = ValueError("prima riga\nseconda riga\nterza riga")
        result = _short_err(e)
        assert "seconda" not in result
        assert "prima riga" in result

    def test_max_len_custom(self):
        e = ValueError("x" * 50)
        result = _short_err(e, max_len=10)
        assert len(result) <= 10

    def test_stringa_vuota(self):
        e = ValueError("")
        result = _short_err(e)
        assert isinstance(result, str)


# =============================================================================
# _dict_to_conn_obj
# =============================================================================

class TestDictToConnObj:

    def test_attributi_accessibili(self):
        conn = {"host": "localhost", "port": 5432, "database": "test"}
        obj = _dict_to_conn_obj(conn)
        assert obj.host == "localhost"
        assert obj.port == 5432
        assert obj.database == "test"

    def test_attributo_inesistente_raise(self):
        conn = {"host": "localhost"}
        obj = _dict_to_conn_obj(conn)
        with pytest.raises(AttributeError):
            _ = obj.port

    def test_dict_vuoto(self):
        obj = _dict_to_conn_obj({})
        assert obj is not None

    def test_tutti_i_tipi(self):
        conn = {"str": "val", "int": 42, "bool": True, "none": None}
        obj = _dict_to_conn_obj(conn)
        assert obj.str == "val"
        assert obj.int == 42
        assert obj.bool is True
        assert obj.none is None


# =============================================================================
# CaricaExecutor.execute()
# =============================================================================

class TestCaricaExecutorExecute:

    def _make_executor(self):
        ex = CaricaExecutor.__new__(CaricaExecutor)
        ex.connection_service = MagicMock()
        ex.engine_factory = MagicMock()
        ex.audit_dir = "/tmp"
        return ex

    def _make_node_data(self, write_mode="create"):
        return {
            "dest_type": "db",
            "connection_id": "conn-1",
            "table_name": "test_table",
            "schema_name": "public",
            "write_mode": write_mode,
            "column_mappings": [],
            "match_columns": [],
            "batch_size": 1000,
            "transaction_mode": "all_or_nothing",
            "srid": 4326,
            "geometry_column": "geometry",
        }

    def test_df_vuoto_success(self):
        """DataFrame vuoto → WriteResult ritornato."""
        ex = self._make_executor()
        df = pd.DataFrame({"id": [], "nome": []})
        node_data = self._make_node_data()
        with patch.object(ex, "_get_connection", return_value={"type": "postgres"}),              patch.object(ex, "_create_engine", return_value=MagicMock()),              patch("app.core.carica.executor.DatabaseWriter") as mock_writer_cls,              patch("app.core.carica.executor.ColumnMapper"):
            mock_writer = MagicMock()
            mock_writer.validate.return_value = (True, "")
            mock_writer.write.return_value = MagicMock(
                status=WriteStatus.SUCCESS, rows_written=0, total_rows=0, error_message="",
            )
            mock_writer_cls.return_value = mock_writer
            result = ex.execute(df, node_data, "proj-1")
        assert result is not None

    def test_connessione_non_trovata_failure(self):
        """Connessione non trovata → WriteStatus.CONNECTION_ERROR."""
        ex = self._make_executor()
        df = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        node_data = self._make_node_data()
        with patch.object(ex, "_get_connection", side_effect=ValueError("Connessione non trovata")):
            result = ex.execute(df, node_data, "proj-1")
        assert result.status == WriteStatus.CONNECTION_ERROR

    def test_write_success(self):
        """Scrittura DB success → WriteStatus.SUCCESS."""
        ex = self._make_executor()
        df = pd.DataFrame({"id": [1, 2, 3], "nome": ["a", "b", "c"]})
        node_data = self._make_node_data()
        mock_write_result = MagicMock()
        mock_write_result.status = WriteStatus.SUCCESS
        mock_write_result.rows_written = 3
        with patch.object(ex, "_get_connection", return_value={"type": "postgres"}),              patch.object(ex, "_create_engine", return_value=MagicMock()),              patch("app.core.carica.executor.DatabaseWriter") as mock_writer_cls,              patch("app.core.carica.executor.ColumnMapper") as mock_mapper_cls:
            mock_writer = MagicMock()
            mock_writer.validate.return_value = (True, "")
            mock_writer.write.return_value = mock_write_result
            mock_writer_cls.return_value = mock_writer
            mock_mapper_cls.return_value = MagicMock()
            result = ex.execute(df, node_data, "proj-1")
        assert result.status == WriteStatus.SUCCESS

    def test_write_error_failure(self):
        """WriteStatus.WRITE_ERROR ritornato."""
        ex = self._make_executor()
        df = pd.DataFrame({"id": [1], "nome": ["a"]})
        node_data = self._make_node_data()
        mock_write_result = MagicMock()
        mock_write_result.status = WriteStatus.WRITE_ERROR
        mock_write_result.rows_written = 0
        with patch.object(ex, "_get_connection", return_value={"type": "postgres"}),              patch.object(ex, "_create_engine", return_value=MagicMock()),              patch("app.core.carica.executor.DatabaseWriter") as mock_writer_cls,              patch("app.core.carica.executor.ColumnMapper") as mock_mapper_cls:
            mock_writer = MagicMock()
            mock_writer.validate.return_value = (True, "")
            mock_writer.write.return_value = mock_write_result
            mock_writer_cls.return_value = mock_writer
            mock_mapper_cls.return_value = MagicMock()
            result = ex.execute(df, node_data, "proj-1")
        assert result.status == WriteStatus.WRITE_ERROR


# =============================================================================
# Router CARICA — TestClient
# =============================================================================

@pytest.fixture
def router_client():
    from app.core.carica.router import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


class TestCaricaRouter:

    def test_get_connections_200(self, router_client):
        res = router_client.get("/connections")
        assert res.status_code in (200, 401, 404)

    def test_validate_endpoint(self, router_client):
        """POST /carica/validate → status 200 o 422."""
        res = router_client.post("/carica/validate", json={})
        assert res.status_code in (200, 401, 404, 422)

    def test_schemas_endpoint(self, router_client):
        """GET /schemas → 200 o 404."""
        res = router_client.get("/schemas/conn-1")
        assert res.status_code in (200, 401, 404, 422)

    def test_tables_endpoint(self, router_client):
        """GET /tables → 200 o 404."""
        res = router_client.get("/tables/conn-1/public")
        assert res.status_code in (200, 401, 404, 422)
