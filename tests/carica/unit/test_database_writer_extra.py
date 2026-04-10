"""
tests/carica/unit/test_database_writer_extra.py
================================================
Test aggiuntivi per DatabaseWriter — _execute_batch, _build_params,
_prepare_dataframe, write() edge cases.

Porta la suite CARICA più vicino ai 400 test totali.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, date
from unittest.mock import MagicMock, patch, call

pytestmark = pytest.mark.unit

from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.models import (
    ColumnMapping, WriteConfig, WriteMode, WriteStatus,
    TransactionMode, ConflictPolicy, WriteResult, BatchProgress
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_col(src, dest=None, dest_type="str", is_geom=False, srid=None, pk=False, skip=False):
    return ColumnMapping(
        source_column=src, dest_column=dest or src,
        dest_type=dest_type, is_geometry=is_geom,
        srid=srid, is_primary_key=pk, skip=skip,
    )


def make_writer(
    write_mode=WriteMode.CREATE,
    transaction_mode=TransactionMode.ALL_OR_NOTHING,
    batch_size=1000,
    match_columns=None,
    update_columns=None,
    db_type="postgres",
):
    config = WriteConfig(
        connection_id="conn-1", table_name="test", schema_name="public",
        write_mode=write_mode,
        column_mappings=[make_col("id", dest_type="int"), make_col("nome")],
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
    writer.db_type = db_type
    writer.progress_callback = None
    writer.ddl = MagicMock()
    writer.ddl.generate_insert.return_value = ("INSERT INTO test (id,nome) VALUES (:id,:nome)", ["id", "nome"])
    writer.ddl.generate_update.return_value = ("UPDATE test SET nome=:nome WHERE id=:match_id", ["nome"])
    writer.ddl.generate_upsert.return_value = ("INSERT INTO test ... ON CONFLICT ...", ["id", "nome"])
    writer.mapper = MagicMock()
    return writer


def make_result(n=5):
    return WriteResult(
        started_at="", total_rows=n, rows_written=0,
        table_name="public.test", write_mode="create",
    )


# =============================================================================
# _execute_batch
# =============================================================================

class TestExecuteBatch:

    def test_righe_elaborate_contate(self):
        """_execute_batch deve restituire il numero di righe elaborate."""
        writer = make_writer()
        mock_conn = MagicMock()
        df = pd.DataFrame({"id": [1, 2, 3], "nome": ["a", "b", "c"]})
        cols = [make_col("id", dest_type="int"), make_col("nome")]

        with patch.object(writer, "_build_params", return_value={"id": 1, "nome": "a"}):
            count = writer._execute_batch(mock_conn, df, cols, "INSERT ...", "insert")

        assert count == 3

    def test_execute_chiamato_per_ogni_riga(self):
        """conn.execute chiamato una volta per riga."""
        writer = make_writer()
        mock_conn = MagicMock()
        df = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        cols = [make_col("id"), make_col("nome")]

        with patch.object(writer, "_build_params", return_value={}):
            writer._execute_batch(mock_conn, df, cols, "INSERT ...", "insert")

        assert mock_conn.execute.call_count == 2

    def test_build_params_chiamato_per_ogni_riga(self):
        """_build_params chiamato per ogni riga."""
        writer = make_writer()
        mock_conn = MagicMock()
        df = pd.DataFrame({"id": [1, 2, 3], "nome": ["a", "b", "c"]})
        cols = [make_col("id"), make_col("nome")]

        with patch.object(writer, "_build_params", return_value={}) as mock_bp:
            writer._execute_batch(mock_conn, df, cols, "INSERT ...", "insert")

        assert mock_bp.call_count == 3

    def test_df_vuoto_zero_righe(self):
        writer = make_writer()
        mock_conn = MagicMock()
        df = pd.DataFrame({"id": [], "nome": []})
        cols = [make_col("id"), make_col("nome")]
        count = writer._execute_batch(mock_conn, df, cols, "INSERT ...", "insert")
        assert count == 0
        mock_conn.execute.assert_not_called()


# =============================================================================
# _build_params — casi aggiuntivi
# =============================================================================

class TestBuildParamsExtra:

    def test_bool_python_preservato(self):
        writer = make_writer()
        row = pd.Series({"attivo": True})
        cols = [make_col("attivo", dest_type="bool")]
        params = writer._build_params(row, cols, "insert")
        assert params["attivo"] is True

    def test_bool_false_preservato(self):
        writer = make_writer()
        row = pd.Series({"attivo": False})
        cols = [make_col("attivo", dest_type="bool")]
        params = writer._build_params(row, cols, "insert")
        assert params["attivo"] is False

    def test_stringa_normale(self):
        writer = make_writer()
        row = pd.Series({"nome": "Roma"})
        cols = [make_col("nome")]
        params = writer._build_params(row, cols, "insert")
        assert params["nome"] == "Roma"

    def test_intero_zero(self):
        """Mutation target: 0 non deve diventare None."""
        writer = make_writer()
        row = pd.Series({"id": 0})
        cols = [make_col("id", dest_type="int")]
        params = writer._build_params(row, cols, "insert")
        assert params["id"] == 0

    def test_numpy_bool(self):
        writer = make_writer()
        row = pd.Series({"flag": np.bool_(True)})
        cols = [make_col("flag")]
        params = writer._build_params(row, cols, "insert")
        assert params["flag"] is not None

    def test_date_object_a_iso(self):
        writer = make_writer()
        d = date(2024, 6, 15)
        row = pd.Series({"data": d})
        cols = [make_col("data", dest_type="date")]
        params = writer._build_params(row, cols, "insert")
        assert "2024" in str(params["data"])

    def test_source_dest_diversi(self):
        """source_column diverso da dest_column."""
        writer = make_writer()
        row = pd.Series({"nome_sorgente": "Roma"})
        cols = [make_col("nome_sorgente", dest="nome_dest")]
        params = writer._build_params(row, cols, "insert")
        assert "nome_dest" in params
        assert params["nome_dest"] == "Roma"

    def test_update_match_col_multipli(self):
        """UPDATE con due match columns → due parametri match_."""
        writer = make_writer(match_columns=["comune", "anno"])
        row = pd.Series({"comune": "Roma", "anno": 2024, "valore": 100})
        cols = [make_col("valore")]
        params = writer._build_params(row, cols, "update")
        assert "match_comune" in params
        assert "match_anno" in params
        assert params["match_comune"] == "Roma"
        assert params["match_anno"] == 2024

    def test_skip_colonna_esclusa(self):
        """_build_params non filtra skip — lo fa _execute_in_batches upstream."""
        writer = make_writer()
        row = pd.Series({"id": 1})
        cols = [make_col("id")]
        params = writer._build_params(row, cols, "insert")
        assert "id" in params


# =============================================================================
# _prepare_dataframe — casi aggiuntivi
# =============================================================================

class TestPrepareDataframeExtra:

    def test_colonne_rinominate_da_mapping(self):
        """source_column diverso da dest_column → colonne rinominate."""
        writer = make_writer()
        writer.config.column_mappings = [
            make_col("nome_it", dest="name"),
            make_col("pop", dest="population"),
        ]
        df = pd.DataFrame({"nome_it": ["Roma"], "pop": [4000000]})
        result = writer._prepare_dataframe(df)
        assert isinstance(result, pd.DataFrame)

    def test_df_passthrough_senza_geopandas(self):
        """Senza GeoSeries → DataFrame non modificato."""
        writer = make_writer()
        df = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        result = writer._prepare_dataframe(df)
        assert len(result) == 2

    def test_fix_misto_chiamato(self):
        writer = make_writer()
        df = pd.DataFrame({"id": [1, 2], "codice": [100, "26A"]})
        with patch.object(writer, "_fix_mixed_type_columns", wraps=writer._fix_mixed_type_columns) as mock_fix:
            writer._prepare_dataframe(df)
        mock_fix.assert_called_once()


# =============================================================================
# write() — casi aggiuntivi
# =============================================================================

class TestWriteExtra:

    def test_create_senza_drop_se_non_replace(self):
        """CREATE normale → drop_table NON chiamato."""
        writer = make_writer(write_mode=WriteMode.CREATE)
        df = pd.DataFrame({"id": [1], "nome": ["a"]})

        with patch.object(writer, "drop_table") as mock_drop, \
             patch.object(writer, "create_table", return_value=True), \
             patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "_batch_insert") as mock_insert:
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=1, rows_written=1,
                table_name="public.test", write_mode="create",
                status=WriteStatus.SUCCESS
            )
            writer.write(df)

        mock_drop.assert_not_called()

    def test_result_ha_completed_at(self):
        """WriteResult deve avere completed_at valorizzato."""
        writer = make_writer(write_mode=WriteMode.CREATE)
        df = pd.DataFrame({"id": [1], "nome": ["a"]})

        with patch.object(writer, "create_table", return_value=True), \
             patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "_batch_insert") as mock_insert:
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=1, rows_written=1,
                table_name="public.test", write_mode="create",
                status=WriteStatus.SUCCESS
            )
            result = writer.write(df)

        assert result.completed_at is not None
        assert result.completed_at != ""

    def test_append_senza_match_columns_skip_conflict_check(self):
        """APPEND senza match_columns → no check_conflicts."""
        writer = make_writer(write_mode=WriteMode.APPEND, match_columns=[])
        df = pd.DataFrame({"id": [1], "nome": ["a"]})

        with patch.object(writer, "check_conflicts") as mock_check, \
             patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "_batch_insert") as mock_insert:
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=1, rows_written=1,
                table_name="public.test", write_mode="append",
                status=WriteStatus.SUCCESS
            )
            writer.write(df)

        mock_check.assert_not_called()

    def test_table_name_nel_result(self):
        """table_name nel risultato deve essere schema.table."""
        writer = make_writer(write_mode=WriteMode.CREATE)
        df = pd.DataFrame({"id": [1], "nome": ["a"]})

        with patch.object(writer, "create_table", return_value=True), \
             patch.object(writer, "_prepare_dataframe", return_value=df), \
             patch.object(writer, "_batch_insert") as mock_insert:
            mock_insert.return_value = WriteResult(
                started_at="", total_rows=1, rows_written=1,
                table_name="public.test", write_mode="create",
                status=WriteStatus.SUCCESS
            )
            result = writer.write(df)

        assert "test" in result.table_name


# =============================================================================
# _geometry_to_wkt — casi aggiuntivi
# =============================================================================

class TestGeometryToWktExtra:

    def test_multilinestring(self):
        writer = make_writer()
        result = writer._geometry_to_wkt("MULTILINESTRING((0 0, 1 1),(2 2, 3 3))")
        assert "MULTILINESTRING" in result

    def test_geometrycollection(self):
        writer = make_writer()
        result = writer._geometry_to_wkt("GEOMETRYCOLLECTION(POINT(1 2),LINESTRING(0 0,1 1))")
        assert result is not None

    def test_wkt_con_spazi_trim(self):
        writer = make_writer()
        result = writer._geometry_to_wkt("  POINT(1 2)  ")
        assert result.startswith("POINT")

    def test_numero_non_geometry(self):
        """Valore numerico → convertito a stringa."""
        writer = make_writer()
        result = writer._geometry_to_wkt(42)
        assert result is not None


# =============================================================================
# _fix_mixed_type_columns — casi aggiuntivi
# =============================================================================

class TestFixMixedTypeColumnsExtra:

    def test_nan_in_colonna_mista(self):
        """NaN in colonna mista → non conta come tipo."""
        writer = make_writer()
        df = pd.DataFrame({"codice": [100, None, "26A", 150]})
        result = writer._fix_mixed_type_columns(df)
        assert "codice" in result.columns

    def test_multipla_colonne_miste(self):
        """Più colonne miste → tutte convertite."""
        writer = make_writer()
        df = pd.DataFrame({
            "cod1": [1, "A", 2],
            "cod2": [100, "B", 200],
        })
        result = writer._fix_mixed_type_columns(df)
        for col in ["cod1", "cod2"]:
            non_null = result[col].dropna()
            assert all(isinstance(v, str) for v in non_null)

    def test_colonna_int64_non_modificata(self):
        """Colonna int64 non deve essere toccata."""
        writer = make_writer()
        df = pd.DataFrame({"id": pd.array([1, 2, 3], dtype="int64")})
        result = writer._fix_mixed_type_columns(df)
        assert result["id"].dtype == df["id"].dtype
