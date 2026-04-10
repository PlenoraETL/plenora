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
# get_schemas (lines 213-220)
# =============================================================================

class TestGetSchemas:

    def test_returns_schema_list(self):
        """Lines 213-217."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([
            ("public",), ("custom_schema",),
        ]))
        mock_conn.execute.return_value = mock_result
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        schemas = writer.get_schemas()
        assert schemas == ["public", "custom_schema"]

    def test_exception_returns_empty_list(self):
        """Lines 218-220."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("error")
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        assert writer.get_schemas() == []


# =============================================================================
# get_tables (lines 224-231)
# =============================================================================

class TestGetTables:

    def test_returns_table_list(self):
        """Lines 224-228."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([
            ("users",), ("orders",),
        ]))
        mock_conn.execute.return_value = mock_result
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        tables = writer.get_tables("public")
        assert tables == ["users", "orders"]
        writer.ddl.query_list_tables.assert_called_once_with("public")

    def test_exception_returns_empty_list(self):
        """Lines 229-231."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("error")
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        assert writer.get_tables("public") == []


# =============================================================================
# create_table (lines 240-291)
# =============================================================================

class TestCreateTable:

    def test_create_table_basic(self):
        """Lines 240-287: basic table creation without spatial index."""
        writer = make_writer(create_spatial_index=False)
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        cols = [make_col("id", dest_type="int"), make_col("nome")]
        result = writer.create_table(cols)

        assert result is True
        mock_conn.commit.assert_called_once()

    def test_create_table_with_spatial_index(self):
        """Lines 254-265: spatial index creation."""
        writer = make_writer(create_spatial_index=True)
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        geom_col = make_col("geom", dest_type="geometry", is_geom=True, srid=4326)
        cols = [make_col("id", dest_type="int"), geom_col]

        result = writer.create_table(cols)

        assert result is True
        writer.ddl.generate_spatial_index.assert_called_once_with(
            "public", "test_table", "geom", 4326
        )

    def test_create_table_spatial_index_uses_config_srid_as_fallback(self):
        """Line 261: geom_col.srid is None, falls back to config.geometry_srid."""
        writer = make_writer(create_spatial_index=True)
        writer.config.geometry_srid = 32632
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        geom_col = make_col("geom", dest_type="geometry", is_geom=True, srid=None)
        cols = [geom_col]

        writer.create_table(cols)

        writer.ddl.generate_spatial_index.assert_called_once_with(
            "public", "test_table", "geom", 32632
        )

    def test_create_table_with_unique_key_postgres(self):
        """Lines 269-280: unique constraint for postgres."""
        writer = make_writer(create_spatial_index=False)
        writer.config.unique_key_column = "codice"
        writer.db_type = "postgres"
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        cols = [make_col("codice", dest_type="str")]
        result = writer.create_table(cols)

        assert result is True
        # Check that ALTER TABLE ... UNIQUE was executed via text() clause
        found_unique = False
        for call in mock_conn.execute.call_args_list:
            if call[0]:
                arg = call[0][0]
                if hasattr(arg, 'text') and "UNIQUE" in arg.text:
                    found_unique = True
                    break
        assert found_unique, "Expected ALTER TABLE ... UNIQUE to be executed"

    def test_create_table_unique_key_ignored_for_non_postgres(self):
        """Lines 270: unique_key_column only applies to postgres."""
        writer = make_writer(create_spatial_index=False)
        writer.config.unique_key_column = "codice"
        writer.db_type = "mysql"
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        cols = [make_col("codice")]
        writer.create_table(cols)

        # Only CREATE TABLE, no ALTER TABLE UNIQUE
        # execute called once for CREATE TABLE only
        assert mock_conn.execute.call_count == 1

    def test_create_table_with_pandas_dtypes(self):
        """Lines 240-241: pandas_dtypes passed to generate_create_table."""
        writer = make_writer(create_spatial_index=False)
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        cols = [make_col("id")]
        dtypes = {"id": "int64"}
        writer.create_table(cols, pandas_dtypes=dtypes)

        writer.ddl.generate_create_table.assert_called_once_with(
            "public", "test_table", cols, dtypes,
        )

    def test_create_table_pandas_dtypes_none_becomes_empty_dict(self):
        """Line 240-241: pandas_dtypes=None -> {}."""
        writer = make_writer(create_spatial_index=False)
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        cols = [make_col("id")]
        writer.create_table(cols, pandas_dtypes=None)

        writer.ddl.generate_create_table.assert_called_once_with(
            "public", "test_table", cols, {},
        )

    def test_create_table_exception_raised(self):
        """Lines 289-291: exception is logged and re-raised."""
        writer = make_writer(create_spatial_index=False)
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("CREATE failed")
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(RuntimeError, match="CREATE failed"):
            writer.create_table([make_col("id")])


# =============================================================================
# drop_table (lines 295-306)
# =============================================================================

class TestDropTable:

    def test_drop_table_success(self):
        """Lines 295-303."""
        writer = make_writer()
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        result = writer.drop_table()

        assert result is True
        mock_conn.commit.assert_called_once()

    def test_drop_table_exception_returns_false(self):
        """Lines 304-306."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("DROP failed")
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        assert writer.drop_table() is False


# =============================================================================
# truncate_table (lines 310-321)
# =============================================================================

class TestTruncateTable:

    def test_truncate_success(self):
        """Lines 310-317."""
        writer = make_writer()
        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        result = writer.truncate_table()

        assert result is True
        mock_conn.commit.assert_called_once()

    def test_truncate_exception_returns_false(self):
        """Lines 319-321."""
        writer = make_writer()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("TRUNCATE failed")
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        assert writer.truncate_table() is False


# =============================================================================
# check_conflicts (lines 333-334)
# =============================================================================

class TestCheckConflicts:

    def test_check_conflicts_delegates_to_conflict_checker(self):
        """Lines 333-334: delegates to ConflictChecker.check()."""
        writer = make_writer()
        df = pd.DataFrame({"id": [1, 2, 3]})

        with patch("app.core.carica.database_writer.ConflictChecker") as mock_cls:
            mock_checker = MagicMock()
            mock_report = ConflictReport(has_conflicts=False)
            mock_checker.check.return_value = mock_report
            mock_cls.return_value = mock_checker

            result = writer.check_conflicts(df, ["id"])

        mock_cls.assert_called_once_with(writer.engine, writer.db_type)
        mock_checker.check.assert_called_once_with(
            df, "public", "test_table", ["id"]
        )
        assert result is mock_report


# =============================================================================
# write() — APPEND conflict SKIP all filtered (lines 427-430)
# =============================================================================

class TestWriteAppendSkipAllFiltered:

    def test_append_skip_all_rows_conflicting_returns_success(self):
        """Lines 427-430: all rows skipped, prepared_df empty -> SUCCESS."""
        writer = make_writer(
            write_mode=WriteMode.APPEND,
            match_columns=["id"],
            conflict_policy=ConflictPolicy.SKIP,
        )
        df = pd.DataFrame({"id": [1], "nome": ["a"]})

        detail = ConflictDetail(row_index=0, match_values={"id": 1})
        conflict_report = ConflictReport(
            has_conflicts=True,
            total_conflicts=1,
            details=[detail],
        )

        with patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "check_conflicts", return_value=conflict_report):
            result = writer.write(df)

        assert result.status == WriteStatus.SUCCESS
        assert result.rows_skipped == 1

    def test_append_force_policy_inserts_all(self):
        """Lines 432-437: FORCE policy ignores conflicts and inserts all."""
        writer = make_writer(
            write_mode=WriteMode.APPEND,
            match_columns=["id"],
            conflict_policy=ConflictPolicy.FORCE,
        )
        df = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})

        conflict_report = ConflictReport(
            has_conflicts=True,
            total_conflicts=1,
            details=[ConflictDetail(row_index=0)],
        )

        with patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "check_conflicts", return_value=conflict_report), \
             patch.object(writer, "_batch_insert") as mock_insert:
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=2, rows_written=2,
                table_name="public.test_table", write_mode="append",
                status=WriteStatus.SUCCESS,
            )
            result = writer.write(df)

        # All rows passed to _batch_insert (not filtered)
        called_df = mock_insert.call_args[0][0]
        assert len(called_df) == 2
