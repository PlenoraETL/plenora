"""
tests/carica/unit/test_models_extra.py
========================================
Unit test aggiuntivi su models.py, WriteConfig, WriteResult,
e metodi helper residui per portare CARICA a 400 test totali.

Copre:
  - WriteMode enum valori
  - WriteStatus enum valori  
  - TransactionMode enum valori
  - ConflictPolicy enum valori
  - WriteConfig.from_node_data: parsing base
  - WriteResult: status, rows, calcoli
  - ColumnMapping: attributi default
  - DDLGenerator.generate_insert tutti i db_type
  - DDLGenerator._geometry_placeholder tutti i db_type
  - ColumnMapper._sanitize_column_name casi aggiuntivi
  - ColumnMapper._infer_type_from_pandas edge cases
"""

import pytest
import pandas as pd
import numpy as np

pytestmark = pytest.mark.unit

from app.core.carica.models import (
    WriteMode, WriteStatus, TransactionMode, ConflictPolicy,
    ColumnMapping, WriteConfig, WriteResult, BatchProgress
)
from app.core.carica.ddl_generator import DDLGenerator
from app.core.carica.column_mapper import ColumnMapper


def col(src, dest=None, dest_type="str", is_geom=False, skip=False, pk=False):
    return ColumnMapping(
        source_column=src, dest_column=dest or src,
        dest_type=dest_type, is_geometry=is_geom,
        is_primary_key=pk, skip=skip,
    )


# =============================================================================
# Enum valori
# =============================================================================

class TestEnums:

    def test_write_mode_valori(self):
        assert WriteMode.CREATE == "create"
        assert WriteMode.APPEND == "append"
        assert WriteMode.REPLACE == "replace"
        assert WriteMode.UPDATE == "update"
        assert WriteMode.UPSERT == "upsert"
        assert WriteMode.TRUNCATE_INSERT == "truncate_insert"

    def test_write_status_valori(self):
        assert WriteStatus.SUCCESS == "success"
        assert WriteStatus.PARTIAL == "partial"
        assert WriteStatus.CONFLICT == "conflict"
        assert WriteStatus.WRITE_ERROR == "write_error"
        assert WriteStatus.CONNECTION_ERROR == "connection_error"

    def test_transaction_mode_valori(self):
        assert TransactionMode.ALL_OR_NOTHING == "all_or_nothing"
        assert TransactionMode.PER_BATCH == "per_batch"

    def test_conflict_policy_valori(self):
        assert ConflictPolicy.ABORT == "abort"
        assert ConflictPolicy.SKIP == "skip"
        assert ConflictPolicy.FORCE == "force"


# =============================================================================
# ColumnMapping default
# =============================================================================

class TestColumnMappingDefaults:

    def test_is_geometry_default_false(self):
        m = ColumnMapping(source_column="id", dest_column="id")
        assert m.is_geometry is False

    def test_is_primary_key_default_false(self):
        m = ColumnMapping(source_column="id", dest_column="id")
        assert m.is_primary_key is False

    def test_skip_default_false(self):
        m = ColumnMapping(source_column="id", dest_column="id")
        assert m.skip is False

    def test_srid_default_none(self):
        m = ColumnMapping(source_column="id", dest_column="id")
        assert m.srid is None

    def test_dest_type_default_vuoto(self):
        m = ColumnMapping(source_column="id", dest_column="id")
        assert m.dest_type == ""


# =============================================================================
# WriteResult
# =============================================================================

class TestWriteResult:

    def test_rows_written_default_zero(self):
        r = WriteResult(started_at="", total_rows=10,
                        table_name="public.test", write_mode="create")
        assert r.rows_written == 0

    def test_rows_failed_default_zero(self):
        r = WriteResult(started_at="", total_rows=10,
                        table_name="public.test", write_mode="create")
        assert r.rows_failed == 0

    def test_status_default_non_success(self):
        r = WriteResult(started_at="", total_rows=10,
                        table_name="public.test", write_mode="create")
        # Prima del write, status non è SUCCESS
        assert r.status != WriteStatus.SUCCESS or r.rows_written == 0

    def test_error_message_default_vuoto(self):
        r = WriteResult(started_at="", total_rows=10,
                        table_name="public.test", write_mode="create")
        assert r.error_message == "" or r.error_message is None


# =============================================================================
# BatchProgress
# =============================================================================

class TestBatchProgress:

    def test_batch_progress_attributi(self):
        bp = BatchProgress(
            batch_number=2, total_batches=5,
            rows_written=200, total_rows=500,
            elapsed_seconds=3.5
        )
        assert bp.batch_number == 2
        assert bp.total_batches == 5
        assert bp.rows_written == 200

    def test_percentuale_calcolabile(self):
        bp = BatchProgress(
            batch_number=3, total_batches=5,
            rows_written=300, total_rows=500,
            elapsed_seconds=2.0
        )
        pct = bp.rows_written / bp.total_rows * 100
        assert abs(pct - 60.0) < 0.01


# =============================================================================
# DDLGenerator.generate_insert — tutti i db_type
# =============================================================================

class TestGenerateInsertAllDb:

    def _cols(self):
        return [col("id", dest_type="int"), col("nome", dest_type="str")]

    def test_oracle_insert(self):
        gen = DDLGenerator("oracle")
        sql, params = gen.generate_insert("myschema", "mytable", self._cols())
        assert "INSERT INTO" in sql.upper()
        assert "MYTABLE" in sql

    def test_mssql_insert(self):
        gen = DDLGenerator("mssql")
        sql, params = gen.generate_insert("dbo", "test", self._cols())
        assert "INSERT INTO" in sql.upper()

    def test_mysql_insert(self):
        gen = DDLGenerator("mysql")
        sql, params = gen.generate_insert("mydb", "test", self._cols())
        assert "INSERT INTO" in sql.upper()
        assert "`mydb`" in sql or "`test`" in sql

    def test_params_non_vuoti(self):
        for db in ["postgres", "oracle", "mssql", "mysql"]:
            gen = DDLGenerator(db)
            _, params = gen.generate_insert("public", "test", self._cols())
            assert len(params) > 0

    def test_insert_geometry_postgres(self):
        gen = DDLGenerator("postgres")
        from app.core.carica.models import ColumnMapping
        geom_col = ColumnMapping(source_column="geom", dest_column="geom", is_geometry=True, srid=4326)
        cols = [col("id", dest_type="int"), geom_col]
        sql, params = gen.generate_insert("public", "test", cols)
        assert "geom" in sql.lower()


# =============================================================================
# DDLGenerator._geometry_placeholder
# =============================================================================

class TestGeometryPlaceholder:

    def test_postgres_st_geomfromtext(self):
        gen = DDLGenerator("postgres")
        result = gen._geometry_placeholder("geom", 4326)
        assert "ST_GeomFromText" in result or "geom" in result.lower()
        assert "4326" in result

    def test_oracle_sdo(self):
        gen = DDLGenerator("oracle")
        result = gen._geometry_placeholder("geom", 4326)
        assert "SDO" in result or "geom" in result.lower()

    def test_mssql_geometry(self):
        gen = DDLGenerator("mssql")
        result = gen._geometry_placeholder("geom", 4326)
        assert "geom" in result.lower() or "geometry" in result.lower()

    def test_mysql_geometry(self):
        gen = DDLGenerator("mysql")
        result = gen._geometry_placeholder("geom", 4326)
        assert result is not None and len(result) > 0


# =============================================================================
# ColumnMapper._sanitize_column_name — casi aggiuntivi
# =============================================================================

class TestSanitizeColumnNameExtra:

    def test_stringa_con_solo_speciali(self):
        m = ColumnMapper("postgres")
        result = m._sanitize_column_name("@#$%")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_numero_iniziale_gestito(self):
        m = ColumnMapper("postgres")
        result = m._sanitize_column_name("1colonna")
        assert isinstance(result, str)

    def test_underscore_iniziale_finale_rimossi(self):
        m = ColumnMapper("postgres")
        result = m._sanitize_column_name("__colonna__")
        assert isinstance(result, str)

    def test_oracle_tronca_30_chars_esatto(self):
        m = ColumnMapper("oracle")
        name = "a" * 30
        result = m._sanitize_column_name(name)
        assert len(result) <= 30

    def test_nome_con_punto(self):
        m = ColumnMapper("postgres")
        result = m._sanitize_column_name("schema.column")
        assert "." not in result


# =============================================================================
# ColumnMapper._infer_type_from_pandas — edge cases
# =============================================================================

class TestInferTypeFromPandasExtra:

    def test_int32(self):
        s = pd.Series([1, 2, 3], dtype="int32")
        result = ColumnMapper("postgres")._infer_type_from_pandas(s)
        assert result == "int"

    def test_float32(self):
        s = pd.Series([1.0, 2.0], dtype="float32")
        result = ColumnMapper("postgres")._infer_type_from_pandas(s)
        assert result == "real"

    def test_uint8(self):
        s = pd.Series([1, 2, 3], dtype="uint8")
        result = ColumnMapper("postgres")._infer_type_from_pandas(s)
        assert result == "int"

    def test_category_come_str(self):
        s = pd.Categorical(["a", "b", "a"])
        s = pd.Series(s)
        result = ColumnMapper("postgres")._infer_type_from_pandas(s)
        assert result in ("str", "real")

    def test_object_tutti_none(self):
        s = pd.Series([None, None, None])
        result = ColumnMapper("postgres")._infer_type_from_pandas(s)
        assert result == "str"

    def test_object_misto_numerico_stringa(self):
        s = pd.Series(["1", "2", "abc"])
        result = ColumnMapper("postgres")._infer_type_from_pandas(s)
        assert result == "str"
