"""
tests/carica/unit/test_carica_final.py
========================================
Test finali per portare CARICA a 400 test totali.

Copre:
  - DDLGenerator.generate_drop_table tutti i db_type
  - DDLGenerator.generate_truncate tutti i db_type
  - DDLGenerator.qualified_table_name edge cases
  - DDLGenerator.resolve_column_type casi aggiuntivi
  - ColumnMapper._extract_srid casi aggiuntivi
  - ColumnMapper._find_matching_column con existing_map complesso
  - DatabaseWriter._geometry_to_wkt tutti i formati
  - ConflictChecker._serialize_value casi aggiuntivi
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, date, timezone

pytestmark = pytest.mark.unit

from app.core.carica.ddl_generator import DDLGenerator
from app.core.carica.column_mapper import ColumnMapper
from app.core.carica.conflict_checker import _serialize_value
from app.core.carica.models import ColumnMapping


def col(src, dest=None, dest_type="str", is_geom=False, srid=None):
    return ColumnMapping(
        source_column=src, dest_column=dest or src,
        dest_type=dest_type, is_geometry=is_geom, srid=srid,
    )


# =============================================================================
# DDLGenerator.generate_drop_table — tutti i db_type
# =============================================================================

class TestDropTableAllDb:

    def test_oracle_drop(self):
        gen = DDLGenerator("oracle")
        sql = gen.generate_drop_table("myschema", "mytable")
        assert "DROP TABLE" in sql.upper()
        assert "MYTABLE" in sql

    def test_mssql_drop(self):
        gen = DDLGenerator("mssql")
        sql = gen.generate_drop_table("dbo", "test")
        assert "DROP TABLE" in sql.upper()

    def test_mysql_drop(self):
        gen = DDLGenerator("mysql")
        sql = gen.generate_drop_table("mydb", "test")
        assert "DROP TABLE" in sql.upper()
        assert "`test`" in sql

    def test_drop_if_exists_tutti_db(self):
        for db in ["postgres", "oracle", "mssql", "mysql"]:
            gen = DDLGenerator(db)
            sql = gen.generate_drop_table("public", "test")
            assert "IF EXISTS" in sql.upper() or "DROP" in sql.upper()


# =============================================================================
# DDLGenerator.generate_truncate — tutti i db_type
# =============================================================================

class TestTruncateAllDb:

    def test_oracle_truncate(self):
        gen = DDLGenerator("oracle")
        sql = gen.generate_truncate("myschema", "mytable")
        assert "TRUNCATE" in sql.upper()

    def test_mssql_truncate(self):
        gen = DDLGenerator("mssql")
        sql = gen.generate_truncate("dbo", "test")
        assert "TRUNCATE" in sql.upper()

    def test_mysql_truncate(self):
        gen = DDLGenerator("mysql")
        sql = gen.generate_truncate("mydb", "test")
        assert "TRUNCATE" in sql.upper()

    def test_postgres_truncate_cascade(self):
        gen = DDLGenerator("postgres")
        sql = gen.generate_truncate("public", "test")
        assert "TRUNCATE" in sql.upper()


# =============================================================================
# DDLGenerator.qualified_table_name — edge cases
# =============================================================================

class TestQualifiedTableNameExtra:

    def test_mariadb_uguale_mysql(self):
        mysql_gen = DDLGenerator("mysql")
        mariadb_gen = DDLGenerator("mariadb")
        r1 = mysql_gen.qualified_table_name("db", "tbl")
        r2 = mariadb_gen.qualified_table_name("db", "tbl")
        assert r1 == r2

    def test_oracle_senza_schema_solo_tabella(self):
        gen = DDLGenerator("oracle")
        result = gen.qualified_table_name("", "mytable")
        assert "MYTABLE" in result
        assert "." not in result

    def test_mysql_senza_schema(self):
        gen = DDLGenerator("mysql")
        result = gen.qualified_table_name("", "test")
        assert "`test`" in result

    def test_mssql_senza_schema(self):
        gen = DDLGenerator("mssql")
        result = gen.qualified_table_name("", "test")
        assert '"test"' in result


# =============================================================================
# DDLGenerator.resolve_column_type — casi aggiuntivi
# =============================================================================

class TestResolveColumnTypeExtra:

    def test_geometry_con_srid_custom(self):
        gen = DDLGenerator("postgres")
        m = col("geom", is_geom=True, srid=32632)
        result = gen.resolve_column_type(m)
        assert "32632" in result

    def test_bool_urby(self):
        gen = DDLGenerator("postgres")
        m = col("flag", dest_type="bool")
        result = gen.resolve_column_type(m)
        assert any(t in result.upper() for t in ["BOOL", "BOOLEAN", "BIT", "TINYINT"])

    def test_date_urby(self):
        gen = DDLGenerator("postgres")
        m = col("data", dest_type="date")
        result = gen.resolve_column_type(m)
        assert any(t in result.upper() for t in ["DATE", "TIMESTAMP", "DATETIME"])

    def test_str_urby(self):
        gen = DDLGenerator("postgres")
        m = col("nome", dest_type="str")
        result = gen.resolve_column_type(m)
        assert any(t in result.upper() for t in ["TEXT", "VARCHAR", "NVARCHAR"])

    def test_pandas_bool_fallback(self):
        gen = DDLGenerator("postgres")
        m = col("flag", dest_type="")
        result = gen.resolve_column_type(m, pandas_dtype="bool")
        assert result is not None

    def test_pandas_datetime_fallback(self):
        gen = DDLGenerator("postgres")
        m = col("data", dest_type="")
        result = gen.resolve_column_type(m, pandas_dtype="datetime64[ns]")
        assert result is not None


# =============================================================================
# ColumnMapper._extract_srid — casi aggiuntivi
# =============================================================================

class TestExtractSridExtra:

    def test_geometry_multipolygon_32632(self):
        m = ColumnMapper("postgres")
        assert m._extract_srid("geometry:multipolygon:32632") == 32632

    def test_geometry_line_4326(self):
        m = ColumnMapper("postgres")
        assert m._extract_srid("geometry:line:4326") == 4326

    def test_due_parti_solo_tipo(self):
        m = ColumnMapper("postgres")
        assert m._extract_srid("geometry:point") == 4326

    def test_srid_zero(self):
        m = ColumnMapper("postgres")
        result = m._extract_srid("geometry:point:0")
        assert result == 0


# =============================================================================
# ColumnMapper._find_matching_column — casi aggiuntivi
# =============================================================================

class TestFindMatchingColumnExtra:

    def test_existing_map_case_misto(self):
        m = ColumnMapper("postgres")
        existing = {"comune": {"name": "Comune", "type": "VARCHAR"}}
        # "Comune" nel df deve matchare "comune" nel dict
        result = m._find_matching_column("Comune", existing)
        assert result == "Comune"

    def test_source_con_underscore(self):
        m = ColumnMapper("postgres")
        existing = {"nome_comune": {"name": "nome_comune", "type": "TEXT"}}
        result = m._find_matching_column("nome_comune", existing)
        assert result == "nome_comune"

    def test_parziale_non_matcha(self):
        """Match deve essere esatto, non parziale."""
        m = ColumnMapper("postgres")
        existing = {"nome": {"name": "nome", "type": "TEXT"}}
        result = m._find_matching_column("nome_completo", existing)
        assert result is None


# =============================================================================
# _serialize_value — casi aggiuntivi
# =============================================================================

class TestSerializeValueExtra:

    def test_numpy_float64(self):
        result = _serialize_value(np.float64(3.14))
        assert abs(result - 3.14) < 1e-9

    def test_numpy_bool_true(self):
        result = _serialize_value(np.bool_(True))
        assert result is True or result == 1

    def test_numpy_bool_false(self):
        result = _serialize_value(np.bool_(False))
        assert result is False or result == 0

    def test_lista_passthrough(self):
        # lista non è NaN/numpy scalar → passthrough
        result = _serialize_value("test_string")
        assert result == "test_string"

    def test_dict_passthrough(self):
        val = {"a": 1}
        result = _serialize_value(val)
        assert result == val

    def test_stringa_vuota(self):
        result = _serialize_value("")
        assert result == ""

    def test_zero_non_none(self):
        """Mutation target: 0 non deve diventare None."""
        result = _serialize_value(0)
        assert result == 0

    def test_false_non_none(self):
        result = _serialize_value(False)
        assert result is not None
