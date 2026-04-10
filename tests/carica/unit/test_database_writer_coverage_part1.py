"""
tests/carica/unit/test_database_writer_coverage.py
===================================================
Coverage-focused unit tests for DatabaseWriter.

Targets uncovered lines: 77-80, 89-149, 153-175, 183-193, 197-209,
213-220, 224-231, 240-291, 295-306, 310-321, 333-334, 428-434,
525-567, 633, 685, 794, 796, 824-828, 889-890, 895, 916-922.

NON richiede database reale.
"""

import pytest
import time
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch, PropertyMock, call
from datetime import datetime

pytestmark = pytest.mark.unit

from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.models import (
    ColumnMapping, WriteConfig, WriteMode, WriteStatus,
    TransactionMode, ConflictPolicy, WriteResult, ConflictReport,
    ConflictDetail, BatchProgress,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_col(src, dest=None, dest_type="str", is_geom=False, srid=None, pk=False, skip=False):
    return ColumnMapping(
        source_column=src, dest_column=dest or src,
        dest_type=dest_type, is_geometry=is_geom,
        srid=srid, is_primary_key=pk, skip=skip,
    )


def make_config(**kwargs):
    defaults = dict(
        connection_id="conn-1",
        table_name="test_table",
        schema_name="public",
        write_mode=WriteMode.CREATE,
        column_mappings=[make_col("id", dest_type="int"), make_col("nome")],
        match_columns=[],
        update_columns=[],
        geometry_srid=4326,
        geometry_column="geometry",
        batch_size=1000,
        transaction_mode=TransactionMode.ALL_OR_NOTHING,
        create_spatial_index=True,
        conflict_policy=ConflictPolicy.SKIP,
        if_table_exists="error",
    )
    defaults.update(kwargs)
    return WriteConfig(**defaults)


def make_writer_via_init(db_type="postgres", **config_kwargs):
    """Create DatabaseWriter through __init__ to cover lines 77-80."""
    config = make_config(**config_kwargs)
    mock_engine = MagicMock()
    mock_engine.dialect.name = "postgresql"
    writer = DatabaseWriter(config, mock_engine, db_type)
    return writer


def make_writer(db_type="postgres", **config_kwargs):
    """Create DatabaseWriter with mocked internals (bypass __init__)."""
    config = make_config(**config_kwargs)
    writer = DatabaseWriter.__new__(DatabaseWriter)
    writer.config = config
    writer.engine = MagicMock()
    writer.engine.dialect.name = "postgresql"
    writer.db_type = db_type
    writer.progress_callback = None
    writer.ddl = MagicMock()
    writer.ddl.generate_insert.return_value = ("INSERT INTO t VALUES (:id,:nome)", ["id", "nome"])
    writer.ddl.generate_update.return_value = ("UPDATE t SET nome=:nome WHERE id=:match_id", ["nome"])
    writer.ddl.generate_upsert.return_value = ("INSERT INTO t ... ON CONFLICT ...", ["id", "nome"])
    writer.ddl.generate_drop_table.return_value = "DROP TABLE IF EXISTS public.test_table"
    writer.ddl.generate_truncate.return_value = "TRUNCATE TABLE public.test_table"
    writer.ddl.generate_create_table.return_value = "CREATE TABLE public.test_table (...)"
    writer.ddl.generate_spatial_index.return_value = ["CREATE INDEX ..."]
    writer.ddl.qualified_table_name.return_value = '"public"."test_table"'
    writer.ddl.query_table_exists.return_value = "SELECT COUNT(*) ..."
    writer.ddl.query_table_columns.return_value = "SELECT column_name, data_type ..."
    writer.ddl.query_list_schemas.return_value = "SELECT schema_name ..."
    writer.ddl.query_list_tables.return_value = "SELECT table_name ..."
    return writer


def make_result(n=5):
    return WriteResult(
        started_at="", total_rows=n, rows_written=0,
        table_name="public.test_table", write_mode="create",
    )




# =============================================================================
# __init__ (lines 77-80)
# =============================================================================

class TestInit:

    def test_init_sets_engine_and_db_type(self):
        """Lines 77-80: super().__init__, engine, db_type.lower(), DDLGenerator."""
        writer = make_writer_via_init(db_type="POSTGRES")
        assert writer.db_type == "postgres"
        assert writer.engine is not None
        assert writer.config.table_name == "test_table"

    def test_init_db_type_lowercased(self):
        writer = make_writer_via_init(db_type="Oracle")
        assert writer.db_type == "oracle"

    def test_init_ddl_generator_created(self):
        """DDLGenerator should be created with the lowered db_type."""
        with patch("app.core.carica.database_writer.DDLGenerator") as mock_ddl_cls:
            writer = make_writer_via_init(db_type="MSSQL")
        mock_ddl_cls.assert_called_once_with("mssql")

    def test_init_progress_callback_stored(self):
        config = make_config()
        mock_engine = MagicMock()
        cb = MagicMock()
        writer = DatabaseWriter(config, mock_engine, "postgres", progress_callback=cb)
        assert writer.progress_callback is cb


# =============================================================================
# validate() (lines 89-149)
# =============================================================================

class TestValidate:

    def test_connection_failure_returns_false(self):
        """Line 89-93: SELECT 1 fails."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("Connection refused")
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        ok, msg = writer.validate()
        assert ok is False
        assert "Connessione al database fallita" in msg

    def test_create_mode_table_exists_if_error(self):
        """Lines 98-103: CREATE + table exists + if_table_exists='error'."""
        writer = make_writer(write_mode=WriteMode.CREATE, if_table_exists="error")
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "table_exists", return_value=True), \
             patch.object(writer, "_check_create_permission", return_value=(True, "")):
            ok, msg = writer.validate()

        assert ok is False
        assert "esiste già" in msg

    def test_create_mode_table_exists_if_replace_ok(self):
        """CREATE + table exists + if_table_exists='replace' -> passes check."""
        writer = make_writer(write_mode=WriteMode.CREATE, if_table_exists="replace")
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "table_exists", return_value=True), \
             patch.object(writer, "_check_create_permission", return_value=(True, "")):
            ok, msg = writer.validate()

        assert ok is True
        assert msg == ""

    def test_append_table_not_exists_returns_false(self):
        """Lines 105-113: APPEND/UPDATE/UPSERT/TRUNCATE require existing table."""
        for mode in [WriteMode.APPEND, WriteMode.UPDATE, WriteMode.UPSERT, WriteMode.TRUNCATE_INSERT]:
            writer = make_writer(write_mode=mode, match_columns=["id"] if mode in (WriteMode.UPDATE, WriteMode.UPSERT) else [])
            mock_conn = MagicMock()
            writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
            writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

            with patch.object(writer, "table_exists", return_value=False):
                ok, msg = writer.validate()

            assert ok is False, f"Expected False for {mode}"
            assert "non esiste" in msg

    def test_update_no_match_columns_returns_false(self):
        """Lines 116-121: UPDATE/UPSERT without match_columns."""
        for mode in [WriteMode.UPDATE, WriteMode.UPSERT]:
            writer = make_writer(write_mode=mode, match_columns=[])
            mock_conn = MagicMock()
            writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
            writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

            with patch.object(writer, "table_exists", return_value=True):
                ok, msg = writer.validate()

            assert ok is False
            assert "colonna match" in msg

    def test_create_permission_check_fails(self):
        """Lines 124-127: CREATE permission check fails."""
        writer = make_writer(write_mode=WriteMode.CREATE, if_table_exists="replace")
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "table_exists", return_value=False), \
             patch.object(writer, "_check_create_permission", return_value=(False, "No permission")):
            ok, msg = writer.validate()

        assert ok is False
        assert msg == "No permission"

    def test_append_invalid_dest_columns_in_mapping(self):
        """Lines 131-147: APPEND with column_mappings pointing to non-existent dest cols."""
        mappings = [
            make_col("id", dest="id_col", dest_type="int"),
            make_col("nome", dest="bad_column"),
        ]
        writer = make_writer(
            write_mode=WriteMode.APPEND,
            column_mappings=mappings,
        )
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "table_exists", return_value=True), \
             patch.object(writer, "get_existing_columns", return_value=[
                 {"name": "id_col", "type": "INTEGER"},
                 {"name": "nome_reale", "type": "VARCHAR"},
             ]):
            ok, msg = writer.validate()

        assert ok is False
        assert "bad_column" in msg
        assert "Colonne non trovate" in msg

    def test_append_valid_dest_columns_passes(self):
        """Validate passes when all dest_columns exist in the target table."""
        mappings = [
            make_col("id", dest="id_col"),
            make_col("nome", dest="nome_reale"),
        ]
        writer = make_writer(
            write_mode=WriteMode.APPEND,
            column_mappings=mappings,
        )
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "table_exists", return_value=True), \
             patch.object(writer, "get_existing_columns", return_value=[
                 {"name": "id_col", "type": "INTEGER"},
                 {"name": "nome_reale", "type": "VARCHAR"},
             ]):
            ok, msg = writer.validate()

        assert ok is True
        assert msg == ""

    def test_append_skip_columns_not_validated(self):
        """Columns with skip=True should not be checked against existing columns."""
        mappings = [
            make_col("id", dest="id_col"),
            make_col("extra", dest="nonexistent", skip=True),
        ]
        writer = make_writer(
            write_mode=WriteMode.APPEND,
            column_mappings=mappings,
        )
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "table_exists", return_value=True), \
             patch.object(writer, "get_existing_columns", return_value=[
                 {"name": "id_col", "type": "INTEGER"},
             ]):
            ok, msg = writer.validate()

        assert ok is True

    def test_validate_success_full_path(self):
        """Full happy path for CREATE with no table existing."""
        writer = make_writer(write_mode=WriteMode.CREATE)
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "table_exists", return_value=False), \
             patch.object(writer, "_check_create_permission", return_value=(True, "")):
            ok, msg = writer.validate()

        assert ok is True
        assert msg == ""


# =============================================================================
# _check_create_permission (lines 153-175)
# =============================================================================

class TestCheckCreatePermission:

    def test_success_creates_and_drops_test_table(self):
        """Lines 153-165: successful CREATE + DROP of temp table."""
        writer = make_writer()
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        ok, msg = writer._check_create_permission()
        assert ok is True
        assert msg == ""
        # conn.execute called at least twice (CREATE + DROP)
        assert mock_conn.execute.call_count >= 2
        mock_conn.commit.assert_called_once()

    def test_permission_error_returns_false(self):
        """Lines 167-172: 'permission' in error -> False."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("permission denied for schema public")
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        ok, msg = writer._check_create_permission()
        assert ok is False
        assert "Permessi insufficienti" in msg

    def test_privilege_error_returns_false(self):
        """Line 168: 'privilege' in error -> False."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("insufficient privilege")
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        ok, msg = writer._check_create_permission()
        assert ok is False
        assert "Permessi insufficienti" in msg

    def test_other_error_returns_true(self):
        """Lines 173-175: non-permission error -> True (warn but don't block)."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("syntax error near...")
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        ok, msg = writer._check_create_permission()
        assert ok is True
        assert msg == ""


# =============================================================================
# table_exists (lines 183-193)
# =============================================================================

class TestTableExists:

    def test_table_exists_true(self):
        """Lines 183-190: scalar returns >0."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_conn.execute.return_value = mock_result
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        assert writer.table_exists() is True

    def test_table_exists_false_zero(self):
        """scalar returns 0 -> False."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_conn.execute.return_value = mock_result
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        assert writer.table_exists() is False

    def test_table_exists_false_none(self):
        """scalar returns None -> False."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = None
        mock_conn.execute.return_value = mock_result
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        assert writer.table_exists() is False

    def test_table_exists_exception_returns_false(self):
        """Lines 191-193: exception -> False."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("DB down")
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        assert writer.table_exists() is False


# =============================================================================
# get_existing_columns (lines 197-209)
# =============================================================================

class TestGetExistingColumns:

    def test_returns_column_list(self):
        """Lines 197-206: normal result."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([
            ("id", "INTEGER"),
            ("nome", "VARCHAR(255)"),
        ]))
        mock_conn.execute.return_value = mock_result
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        cols = writer.get_existing_columns()
        assert len(cols) == 2
        assert cols[0] == {"name": "id", "type": "INTEGER"}
        assert cols[1] == {"name": "nome", "type": "VARCHAR(255)"}

    def test_exception_returns_empty_list(self):
        """Lines 207-209: exception -> []."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("error")
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        assert writer.get_existing_columns() == []
