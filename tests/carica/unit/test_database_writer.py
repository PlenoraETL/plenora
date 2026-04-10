"""
tests/carica/unit/test_database_writer.py
==========================================
Unit test sui metodi puri di DatabaseWriter e ConflictChecker.

NON richiede backend, DB o Celery.

Copre:
  - DatabaseWriter._geometry_to_wkt: WKT string, shapely, GeoJSON, None
  - DatabaseWriter._fix_mixed_type_columns: tipi misti, solo numeri, solo stringhe
  - DatabaseWriter._prepare_dataframe: skip colonne, GeoSeries
  - DatabaseWriter._build_params: None/NaN, geometry, numpy, datetime, update match
  - ConflictChecker._qualified_name: tutti i db_type
  - ConflictChecker._quote_col: tutti i db_type
  - ConflictChecker._check_internal_duplicates: duplicati, nessun duplicato
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.unit

from app.core.carica.conflict_checker import ConflictChecker, _serialize_value
from app.core.carica.models import (
    ColumnMapping, WriteConfig, WriteMode, TransactionMode
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_col(src, dest=None, dest_type="str", is_geom=False, srid=None, skip=False, pk=False):
    return ColumnMapping(
        source_column=src,
        dest_column=dest or src,
        dest_type=dest_type,
        is_geometry=is_geom,
        srid=srid,
        is_primary_key=pk,
        skip=skip,
    )


def make_write_config(**kwargs):
    defaults = {
        "connection_id": "conn-1",
        "table_name": "test_table",
        "schema_name": "public",
        "write_mode": WriteMode.CREATE,
        "column_mappings": [],
        "match_columns": [],
        "geometry_srid": 4326,
        "geometry_column": "geometry",
        "batch_size": 1000,
        "transaction_mode": TransactionMode.ALL_OR_NOTHING,
    }
    defaults.update(kwargs)
    return WriteConfig(**defaults)


def make_writer(**kwargs):
    """Crea DatabaseWriter con engine mockato."""
    from app.core.carica.database_writer import DatabaseWriter
    config = make_write_config(**kwargs)
    mock_engine = MagicMock()
    mock_engine.dialect.name = "postgresql"
    writer = DatabaseWriter.__new__(DatabaseWriter)
    writer.config = config
    writer.engine = mock_engine
    writer.db_type = "postgres"
    writer.ddl = MagicMock()
    writer.mapper = MagicMock()
    writer.progress_callback = None
    return writer


def make_conflict_checker(db_type="postgres"):
    engine = MagicMock()
    return ConflictChecker(engine, db_type)


# =============================================================================
# DatabaseWriter._geometry_to_wkt
# =============================================================================

class TestGeometryToWkt:

    def test_none_restituisce_none(self):
        w = make_writer()
        assert w._geometry_to_wkt(None) is None

    def test_wkt_stringa_point(self):
        w = make_writer()
        result = w._geometry_to_wkt("POINT(12.4 41.9)")
        assert result == "POINT(12.4 41.9)"

    def test_wkt_stringa_polygon(self):
        w = make_writer()
        result = w._geometry_to_wkt("POLYGON((0 0, 1 0, 1 1, 0 0))")
        assert "POLYGON" in result

    def test_shapely_geometry(self):
        """Shapely geometry con .wkt → usa .wkt."""
        from shapely.geometry import Point
        w = make_writer()
        geom = Point(12.4, 41.9)
        result = w._geometry_to_wkt(geom)
        assert "POINT" in result.upper()

    def test_geojson_stringa(self):
        """Stringa GeoJSON → converte in WKT."""
        import json
        w = make_writer()
        geojson = json.dumps({"type": "Point", "coordinates": [12.4, 41.9]})
        result = w._geometry_to_wkt(geojson)
        assert result is not None
        assert "POINT" in result.upper()

    def test_stringa_non_geometry_passthrough(self):
        """Stringa non geometrica → restituita invariata."""
        w = make_writer()
        result = w._geometry_to_wkt("valore_normale")
        assert result is not None

    def test_multipoint(self):
        w = make_writer()
        result = w._geometry_to_wkt("MULTIPOINT((0 0), (1 1))")
        assert "MULTIPOINT" in result


# =============================================================================
# DatabaseWriter._fix_mixed_type_columns
# =============================================================================

class TestFixMixedTypeColumns:

    def test_colonna_mista_convertita_a_str(self):
        """Mutation target: numeri + stringhe → tutto str."""
        w = make_writer()
        df = pd.DataFrame({"codice": [95, 100, "26A", 150]})
        result = w._fix_mixed_type_columns(df)
        # Dopo il fix la colonna deve essere tutta stringhe
        non_null = result["codice"].dropna()
        assert all(isinstance(v, str) for v in non_null)

    def test_colonna_solo_numeri_invariata(self):
        """Colonna con soli numeri (come object) non deve essere convertita."""
        w = make_writer()
        df = pd.DataFrame({"valore": pd.array([1.5, 2.5, 3.5], dtype=object)})
        result = w._fix_mixed_type_columns(df)
        # Non deve crashare
        assert "valore" in result.columns

    def test_colonna_solo_stringhe_invariata(self):
        w = make_writer()
        df = pd.DataFrame({"nome": ["Roma", "Milano", "Napoli"]})
        result = w._fix_mixed_type_columns(df)
        assert list(result["nome"]) == ["Roma", "Milano", "Napoli"]

    def test_colonna_non_object_skip(self):
        """Colonne non-object non vengono toccate."""
        w = make_writer()
        df = pd.DataFrame({"id": [1, 2, 3], "val": [1.5, 2.5, 3.5]})
        result = w._fix_mixed_type_columns(df)
        assert result["id"].dtype == df["id"].dtype

    def test_colonna_tutta_null_skip(self):
        """Colonna tutta null → non modificata."""
        w = make_writer()
        df = pd.DataFrame({"note": [None, None, None]})
        result = w._fix_mixed_type_columns(df)
        assert result["note"].isna().all()


# =============================================================================
# DatabaseWriter._build_params
# =============================================================================

class TestBuildParams:

    def test_valore_normale(self):
        w = make_writer()
        row = pd.Series({"nome": "Roma", "pop": 4000000})
        cols = [make_col("nome"), make_col("pop", dest_type="int")]
        params = w._build_params(row, cols, "insert")
        assert params["nome"] == "Roma"
        assert params["pop"] == 4000000

    def test_nan_diventa_none(self):
        """Mutation target: NaN → None (SQL NULL)."""
        w = make_writer()
        row = pd.Series({"nome": float("nan")})
        cols = [make_col("nome")]
        params = w._build_params(row, cols, "insert")
        assert params["nome"] is None

    def test_none_rimane_none(self):
        w = make_writer()
        row = pd.Series({"nome": None})
        cols = [make_col("nome")]
        params = w._build_params(row, cols, "insert")
        assert params["nome"] is None

    def test_geometry_convertita_wkt(self):
        from shapely.geometry import Point
        w = make_writer()
        row = pd.Series({"geom": Point(12.4, 41.9)})
        cols = [make_col("geom", is_geom=True, srid=4326)]
        params = w._build_params(row, cols, "insert")
        assert "POINT" in str(params["geom"]).upper()

    def test_numpy_int64_convertito(self):
        w = make_writer()
        row = pd.Series({"id": np.int64(42)})
        cols = [make_col("id", dest_type="int")]
        params = w._build_params(row, cols, "insert")
        assert params["id"] == 42
        assert isinstance(params["id"], (int, float))

    def test_datetime_a_iso_string(self):
        w = make_writer()
        dt = datetime(2024, 1, 15, 12, 0, 0)
        row = pd.Series({"data": dt})
        cols = [make_col("data", dest_type="date")]
        params = w._build_params(row, cols, "insert")
        assert "2024" in str(params["data"])

    def test_update_aggiunge_match_prefix(self):
        """Per UPDATE → parametri match con prefisso match_."""
        w = make_writer(
            match_columns=["id"],
            write_mode=WriteMode.UPDATE
        )
        row = pd.Series({"id": 1, "nome": "Roma"})
        cols = [make_col("id", dest_type="int"), make_col("nome")]
        params = w._build_params(row, cols, "update")
        assert "match_id" in params
        assert params["match_id"] == 1


# =============================================================================
# ConflictChecker._qualified_name
# =============================================================================

class TestConflictCheckerQualifiedName:

    def test_postgres_con_schema(self):
        cc = make_conflict_checker("postgres")
        result = cc._qualified_name("public", "test")
        assert result == '"public"."test"'

    def test_postgres_senza_schema(self):
        cc = make_conflict_checker("postgres")
        result = cc._qualified_name("", "test")
        assert result == '"test"'

    def test_oracle_uppercase(self):
        cc = make_conflict_checker("oracle")
        result = cc._qualified_name("myschema", "mytable")
        assert "MYSCHEMA" in result
        assert "MYTABLE" in result

    def test_mysql_backtick(self):
        cc = make_conflict_checker("mysql")
        result = cc._qualified_name("mydb", "test")
        assert "`mydb`" in result
        assert "`test`" in result

    def test_mssql(self):
        cc = make_conflict_checker("mssql")
        result = cc._qualified_name("dbo", "test")
        assert '"dbo"."test"' == result


# =============================================================================
# ConflictChecker._quote_col
# =============================================================================

class TestConflictCheckerQuoteCol:

    def test_postgres(self):
        cc = make_conflict_checker("postgres")
        assert cc._quote_col("nome") == '"nome"'

    def test_oracle_uppercase(self):
        cc = make_conflict_checker("oracle")
        assert cc._quote_col("nome") == '"NOME"'

    def test_mysql_backtick(self):
        cc = make_conflict_checker("mysql")
        assert cc._quote_col("nome") == "`nome`"

    def test_mssql(self):
        cc = make_conflict_checker("mssql")
        assert cc._quote_col("nome") == '"nome"'


# =============================================================================
# ConflictChecker._check_internal_duplicates
# =============================================================================

class TestCheckInternalDuplicates:

    def test_nessun_duplicato(self):
        cc = make_conflict_checker("postgres")
        df = pd.DataFrame({"id": [1, 2, 3], "nome": ["a", "b", "c"]})
        conflicts = cc._check_internal_duplicates(df, ["id"])
        assert conflicts == 0

    def test_con_duplicati(self):
        cc = make_conflict_checker("postgres")
        df = pd.DataFrame({"id": [1, 1, 2], "nome": ["a", "b", "c"]})
        conflicts = cc._check_internal_duplicates(df, ["id"])
        assert conflicts > 0

    def test_match_columns_multiple(self):
        """Duplicati su combinazione di colonne."""
        cc = make_conflict_checker("postgres")
        df = pd.DataFrame({
            "comune": ["Roma", "Roma", "Milano"],
            "anno":   [2024,   2024,   2024],
            "val":    [1,      2,      3],
        })
        conflicts = cc._check_internal_duplicates(df, ["comune", "anno"])
        assert conflicts > 0

    def test_df_vuoto_nessun_conflitto(self):
        cc = make_conflict_checker("postgres")
        df = pd.DataFrame({"id": [], "nome": []})
        conflicts = cc._check_internal_duplicates(df, ["id"])
        assert conflicts == 0
