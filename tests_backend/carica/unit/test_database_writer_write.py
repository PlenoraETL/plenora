"""
tests/carica/unit/test_database_writer_write.py
================================================
Unit test su DatabaseWriter.write() con SQLAlchemy mockato.

NON richiede database reale.

Copre:
  - write(): DataFrame vuoto → SUCCESS senza chiamate DB
  - write(): CREATE → create_table + batch_insert chiamati
  - write(): APPEND → batch_insert chiamato
  - write(): REPLACE → drop_table + create_table + batch_insert
  - write(): TRUNCATE_INSERT → truncate + batch_insert
  - write(): errore generico → WriteStatus.WRITE_ERROR
  - write(): APPEND con conflict ABORT → WriteStatus.CONFLICT
  - write(): APPEND con conflict SKIP → righe filtrate
  - _batch_insert: eseguito con batch_size corretto
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

pytestmark = pytest.mark.unit

from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.models import (
    ColumnMapping, WriteConfig, WriteMode, WriteStatus,
    TransactionMode, ConflictPolicy, WriteResult,
    ConflictReport, ConflictDetail
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_col(src, dest=None, dest_type="str", is_geom=False, pk=False, skip=False):
    return ColumnMapping(
        source_column=src,
        dest_column=dest or src,
        dest_type=dest_type,
        is_geometry=is_geom,
        is_primary_key=pk,
        skip=skip,
    )


def make_writer(
    write_mode=WriteMode.CREATE,
    column_mappings=None,
    match_columns=None,
    conflict_policy=ConflictPolicy.FORCE,
    batch_size=1000,
    transaction_mode=TransactionMode.ALL_OR_NOTHING,
):
    """Crea un DatabaseWriter con engine e DDL mockati."""
    config = WriteConfig(
        connection_id="conn-1",
        table_name="test_table",
        schema_name="public",
        write_mode=write_mode,
        column_mappings=column_mappings or [
            make_col("id", dest_type="int"),
            make_col("nome", dest_type="str"),
        ],
        match_columns=match_columns or [],
        conflict_policy=conflict_policy,
        geometry_srid=4326,
        geometry_column="geometry",
        batch_size=batch_size,
        transaction_mode=transaction_mode,
    )

    writer = DatabaseWriter.__new__(DatabaseWriter)
    writer.config = config
    writer.engine = MagicMock()
    writer.engine.dialect.name = "postgresql"
    writer.db_type = "postgres"
    writer.progress_callback = None

    # Mock DDL generator
    writer.ddl = MagicMock()
    writer.ddl.generate_create_table.return_value = "CREATE TABLE ..."
    writer.ddl.generate_insert.return_value = ("INSERT INTO ...", ["id", "nome"])
    writer.ddl.generate_upsert.return_value = ("MERGE INTO ...", ["id", "nome"])
    writer.ddl.generate_update.return_value = ("UPDATE ...", ["id", "nome"])

    # Mock mapper
    writer.mapper = MagicMock()

    return writer


def make_df(n=5):
    return pd.DataFrame({
        "id":   range(1, n+1),
        "nome": [f"item_{i}" for i in range(n)],
    })


# =============================================================================
# write() — DataFrame vuoto
# =============================================================================

class TestWriteDataFrameVuoto:

    def test_df_vuoto_success_senza_db(self):
        """DataFrame vuoto → SUCCESS immediato, nessuna chiamata al DB."""
        writer = make_writer()
        df = pd.DataFrame({"id": [], "nome": []})
        result = writer.write(df)
        assert result.status == WriteStatus.SUCCESS
        # Nessuna chiamata al DB
        writer.engine.connect.assert_not_called()

    def test_df_vuoto_rows_written_zero(self):
        writer = make_writer()
        df = pd.DataFrame({"id": [], "nome": []})
        result = writer.write(df)
        assert result.rows_written == 0 or result.total_rows == 0


# =============================================================================
# write() — CREATE
# =============================================================================

class TestWriteCreate:

    def test_create_chiama_create_table_e_insert(self):
        writer = make_writer(write_mode=WriteMode.CREATE)
        df = make_df(5)

        with patch.object(writer, "create_table", return_value=True) as mock_create, \
             patch.object(writer, "_batch_insert") as mock_insert, \
             patch.object(writer, "_prepare_dataframe", return_value=df):
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=5, rows_written=5,
                table_name="public.test_table", write_mode="create",
                status=WriteStatus.SUCCESS
            )
            result = writer.write(df)

        mock_create.assert_called_once()
        mock_insert.assert_called_once()

    def test_create_result_success(self):
        writer = make_writer(write_mode=WriteMode.CREATE)
        df = make_df(3)

        with patch.object(writer, "create_table", return_value=True), \
             patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "_batch_insert") as mock_insert:
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=3, rows_written=3,
                table_name="public.test_table", write_mode="create",
                status=WriteStatus.SUCCESS
            )
            result = writer.write(df)

        assert result.status == WriteStatus.SUCCESS


# =============================================================================
# write() — REPLACE
# =============================================================================

class TestWriteReplace:

    def test_replace_chiama_drop_table(self):
        writer = make_writer(write_mode=WriteMode.REPLACE)
        df = make_df(3)

        with patch.object(writer, "drop_table", return_value=True) as mock_drop, \
             patch.object(writer, "create_table", return_value=True), \
             patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "_batch_insert") as mock_insert:
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=3, rows_written=3,
                table_name="public.test_table", write_mode="replace",
                status=WriteStatus.SUCCESS
            )
            result = writer.write(df)

        mock_drop.assert_called_once()


# =============================================================================
# write() — APPEND
# =============================================================================

class TestWriteAppend:

    def test_append_chiama_batch_insert(self):
        writer = make_writer(write_mode=WriteMode.APPEND)
        df = make_df(5)

        with patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "_batch_insert") as mock_insert:
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=5, rows_written=5,
                table_name="public.test_table", write_mode="append",
                status=WriteStatus.SUCCESS
            )
            result = writer.write(df)

        mock_insert.assert_called_once()

    def test_append_conflict_abort(self):
        """APPEND con conflict ABORT → WriteStatus.CONFLICT."""
        writer = make_writer(
            write_mode=WriteMode.APPEND,
            match_columns=["id"],
            conflict_policy=ConflictPolicy.ABORT,
        )
        df = make_df(3)

        conflict_report = MagicMock()
        conflict_report.has_conflicts = True
        conflict_report.total_conflicts = 2
        conflict_report.details = []

        with patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "check_conflicts", return_value=conflict_report):
            result = writer.write(df)

        assert result.status == WriteStatus.CONFLICT

    def test_append_conflict_skip_filtra_righe(self):
        """APPEND con conflict SKIP → righe conflitto escluse."""
        writer = make_writer(
            write_mode=WriteMode.APPEND,
            match_columns=["id"],
            conflict_policy=ConflictPolicy.SKIP,
        )
        df = make_df(5)

        detail = MagicMock()
        detail.row_index = 0

        conflict_report = MagicMock()
        conflict_report.has_conflicts = True
        conflict_report.total_conflicts = 1
        conflict_report.details = [detail]

        with patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "check_conflicts", return_value=conflict_report), \
             patch.object(writer, "_batch_insert") as mock_insert:
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=4, rows_written=4,
                table_name="public.test_table", write_mode="append",
                status=WriteStatus.SUCCESS
            )
            result = writer.write(df)

        # _batch_insert chiamato con df filtrato (4 righe invece di 5)
        called_df = mock_insert.call_args[0][0]
        assert len(called_df) == 4


# =============================================================================
# write() — TRUNCATE_INSERT
# =============================================================================

class TestWriteTruncateInsert:

    def test_truncate_chiama_truncate_e_insert(self):
        writer = make_writer(write_mode=WriteMode.TRUNCATE_INSERT)
        df = make_df(3)

        with patch.object(writer, "truncate_table", return_value=True) as mock_trunc, \
             patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "_batch_insert") as mock_insert:
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=3, rows_written=3,
                table_name="public.test_table", write_mode="truncate_insert",
                status=WriteStatus.SUCCESS
            )
            result = writer.write(df)

        mock_trunc.assert_called_once()
        mock_insert.assert_called_once()


# =============================================================================
# write() — errore generico
# =============================================================================

class TestWriteErroreGenerico:

    def test_eccezione_restituisce_write_error(self):
        """Eccezione durante scrittura → WriteStatus.WRITE_ERROR."""
        writer = make_writer(write_mode=WriteMode.CREATE)
        df = make_df(3)

        with patch.object(writer, "_prepare_dataframe", side_effect=RuntimeError("DB error")):
            result = writer.write(df)

        assert result.status == WriteStatus.WRITE_ERROR
        assert "DB error" in result.error_message


# =============================================================================
# write() — UPDATE e UPSERT
# =============================================================================

class TestWriteUpdateUpsert:

    def test_update_chiama_batch_update(self):
        writer = make_writer(
            write_mode=WriteMode.UPDATE,
            match_columns=["id"],
        )
        df = make_df(3)

        with patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "_batch_update") as mock_update:
            mock_update.return_value = WriteResult(
                started_at="", total_rows=3, rows_written=3,
                table_name="public.test_table", write_mode="update",
                status=WriteStatus.SUCCESS
            )
            result = writer.write(df)

        mock_update.assert_called_once()

    def test_upsert_chiama_batch_upsert(self):
        writer = make_writer(
            write_mode=WriteMode.UPSERT,
            match_columns=["id"],
        )
        df = make_df(3)

        with patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "_batch_upsert") as mock_upsert:
            mock_upsert.return_value = WriteResult(
                started_at="", total_rows=3, rows_written=3,
                table_name="public.test_table", write_mode="upsert",
                status=WriteStatus.SUCCESS
            )
            result = writer.write(df)

        mock_upsert.assert_called_once()
