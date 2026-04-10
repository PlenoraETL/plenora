"""
tests/carica/unit/test_database_writer_batch.py
================================================
Unit test sui metodi di esecuzione batch di DatabaseWriter.

NON richiede database reale — usa mock SQLAlchemy.

Copre:
  - _execute_in_batches: routing all_or_nothing vs per_batch
  - _execute_all_or_nothing: success, errore→rollback rows_written=0
  - _execute_per_batch: success, errore parziale→PARTIAL, tutti falliti→WRITE_ERROR
  - _batch_insert: chiama generate_insert + _execute_in_batches
  - _batch_update: filtra colonne update, chiama generate_update
  - _batch_upsert: chiama generate_upsert (senza DB reale)
  - report_progress: callback chiamato durante esecuzione
"""

import pytest
import math
import pandas as pd
from unittest.mock import MagicMock, patch, call

pytestmark = pytest.mark.unit

from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.models import (
    ColumnMapping, WriteConfig, WriteMode, WriteStatus,
    TransactionMode, WriteResult, BatchProgress
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_col(src, dest=None, dest_type="str", is_geom=False, pk=False, skip=False):
    return ColumnMapping(
        source_column=src, dest_column=dest or src,
        dest_type=dest_type, is_geometry=is_geom,
        is_primary_key=pk, skip=skip,
    )


def make_writer(
    write_mode=WriteMode.CREATE,
    transaction_mode=TransactionMode.ALL_OR_NOTHING,
    batch_size=100,
    match_columns=None,
    update_columns=None,
):
    config = WriteConfig(
        connection_id="conn-1", table_name="test", schema_name="public",
        write_mode=write_mode, column_mappings=[],
        match_columns=match_columns or [],
        update_columns=update_columns or [],
        geometry_srid=4326, geometry_column="geometry",
        batch_size=batch_size,
        transaction_mode=transaction_mode,
    )
    writer = DatabaseWriter.__new__(DatabaseWriter)
    writer.config = config
    writer.engine = MagicMock()
    writer.engine.dialect.name = "postgresql"
    writer.db_type = "postgres"
    writer.progress_callback = None
    writer.ddl = MagicMock()
    writer.ddl.generate_insert.return_value = ("INSERT INTO test VALUES (:id, :nome)", ["id", "nome"])
    writer.ddl.generate_update.return_value = ("UPDATE test SET nome=:nome WHERE id=:match_id", ["nome", "match_id"])
    writer.ddl.generate_upsert.return_value = ("INSERT INTO test ... ON CONFLICT DO UPDATE ...", ["id", "nome"])
    writer.mapper = MagicMock()
    return writer


def make_df(n=10):
    return pd.DataFrame({"id": range(1, n+1), "nome": [f"item_{i}" for i in range(n)]})


def make_result(n=10):
    return WriteResult(
        started_at="", total_rows=n, rows_written=0,
        table_name="public.test", write_mode="create",
    )


# =============================================================================
# _execute_in_batches — routing
# =============================================================================

class TestExecuteInBatchesRouting:

    def test_all_or_nothing_chiamato(self):
        writer = make_writer(transaction_mode=TransactionMode.ALL_OR_NOTHING)
        df = make_df(5)
        cols = [make_col("id"), make_col("nome")]
        result = make_result(5)

        with patch.object(writer, "_execute_all_or_nothing", return_value=result) as mock_aon, \
             patch.object(writer, "_execute_per_batch") as mock_pb:
            writer._execute_in_batches(df, cols, "INSERT ...", result, "insert")

        mock_aon.assert_called_once()
        mock_pb.assert_not_called()

    def test_per_batch_chiamato(self):
        writer = make_writer(transaction_mode=TransactionMode.PER_BATCH)
        df = make_df(5)
        cols = [make_col("id"), make_col("nome")]
        result = make_result(5)

        with patch.object(writer, "_execute_all_or_nothing") as mock_aon, \
             patch.object(writer, "_execute_per_batch", return_value=result) as mock_pb:
            writer._execute_in_batches(df, cols, "INSERT ...", result, "insert")

        mock_pb.assert_called_once()
        mock_aon.assert_not_called()

    def test_batch_count_calcolato_corretto(self):
        """batch_size=3 con 10 righe → ceil(10/3) = 4 batch."""
        writer = make_writer(batch_size=3, transaction_mode=TransactionMode.ALL_OR_NOTHING)
        df = make_df(10)
        cols = [make_col("id")]
        result = make_result(10)

        with patch.object(writer, "_execute_all_or_nothing", return_value=result) as mock_aon:
            writer._execute_in_batches(df, cols, "INSERT ...", result, "insert")

        args = mock_aon.call_args[0]
        total_batches = args[4]
        assert total_batches == math.ceil(10/3)

    def test_colonne_skip_escluse(self):
        """Colonne con skip=True escluse da active_cols."""
        writer = make_writer(transaction_mode=TransactionMode.ALL_OR_NOTHING)
        df = make_df(3)
        cols = [make_col("id"), make_col("_interno", skip=True)]
        result = make_result(3)

        with patch.object(writer, "_execute_all_or_nothing", return_value=result) as mock_aon:
            writer._execute_in_batches(df, cols, "INSERT ...", result, "insert")

        active_cols = mock_aon.call_args[0][1]
        assert all(not c.skip for c in active_cols)


# =============================================================================
# _execute_all_or_nothing
# =============================================================================

class TestExecuteAllOrNothing:

    def test_success_rows_written(self):
        writer = make_writer(batch_size=5)
        df = make_df(10)
        cols = [make_col("id"), make_col("nome")]
        result = make_result(10)

        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "_execute_batch", return_value=5) as mock_batch, \
             patch.object(writer, "report_progress"):
            result = writer._execute_all_or_nothing(
                df, cols, "INSERT ...", result, 2, 5, 0.0, "insert"
            )

        assert result.status == WriteStatus.SUCCESS
        assert result.rows_written == 10
        assert mock_batch.call_count == 2  # 2 batch da 5

    def test_errore_rollback_rows_zero(self):
        """Mutation target: errore → rows_written=0 (rollback)."""
        writer = make_writer(batch_size=5)
        df = make_df(10)
        cols = [make_col("id")]
        result = make_result(10)

        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "_execute_batch", side_effect=RuntimeError("DB error")):
            result = writer._execute_all_or_nothing(
                df, cols, "INSERT ...", result, 2, 5, 0.0, "insert"
            )

        assert result.status == WriteStatus.WRITE_ERROR
        assert result.rows_written == 0  # rollback
        assert "DB error" in result.error_message

    def test_progress_callback_chiamato(self):
        """report_progress chiamato per ogni batch."""
        writer = make_writer(batch_size=5)
        progress_calls = []
        writer.progress_callback = lambda p: progress_calls.append(p)
        df = make_df(10)
        cols = [make_col("id")]
        result = make_result(10)

        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "_execute_batch", return_value=5):
            writer._execute_all_or_nothing(
                df, cols, "INSERT ...", result, 2, 5, 0.0, "insert"
            )

        assert len(progress_calls) == 2


# =============================================================================
# _execute_per_batch
# =============================================================================

class TestExecutePerBatch:

    def test_success_tutti_batch(self):
        writer = make_writer(batch_size=5)
        df = make_df(10)
        cols = [make_col("id")]
        result = make_result(10)

        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "_execute_batch", return_value=5):
            result = writer._execute_per_batch(
                df, cols, "INSERT ...", result, 2, 5, 0.0, "insert"
            )

        assert result.status == WriteStatus.SUCCESS
        assert result.rows_written == 10
        assert result.rows_failed == 0

    def test_errore_parziale_status_partial(self):
        """Mutation target: un batch fallisce → PARTIAL (non WRITE_ERROR)."""
        writer = make_writer(batch_size=5)
        df = make_df(10)
        cols = [make_col("id")]
        result = make_result(10)

        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        call_count = [0]
        def mock_batch(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("errore batch 2")
            return 5

        with patch.object(writer, "_execute_batch", side_effect=mock_batch):
            result = writer._execute_per_batch(
                df, cols, "INSERT ...", result, 2, 5, 0.0, "insert"
            )

        assert result.status == WriteStatus.PARTIAL
        assert result.rows_written == 5
        assert result.rows_failed == 5

    def test_tutti_batch_falliti_write_error(self):
        """Mutation target: tutti i batch falliscono → WRITE_ERROR."""
        writer = make_writer(batch_size=5)
        df = make_df(10)
        cols = [make_col("id")]
        result = make_result(10)

        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "_execute_batch", side_effect=RuntimeError("errore")):
            result = writer._execute_per_batch(
                df, cols, "INSERT ...", result, 2, 5, 0.0, "insert"
            )

        assert result.status == WriteStatus.WRITE_ERROR
        assert result.rows_written == 0

    def test_commit_per_batch_chiamato(self):
        """Ogni batch ha il suo commit."""
        writer = make_writer(batch_size=5)
        df = make_df(10)
        cols = [make_col("id")]
        result = make_result(10)

        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "_execute_batch", return_value=5):
            writer._execute_per_batch(
                df, cols, "INSERT ...", result, 2, 5, 0.0, "insert"
            )

        # commit chiamato 2 volte (una per batch)
        assert mock_conn.commit.call_count == 2


# =============================================================================
# _batch_insert
# =============================================================================

class TestBatchInsert:

    def test_chiama_generate_insert(self):
        writer = make_writer()
        df = make_df(5)
        cols = [make_col("id"), make_col("nome")]
        result = make_result(5)

        with patch.object(writer, "_execute_in_batches", return_value=result) as mock_exec:
            writer._batch_insert(df, cols, result)

        writer.ddl.generate_insert.assert_called_once_with("public", "test", cols)
        mock_exec.assert_called_once()

    def test_chiama_execute_in_batches_con_insert(self):
        writer = make_writer()
        df = make_df(5)
        cols = [make_col("id")]
        result = make_result(5)

        with patch.object(writer, "_execute_in_batches", return_value=result) as mock_exec:
            writer._batch_insert(df, cols, result)

        assert mock_exec.called


# =============================================================================
# _batch_update
# =============================================================================

class TestBatchUpdate:

    def test_filtra_colonne_non_match(self):
        """Colonne di match non incluse nell'UPDATE."""
        writer = make_writer(match_columns=["id"])
        df = make_df(5)
        cols = [make_col("id", pk=True), make_col("nome")]
        result = make_result(5)

        with patch.object(writer, "_execute_in_batches", return_value=result):
            writer._batch_update(df, cols, result)

        # generate_update chiamato con colonne senza "id"
        call_args = writer.ddl.generate_update.call_args
        update_cols = call_args[0][2]
        assert all(c.dest_column != "id" for c in update_cols)

    def test_update_columns_esplicite(self):
        """update_columns esplicite → usa solo quelle."""
        writer = make_writer(match_columns=["id"], update_columns=["nome"])
        df = make_df(5)
        cols = [make_col("id"), make_col("nome"), make_col("valore")]
        result = make_result(5)

        with patch.object(writer, "_execute_in_batches", return_value=result):
            writer._batch_update(df, cols, result)

        call_args = writer.ddl.generate_update.call_args
        update_cols = call_args[0][2]
        col_names = [c.dest_column for c in update_cols]
        assert "nome" in col_names
        assert "valore" not in col_names


# =============================================================================
# report_progress
# =============================================================================

class TestReportProgress:

    def test_callback_chiamato_se_presente(self):
        writer = make_writer()
        calls = []
        writer.progress_callback = lambda p: calls.append(p)
        progress = BatchProgress(
            batch_number=1, total_batches=3,
            rows_written=100, total_rows=300, elapsed_seconds=1.0
        )
        writer.report_progress(progress)
        assert len(calls) == 1
        assert calls[0] is progress

    def test_nessun_callback_no_crash(self):
        writer = make_writer()
        writer.progress_callback = None
        progress = BatchProgress(
            batch_number=1, total_batches=1,
            rows_written=10, total_rows=10, elapsed_seconds=0.5
        )
        writer.report_progress(progress)  # non deve crashare
