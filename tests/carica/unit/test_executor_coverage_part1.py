"""
tests/carica/unit/test_executor_coverage.py
=============================================
Unit tests for app.core.carica.executor — targeting uncovered lines:
97-99, 126-128, 149-151, 170-180, 192-193, 225-279, 294-318, 322-332, 336-360, 383-384
"""

import json
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch, PropertyMock

pytestmark = pytest.mark.unit

from app.core.carica.executor import CaricaExecutor, _short_err, _dict_to_conn_obj
from app.core.carica.models import (
    ColumnMapping,
    WriteConfig,
    WriteMode,
    WriteResult,
    WriteStatus,
    TransactionMode,
    ConflictPolicy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node_data(**overrides):
    """Minimal valid node_data dict."""
    base = {
        "connection_id": "conn-1",
        "schema_name": "public",
        "table_name": "test_table",
        "write_mode": "create",
        "column_mappings": [],
        "batch_size": 1000,
    }
    base.update(overrides)
    return base


def _simple_df():
    return pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})




# ===========================================================================
# _short_err
# ===========================================================================

class TestShortErr:

    def test_truncates_long_message(self):
        msg = "x" * 200
        result = _short_err(Exception(msg))
        assert len(result) == 120
        assert result == "x" * 120

    def test_takes_first_line_only(self):
        result = _short_err(Exception("line1\nline2\nline3"))
        assert result == "line1"

    def test_short_message_unchanged(self):
        result = _short_err(Exception("short"))
        assert result == "short"

    def test_strips_whitespace(self):
        result = _short_err(Exception("  hello  \n  world  "))
        assert result == "hello"


# ===========================================================================
# _dict_to_conn_obj
# ===========================================================================

class TestDictToConnObj:

    def test_sets_attributes(self):
        obj = _dict_to_conn_obj({"host": "localhost", "port": 5432, "type": "postgres"})
        assert obj.host == "localhost"
        assert obj.port == 5432
        assert obj.type == "postgres"

    def test_empty_dict(self):
        obj = _dict_to_conn_obj({})
        assert not hasattr(obj, "host")


# ===========================================================================
# CaricaExecutor.__init__  (lines 97-99)
# ===========================================================================

class TestInit:

    def test_default_attributes(self):
        executor = CaricaExecutor()
        assert executor.connection_service is None
        assert executor.engine_factory is None
        assert executor.audit_dir == "data/logs/carica"

    def test_custom_attributes(self):
        svc = MagicMock()
        factory = MagicMock()
        executor = CaricaExecutor(
            connection_service=svc,
            engine_factory=factory,
            audit_dir="/tmp/audit",
        )
        assert executor.connection_service is svc
        assert executor.engine_factory is factory
        assert executor.audit_dir == "/tmp/audit"


# ===========================================================================
# execute — config parse error (lines 126-128)
# ===========================================================================

class TestExecuteConfigError:

    def test_invalid_node_data_returns_validation_error(self):
        executor = CaricaExecutor()
        df = _simple_df()
        # write_mode "INVALID" will fail WriteMode enum parsing
        node_data = _make_node_data(write_mode="INVALID")
        result = executor.execute(df, node_data, project_id="p1")
        assert result.status == WriteStatus.VALIDATION_ERROR
        assert "non valida" in result.error_message
        assert result.total_rows == 2


# ===========================================================================
# execute — connection error (lines 149-151)
# ===========================================================================

class TestExecuteConnectionError:

    def test_get_connection_raises(self):
        svc = MagicMock()
        svc.get_connection.return_value = None
        executor = CaricaExecutor(connection_service=svc)
        df = _simple_df()
        result = executor.execute(df, _make_node_data(), project_id="p2")
        assert result.status == WriteStatus.CONNECTION_ERROR
        assert "non trovata" in result.error_message

    def test_create_engine_raises(self):
        svc = MagicMock()
        svc.get_connection.return_value = {"type": "postgres", "host": "h"}
        factory = MagicMock()
        factory.create_engine.side_effect = RuntimeError("boom")
        executor = CaricaExecutor(connection_service=svc, engine_factory=factory)
        df = _simple_df()
        result = executor.execute(df, _make_node_data(), project_id="p3")
        assert result.status == WriteStatus.CONNECTION_ERROR
        assert "fallita" in result.error_message
        assert result.total_rows == 2


# ===========================================================================
# execute — alias correction B-BE-59  (lines 170-180)
# ===========================================================================

class TestExecuteAliasCorrection:

    @patch("app.core.carica.executor.DatabaseWriter")
    @patch("app.core.carica.executor.ColumnMapper")
    def test_alias_to_original_applied(self, MockMapper, MockWriter):
        """When column_mappings is empty and availableColumns has aliases,
        dest_column should be corrected from alias to original name."""
        svc = MagicMock()
        svc.get_connection.return_value = {"type": "postgres", "host": "h"}
        factory = MagicMock()
        executor = CaricaExecutor(connection_service=svc, engine_factory=factory)

        # auto_map returns mappings with alias as dest_column
        cm = ColumnMapping(source_column="id2", dest_column="id2", dest_type="int")
        MockMapper.return_value.auto_map.return_value = [cm]

        mock_writer_inst = MockWriter.return_value
        mock_writer_inst.validate.return_value = (True, None)
        mock_writer_inst.write.return_value = WriteResult(
            status=WriteStatus.SUCCESS, rows_written=2, total_rows=2
        )

        node_data = _make_node_data(
            column_mappings=[],  # empty triggers auto-map
            availableColumns=[
                {"name": "id", "alias": "id2"},
            ],
        )
        df = pd.DataFrame({"id2": [1, 2]})
        result = executor.execute(df, node_data)

        # dest_column should have been corrected from "id2" to "id"
        assert cm.dest_column == "id"
        assert result.status == WriteStatus.SUCCESS

    @patch("app.core.carica.executor.DatabaseWriter")
    @patch("app.core.carica.executor.ColumnMapper")
    def test_no_alias_no_change(self, MockMapper, MockWriter):
        """When availableColumns has no alias, dest_column stays the same."""
        svc = MagicMock()
        svc.get_connection.return_value = {"type": "postgres", "host": "h"}
        factory = MagicMock()
        executor = CaricaExecutor(connection_service=svc, engine_factory=factory)

        cm = ColumnMapping(source_column="id", dest_column="id", dest_type="int")
        MockMapper.return_value.auto_map.return_value = [cm]

        mock_writer_inst = MockWriter.return_value
        mock_writer_inst.validate.return_value = (True, None)
        mock_writer_inst.write.return_value = WriteResult(
            status=WriteStatus.SUCCESS, rows_written=2, total_rows=2
        )

        node_data = _make_node_data(
            column_mappings=[],
            availableColumns=[{"name": "id", "alias": ""}],
        )
        df = pd.DataFrame({"id": [1, 2]})
        result = executor.execute(df, node_data)
        assert cm.dest_column == "id"

    @patch("app.core.carica.executor.DatabaseWriter")
    @patch("app.core.carica.executor.ColumnMapper")
    def test_no_available_columns_key(self, MockMapper, MockWriter):
        """When no availableColumns at all, auto-map runs without alias step."""
        svc = MagicMock()
        svc.get_connection.return_value = {"type": "postgres", "host": "h"}
        factory = MagicMock()
        executor = CaricaExecutor(connection_service=svc, engine_factory=factory)

        cm = ColumnMapping(source_column="id", dest_column="id", dest_type="int")
        MockMapper.return_value.auto_map.return_value = [cm]

        mock_writer_inst = MockWriter.return_value
        mock_writer_inst.validate.return_value = (True, None)
        mock_writer_inst.write.return_value = WriteResult(
            status=WriteStatus.SUCCESS, rows_written=2, total_rows=2
        )

        node_data = _make_node_data(column_mappings=[])
        # no "availableColumns" key at all
        df = pd.DataFrame({"id": [1, 2]})
        result = executor.execute(df, node_data)
        assert cm.dest_column == "id"


# ===========================================================================
# execute — validation failure (lines 192-193)
# ===========================================================================

class TestExecuteValidationFailure:

    @patch("app.core.carica.executor.DatabaseWriter")
    def test_writer_validate_fails(self, MockWriter):
        svc = MagicMock()
        svc.get_connection.return_value = {"type": "postgres", "host": "h"}
        factory = MagicMock()
        executor = CaricaExecutor(connection_service=svc, engine_factory=factory)

        mock_writer_inst = MockWriter.return_value
        mock_writer_inst.validate.return_value = (False, "table missing")

        df = _simple_df()
        node_data = _make_node_data(
            column_mappings=[{"source_column": "id", "dest_column": "id", "dest_type": "int"}]
        )
        result = executor.execute(df, node_data)
        assert result.status == WriteStatus.VALIDATION_ERROR
        assert result.error_message == "table missing"
        assert result.total_rows == 2


# ===========================================================================
# validate_config (lines 225-279)
# ===========================================================================

class TestValidateConfig:

    def test_invalid_config_returns_errors(self):
        executor = CaricaExecutor()
        df = _simple_df()
        result = executor.validate_config(df, {"write_mode": "BOGUS"})
        assert result["is_valid"] is False
        assert len(result["errors"]) > 0
        assert "non valida" in result["errors"][0].lower() or "Configurazione" in result["errors"][0]

    def test_connection_unavailable(self):
        svc = MagicMock()
        svc.get_connection.return_value = None
        executor = CaricaExecutor(connection_service=svc)
        df = _simple_df()
        result = executor.validate_config(df, _make_node_data())
        assert result["is_valid"] is False
        assert any("onnessione" in e for e in result["errors"])

    @patch("app.core.carica.executor.DatabaseWriter")
    @patch("app.core.carica.executor.ColumnMapper")
    def test_valid_config_with_warnings(self, MockMapper, MockWriter):
        svc = MagicMock()
        svc.get_connection.return_value = {"type": "postgres", "host": "h"}
        factory = MagicMock()
        executor = CaricaExecutor(connection_service=svc, engine_factory=factory)

        mock_writer = MockWriter.return_value
        mock_writer.table_exists.return_value = True
        mock_writer.validate.return_value = (True, None)

        MockMapper.return_value.validate_mapping.return_value = (True, [])

        # Large batch_size triggers warning
        node_data = _make_node_data(
            batch_size=20000,
            column_mappings=[{"source_column": "id", "dest_column": "id", "dest_type": "int"}],
        )
        # Large DataFrame triggers warning
        df = pd.DataFrame({"id": range(200000)})
        result = executor.validate_config(df, node_data)
        assert result["is_valid"] is True
        assert len(result["warnings"]) == 2
        assert "Batch size elevato" in result["warnings"][0]
        assert "Dataset grande" in result["warnings"][1]
        assert result["table_exists"] is True
        assert result["total_rows"] == 200000

    @patch("app.core.carica.executor.DatabaseWriter")
    def test_valid_config_no_column_mappings(self, MockWriter):
        """When column_mappings is empty, the mapper validation is skipped."""
        svc = MagicMock()
        svc.get_connection.return_value = {"type": "postgres", "host": "h"}
        factory = MagicMock()
        executor = CaricaExecutor(connection_service=svc, engine_factory=factory)

        mock_writer = MockWriter.return_value
        mock_writer.table_exists.return_value = False
        mock_writer.validate.return_value = (True, None)

        node_data = _make_node_data(column_mappings=[])
        df = _simple_df()
        result = executor.validate_config(df, node_data)
        assert result["is_valid"] is True
        assert result["column_count"] == 0

    @patch("app.core.carica.executor.DatabaseWriter")
    @patch("app.core.carica.executor.ColumnMapper")
    def test_mode_validation_error_appended(self, MockMapper, MockWriter):
        svc = MagicMock()
        svc.get_connection.return_value = {"type": "postgres", "host": "h"}
        factory = MagicMock()
        executor = CaricaExecutor(connection_service=svc, engine_factory=factory)

        mock_writer = MockWriter.return_value
        mock_writer.table_exists.return_value = True
        mock_writer.validate.return_value = (False, "mode error detail")
        MockMapper.return_value.validate_mapping.return_value = (False, ["col X missing"])

        node_data = _make_node_data(
            column_mappings=[{"source_column": "id", "dest_column": "id", "dest_type": "int"}],
        )
        df = _simple_df()
        result = executor.validate_config(df, node_data)
        assert result["is_valid"] is False
        assert "col X missing" in result["errors"]
        assert "mode error detail" in result["errors"]


# ===========================================================================
# _get_connection (lines 294-318)
# ===========================================================================

class TestGetConnection:

    def test_connection_service_returns_dict(self):
        svc = MagicMock()
        svc.get_connection.return_value = {"type": "postgres", "host": "h"}
        executor = CaricaExecutor(connection_service=svc)
        result = executor._get_connection("conn-1")
        assert result == {"type": "postgres", "host": "h"}

    def test_connection_service_returns_obj_with_dict_method(self):
        svc = MagicMock()
        conn_obj = MagicMock()
        conn_obj.dict.return_value = {"type": "mysql", "host": "db"}
        svc.get_connection.return_value = conn_obj
        executor = CaricaExecutor(connection_service=svc)
        result = executor._get_connection("conn-2")
        assert result == {"type": "mysql", "host": "db"}

    def test_connection_service_returns_obj_with_dunder_dict(self):
        svc = MagicMock()

        class PlainObj:
            pass

        obj = PlainObj()
        obj.type = "oracle"
        obj.host = "ora-host"
        # Remove .dict so hasattr(conn, 'dict') is False
        svc.get_connection.return_value = obj
        executor = CaricaExecutor(connection_service=svc)
        result = executor._get_connection("conn-3")
        assert result["type"] == "oracle"
        assert result["host"] == "ora-host"

    def test_connection_service_returns_none_raises(self):
        svc = MagicMock()
        svc.get_connection.return_value = None
        executor = CaricaExecutor(connection_service=svc)
        with pytest.raises(ValueError, match="non trovata"):
            executor._get_connection("missing")

    @patch("app.db.db_service.fetchone")
    @patch("app.services.connection_service._decrypt_password", return_value="secret")
    def test_fallback_sqlite_found(self, mock_decrypt, mock_fetchone):
        """Without connection_service, falls back to SQLite."""
        mock_row = {
            "id": "c1", "name": "Test", "db_type": "postgres",
            "host": "localhost", "port": 5432, "database": "mydb",
            "db_schema": "public", "username": "user", "password": "enc",
        }
        # sqlite3.Row-like object supporting dict()
        mock_fetchone.return_value = MagicMock(**{"__getitem__": mock_row.__getitem__, "keys": mock_row.keys})
        # Make dict(row) work
        mock_fetchone.return_value.__iter__ = lambda self: iter(mock_row)
        mock_fetchone.return_value.__getitem__ = mock_row.__getitem__

        # Provide a proper dict-like mock
        class DictRow:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, key):
                return self._d[key]
            def keys(self):
                return self._d.keys()
            def get(self, key, default=None):
                return self._d.get(key, default)

        mock_fetchone.return_value = DictRow(mock_row)

        executor = CaricaExecutor(connection_service=None)
        result = executor._get_connection("c1")
        assert result["id"] == "c1"
        assert result["type"] == "postgres"
        assert result["password"] == "secret"
        mock_decrypt.assert_called_once_with("enc")

    @patch("app.db.db_service.fetchone", return_value=None)
    def test_fallback_sqlite_not_found(self, mock_fetchone):
        executor = CaricaExecutor(connection_service=None)
        with pytest.raises(ValueError, match="non trovata"):
            executor._get_connection("missing")
