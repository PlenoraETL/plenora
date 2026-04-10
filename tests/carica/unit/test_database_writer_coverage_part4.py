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
# write() — CREATE with if_table_exists="replace" triggers drop (line 383)
# =============================================================================

class TestWriteCreateWithReplace:

    def test_create_if_table_exists_replace_drops(self):
        """Line 381-384: CREATE + if_table_exists='replace' -> drop_table called."""
        writer = make_writer(
            write_mode=WriteMode.CREATE,
            if_table_exists="replace",
        )
        # BUG: Line 381-384 — when write_mode is CREATE and if_table_exists is "replace",
        # the code calls drop_table() unconditionally without checking if the table
        # actually exists first. This means it will attempt to drop a table that
        # may not exist (though DROP IF EXISTS usually handles this).
        df = pd.DataFrame({"id": [1], "nome": ["a"]})

        with patch.object(writer, "drop_table") as mock_drop, \
             patch.object(writer, "create_table", return_value=True), \
             patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "_batch_insert") as mock_insert:
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=1, rows_written=1,
                table_name="public.test_table", write_mode="create",
                status=WriteStatus.SUCCESS,
            )
            result = writer.write(df)

        mock_drop.assert_called_once()

    def test_replace_mode_always_drops(self):
        """Line 381: REPLACE -> drop_table always called."""
        writer = make_writer(write_mode=WriteMode.REPLACE)
        df = pd.DataFrame({"id": [1], "nome": ["a"]})

        with patch.object(writer, "drop_table") as mock_drop, \
             patch.object(writer, "create_table", return_value=True), \
             patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "_batch_insert") as mock_insert:
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=1, rows_written=1,
                table_name="public.test_table", write_mode="replace",
                status=WriteStatus.SUCCESS,
            )
            writer.write(df)

        mock_drop.assert_called_once()


# =============================================================================
# write() — real_dtypes construction (lines 388-394)
# =============================================================================

class TestWriteRealDtypes:

    def test_create_passes_real_dtypes_to_create_table(self):
        """Lines 388-394: real dtypes from prepared DataFrame passed."""
        cols = [make_col("id", dest_type="int"), make_col("nome")]
        writer = make_writer(write_mode=WriteMode.CREATE, column_mappings=cols)
        df = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})

        with patch.object(writer, "drop_table"), \
             patch.object(writer, "create_table", return_value=True) as mock_ct, \
             patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "_batch_insert") as mock_insert:
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=2, rows_written=2,
                table_name="public.test_table", write_mode="create",
                status=WriteStatus.SUCCESS,
            )
            writer.write(df)

        # create_table called with pandas_dtypes dict
        call_kwargs = mock_ct.call_args
        pandas_dtypes = call_kwargs[1]["pandas_dtypes"]
        assert "id" in pandas_dtypes
        assert "nome" in pandas_dtypes
        assert pandas_dtypes["id"] == "int64"


# =============================================================================
# Validate: column mapping checks with empty existing_col_names (line 136)
# =============================================================================

class TestValidateEmptyExistingCols:

    def test_append_with_empty_existing_columns_passes(self):
        """Line 136: if existing_col_names is empty set, skip validation."""
        mappings = [make_col("id", dest="id_col")]
        writer = make_writer(
            write_mode=WriteMode.APPEND,
            column_mappings=mappings,
        )
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "table_exists", return_value=True), \
             patch.object(writer, "get_existing_columns", return_value=[]):
            ok, msg = writer.validate()

        assert ok is True


# =============================================================================
# Validate: REPLACE mode calls _check_create_permission (line 124)
# =============================================================================

class TestValidateReplacePermission:

    def test_replace_calls_check_create_permission(self):
        """Line 124: REPLACE mode checks CREATE permission."""
        writer = make_writer(write_mode=WriteMode.REPLACE)
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "table_exists", return_value=True), \
             patch.object(writer, "_check_create_permission", return_value=(True, "")) as mock_perm:
            ok, msg = writer.validate()

        mock_perm.assert_called_once()
        assert ok is True
