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
# _batch_upsert — postgres UNIQUE constraint check (lines 525-567)
# =============================================================================

class TestBatchUpsertPostgresConstraint:

    def test_upsert_postgres_constraint_exists_no_create(self):
        """Lines 525-541: constraint already exists -> no CREATE INDEX."""
        writer = make_writer(
            write_mode=WriteMode.UPSERT,
            match_columns=["id"],
            db_type="postgres",
        )

        mock_begin_conn = MagicMock()
        mock_scalar_result = MagicMock()
        mock_scalar_result.scalar.return_value = 1  # constraint exists
        mock_begin_conn.execute.return_value = mock_scalar_result
        writer.engine.begin.return_value.__enter__ = MagicMock(return_value=mock_begin_conn)
        writer.engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        df = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        cols = [make_col("id", pk=True), make_col("nome")]
        result = make_result(2)

        with patch.object(writer, "_execute_in_batches", return_value=result):
            writer._batch_upsert(df, cols, result)

        # execute called once for the check query only (no CREATE INDEX)
        assert mock_begin_conn.execute.call_count == 1

    def test_upsert_postgres_no_constraint_creates_index(self):
        """Lines 542-552: no constraint -> CREATE UNIQUE INDEX."""
        writer = make_writer(
            write_mode=WriteMode.UPSERT,
            match_columns=["id"],
            db_type="postgres",
        )

        mock_begin_conn = MagicMock()
        # First call: check returns 0 (no constraint)
        check_result = MagicMock()
        check_result.scalar.return_value = 0
        # Second call: CREATE INDEX succeeds
        mock_begin_conn.execute.side_effect = [check_result, MagicMock()]
        writer.engine.begin.return_value.__enter__ = MagicMock(return_value=mock_begin_conn)
        writer.engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        df = pd.DataFrame({"id": [1], "nome": ["a"]})
        cols = [make_col("id"), make_col("nome")]
        result = make_result(1)

        with patch.object(writer, "_execute_in_batches", return_value=result):
            writer._batch_upsert(df, cols, result)

        # Two execute calls: check + CREATE INDEX
        assert mock_begin_conn.execute.call_count == 2

    def test_upsert_postgres_create_index_fails_raises_valueerror(self):
        """Lines 554-558: CREATE INDEX fails -> ValueError."""
        writer = make_writer(
            write_mode=WriteMode.UPSERT,
            match_columns=["id"],
            db_type="postgres",
        )

        mock_begin_conn = MagicMock()
        check_result = MagicMock()
        check_result.scalar.return_value = 0
        mock_begin_conn.execute.side_effect = [
            check_result,
            RuntimeError("duplicate key values"),
        ]
        writer.engine.begin.return_value.__enter__ = MagicMock(return_value=mock_begin_conn)
        writer.engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        df = pd.DataFrame({"id": [1]})
        cols = [make_col("id")]
        result = make_result(1)

        with pytest.raises(ValueError, match="non ha un indice UNIQUE"):
            writer._batch_upsert(df, cols, result)

    def test_upsert_non_postgres_skips_constraint_check(self):
        """Lines 525: non-postgres db_type skips the whole constraint block."""
        writer = make_writer(
            write_mode=WriteMode.UPSERT,
            match_columns=["id"],
            db_type="mysql",
        )

        df = pd.DataFrame({"id": [1], "nome": ["a"]})
        cols = [make_col("id"), make_col("nome")]
        result = make_result(1)

        with patch.object(writer, "_execute_in_batches", return_value=result):
            writer._batch_upsert(df, cols, result)

        # engine.begin() should NOT have been called for mysql
        writer.engine.begin.assert_not_called()

    def test_upsert_postgres_multiple_match_columns(self):
        """Lines 527: iterates over each match column."""
        writer = make_writer(
            write_mode=WriteMode.UPSERT,
            match_columns=["comune", "anno"],
            db_type="postgres",
        )

        mock_begin_conn = MagicMock()
        # Both columns have constraints
        check_result = MagicMock()
        check_result.scalar.return_value = 1
        mock_begin_conn.execute.return_value = check_result
        writer.engine.begin.return_value.__enter__ = MagicMock(return_value=mock_begin_conn)
        writer.engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        df = pd.DataFrame({"comune": ["Roma"], "anno": [2024]})
        cols = [make_col("comune"), make_col("anno")]
        result = make_result(1)

        with patch.object(writer, "_execute_in_batches", return_value=result):
            writer._batch_upsert(df, cols, result)

        # Two check queries (one per match column)
        assert mock_begin_conn.execute.call_count == 2

    def test_upsert_no_match_columns_skips_constraint_check(self):
        """Line 525: empty match_columns -> skip constraint block."""
        writer = make_writer(
            write_mode=WriteMode.UPSERT,
            match_columns=[],
            db_type="postgres",
        )

        df = pd.DataFrame({"id": [1]})
        cols = [make_col("id")]
        result = make_result(1)

        with patch.object(writer, "_execute_in_batches", return_value=result):
            writer._batch_upsert(df, cols, result)

        writer.engine.begin.assert_not_called()


# =============================================================================
# _execute_all_or_nothing with oracle (line 633)
# =============================================================================

class TestAllOrNothingOracle:

    def test_oracle_setup_called(self):
        """Line 633: oracle -> _oracle_setup called."""
        writer = make_writer(db_type="oracle")
        writer.config.batch_size = 5
        df = pd.DataFrame({"id": [1, 2, 3]})
        cols = [make_col("id")]
        result = make_result(3)

        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "_execute_batch", return_value=3), \
             patch.object(writer, "_oracle_setup") as mock_oracle, \
             patch.object(writer, "report_progress"):
            writer._execute_all_or_nothing(
                df, cols, "INSERT ...", result, 1, 5, 0.0, "insert"
            )

        mock_oracle.assert_called_once_with(mock_conn)


# =============================================================================
# _execute_per_batch with oracle (line 685)
# =============================================================================

class TestPerBatchOracle:

    def test_oracle_setup_called_per_batch(self):
        """Line 685: oracle -> _oracle_setup called in per_batch mode."""
        writer = make_writer(db_type="oracle")
        df = pd.DataFrame({"id": [1, 2]})
        cols = [make_col("id")]
        result = make_result(2)

        mock_conn = MagicMock()
        writer.engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        writer.engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(writer, "_execute_batch", return_value=2), \
             patch.object(writer, "_oracle_setup") as mock_oracle, \
             patch.object(writer, "report_progress"):
            writer._execute_per_batch(
                df, cols, "INSERT ...", result, 1, 5, 0.0, "insert"
            )

        mock_oracle.assert_called_once_with(mock_conn)


# =============================================================================
# _build_params — update match with NaN and numpy (lines 794, 796)
# =============================================================================

class TestBuildParamsMatchEdgeCases:

    def test_update_match_nan_becomes_none(self):
        """Line 794: NaN match value -> None."""
        writer = make_writer(match_columns=["id"])
        row = pd.Series({"id": float("nan"), "nome": "test"})
        cols = [make_col("nome")]
        params = writer._build_params(row, cols, "update")
        assert params["match_id"] is None

    def test_update_match_numpy_scalar_item(self):
        """Line 796: numpy match value -> .item()."""
        writer = make_writer(match_columns=["id"])
        row = pd.Series({"id": np.int64(42), "nome": "test"})
        cols = [make_col("nome")]
        params = writer._build_params(row, cols, "update")
        assert params["match_id"] == 42
        assert isinstance(params["match_id"], int)

    def test_update_match_string_value(self):
        """Line 793: string match value is not NaN (isinstance check)."""
        writer = make_writer(match_columns=["code"])
        row = pd.Series({"code": "ABC", "val": 1})
        cols = [make_col("val")]
        params = writer._build_params(row, cols, "update")
        assert params["match_code"] == "ABC"


# =============================================================================
# _prepare_dataframe — GeoSeries conversion (lines 824-828)
# =============================================================================

class TestPrepareDataframeGeoSeries:

    def test_geoseries_converted_to_wkt(self):
        """Lines 820-828: GeoSeries columns converted to WKT strings."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
        except ImportError:
            pytest.skip("geopandas/shapely not installed")

        writer = make_writer()
        gdf = gpd.GeoDataFrame({
            "id": [1, 2],
            "geometry": [Point(12.4, 41.9), Point(11.2, 43.7)],
        })

        result = writer._prepare_dataframe(gdf)

        # geometry column should now contain WKT strings
        assert isinstance(result["geometry"].iloc[0], str)
        assert "POINT" in result["geometry"].iloc[0].upper()

    def test_geoseries_none_values_become_none(self):
        """Line 825: None geometry -> None."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
        except ImportError:
            pytest.skip("geopandas/shapely not installed")

        writer = make_writer()
        gdf = gpd.GeoDataFrame({
            "id": [1, 2],
            "geometry": [Point(12.4, 41.9), None],
        })

        result = writer._prepare_dataframe(gdf)
        # None geometry becomes NaN after pandas apply — this is expected pandas behavior
        assert pd.isna(result["geometry"].iloc[1])

    def test_prepare_without_geopandas_installed(self):
        """Lines 827-828: ImportError from geopandas -> pass gracefully."""
        writer = make_writer()
        df = pd.DataFrame({"id": [1], "nome": ["test"]})

        with patch.dict("sys.modules", {"geopandas": None}):
            # Should not raise
            result = writer._prepare_dataframe(df)

        assert len(result) == 1


# =============================================================================
# _geometry_to_wkt — GeoJSON and WKB paths (lines 889-890, 895)
# =============================================================================

class TestGeometryToWktEdgeCases:

    def test_geojson_string_converted_to_wkt(self):
        """Lines 884-890: GeoJSON string -> WKT via shapely."""
        import json
        writer = make_writer()
        geojson_str = json.dumps({"type": "Point", "coordinates": [12.4, 41.9]})
        result = writer._geometry_to_wkt(geojson_str)
        assert result is not None
        assert "POINT" in result.upper()

    def test_geojson_invalid_falls_through(self):
        """Lines 889-890: invalid GeoJSON -> falls through to WKB attempt."""
        writer = make_writer()
        result = writer._geometry_to_wkt("{invalid json")
        # Should not crash; returns the string as-is after WKB attempt fails too
        assert result is not None

    def test_wkb_hex_string_converted(self):
        """Line 895: WKB hex -> WKT via shapely."""
        try:
            from shapely.geometry import Point
            from shapely import wkb
        except ImportError:
            pytest.skip("shapely not installed")

        writer = make_writer()
        # Create a valid WKB hex string
        point = Point(12.4, 41.9)
        wkb_hex = point.wkb_hex
        result = writer._geometry_to_wkt(wkb_hex)
        assert result is not None
        assert "POINT" in result.upper()


# =============================================================================
# _oracle_setup (lines 916-922)
# =============================================================================

class TestOracleSetup:

    def test_oracle_setup_executes_alter_session(self):
        """Lines 916-919: ALTER SESSION executed via text()."""
        writer = make_writer(db_type="oracle")
        mock_conn = MagicMock()

        writer._oracle_setup(mock_conn)

        mock_conn.execute.assert_called_once()
        # Verify the text() clause contains the expected SQL
        text_clause = mock_conn.execute.call_args[0][0]
        assert "ALTER SESSION" in str(text_clause.text)

    def test_oracle_setup_exception_swallowed(self):
        """Lines 920-922: exception is caught and swallowed."""
        writer = make_writer(db_type="oracle")
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("ORA-xxx")

        # Should NOT raise
        writer._oracle_setup(mock_conn)
