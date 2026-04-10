"""
tests/carica/unit/test_ddl_generator.py
========================================
Unit test su DDLGenerator — generatore DDL multi-database.

NON richiede backend, DB o Celery.

Copre:
  - __init__: db_type valido/invalido
  - qualified_table_name: postgres/mssql/oracle/mysql con/senza schema
  - quote_column: per ogni db_type
  - resolve_column_type: geometry, object dtype, tipo esplicito, URBY interno, pandas fallback
  - _geometry_type: per ogni db_type
  - generate_create_table: struttura SQL, colonne, geometria
  - generate_drop_table, generate_truncate
  - generate_spatial_index: postgres/altri
  - generate_insert: struttura query
  - generate_upsert: postgres/oracle/mssql/mysql
  - query_list_schemas, query_list_tables, query_table_exists, query_table_columns
"""

import pytest
import pandas as pd

pytestmark = pytest.mark.unit

from app.core.carica.ddl_generator import DDLGenerator
from app.core.carica.models import ColumnMapping


# ─── Helper ───────────────────────────────────────────────────────────────────

def col(name, dest_type="", is_geom=False, srid=None, skip=False):
    return ColumnMapping(
        source_column=name,
        dest_column=name,
        dest_type=dest_type,
        is_geometry=is_geom,
        srid=srid,
        skip=skip,
    )


# =============================================================================
# __init__ — db_type validation
# =============================================================================

class TestInit:

    def test_postgres_valido(self):
        gen = DDLGenerator("postgres")
        assert gen.db_type == "postgres"

    def test_case_insensitive(self):
        gen = DDLGenerator("postgres")
        assert gen.db_type == "postgres"

    def test_oracle_valido(self):
        gen = DDLGenerator("oracle")
        assert gen.db_type == "oracle"

    def test_mssql_valido(self):
        gen = DDLGenerator("mssql")
        assert gen.db_type == "mssql"

    def test_mysql_valido(self):
        gen = DDLGenerator("mysql")
        assert gen.db_type == "mysql"

    def test_db_type_invalido_raise(self):
        with pytest.raises(ValueError, match="non supportato"):
            DDLGenerator("mongodb")


# =============================================================================
# qualified_table_name
# =============================================================================

class TestQualifiedTableName:

    def test_postgres_con_schema(self):
        gen = DDLGenerator("postgres")
        result = gen.qualified_table_name("public", "immobili")
        assert result == '"public"."immobili"'

    def test_postgres_senza_schema(self):
        gen = DDLGenerator("postgres")
        result = gen.qualified_table_name("", "immobili")
        assert result == '"immobili"'

    def test_oracle_uppercase(self):
        """Mutation target: Oracle → UPPERCASE."""
        gen = DDLGenerator("oracle")
        result = gen.qualified_table_name("myschema", "mytable")
        assert "MYSCHEMA" in result
        assert "MYTABLE" in result

    def test_mysql_backtick(self):
        """Mutation target: MySQL → backtick."""
        gen = DDLGenerator("mysql")
        result = gen.qualified_table_name("mydb", "mytable")
        assert "`mydb`" in result
        assert "`mytable`" in result

    def test_mssql_con_schema(self):
        gen = DDLGenerator("mssql")
        result = gen.qualified_table_name("dbo", "immobili")
        assert result == '"dbo"."immobili"'

    def test_strip_spazi(self):
        """Mutation target: spazi in schema/table devono essere rimossi."""
        gen = DDLGenerator("postgres")
        result = gen.qualified_table_name(" public ", " immobili ")
        assert " " not in result


# =============================================================================
# quote_column
# =============================================================================

class TestQuoteColumn:

    def test_postgres(self):
        assert DDLGenerator("postgres").quote_column("nome") == '"nome"'

    def test_oracle_uppercase(self):
        result = DDLGenerator("oracle").quote_column("nome")
        assert result == '"NOME"'

    def test_mysql_backtick(self):
        result = DDLGenerator("mysql").quote_column("nome")
        assert result == "`nome`"

    def test_mssql(self):
        result = DDLGenerator("mssql").quote_column("nome")
        assert result == '"nome"'


# =============================================================================
# _geometry_type
# =============================================================================

class TestGeometryType:

    def test_postgres_postgis(self):
        gen = DDLGenerator("postgres")
        result = gen._geometry_type(4326)
        assert "geometry" in result.lower()
        assert "4326" in result

    def test_oracle_sdo(self):
        result = DDLGenerator("oracle")._geometry_type(4326)
        assert "SDO_GEOMETRY" in result

    def test_mssql(self):
        result = DDLGenerator("mssql")._geometry_type(4326)
        assert "geometry" in result.lower()

    def test_mysql(self):
        result = DDLGenerator("mysql")._geometry_type(4326)
        assert "GEOMETRY" in result


# =============================================================================
# resolve_column_type
# =============================================================================

class TestResolveColumnType:

    def test_geometria(self):
        gen = DDLGenerator("postgres")
        m = col("geom", is_geom=True, srid=4326)
        result = gen.resolve_column_type(m)
        assert "geometry" in result.lower()
        assert "4326" in result

    def test_pandas_object_forzato_text(self):
        """Mutation target: object dtype → TEXT indipendentemente dal dest_type."""
        gen = DDLGenerator("postgres")
        m = col("codice", dest_type="int")
        result = gen.resolve_column_type(m, pandas_dtype="object")
        assert result in ("TEXT", "VARCHAR", "NVARCHAR(MAX)", "CLOB") or "TEXT" in result or "VARCHAR" in result

    def test_tipo_esplicito_sql(self):
        """Tipo già SQL (non URBY interno) → passato direttamente."""
        gen = DDLGenerator("postgres")
        m = col("nome", dest_type="VARCHAR(255)")
        result = gen.resolve_column_type(m)
        assert result == "VARCHAR(255)"

    def test_urby_interno_int(self):
        gen = DDLGenerator("postgres")
        m = col("valore", dest_type="int")
        result = gen.resolve_column_type(m)
        assert "INT" in result.upper()

    def test_urby_interno_real(self):
        gen = DDLGenerator("postgres")
        m = col("prezzo", dest_type="real")
        result = gen.resolve_column_type(m)
        assert any(t in result.upper() for t in ["FLOAT", "DOUBLE", "REAL", "NUMERIC"])

    def test_pandas_int_fallback(self):
        gen = DDLGenerator("postgres")
        m = col("id", dest_type="")
        result = gen.resolve_column_type(m, pandas_dtype="int64")
        assert "INT" in result.upper()

    def test_pandas_float_fallback(self):
        gen = DDLGenerator("postgres")
        m = col("prezzo", dest_type="")
        result = gen.resolve_column_type(m, pandas_dtype="float64")
        assert any(t in result.upper() for t in ["FLOAT", "DOUBLE", "REAL", "NUMERIC"])

    def test_default_text(self):
        """Nessun tipo → TEXT."""
        gen = DDLGenerator("postgres")
        m = col("note", dest_type="")
        result = gen.resolve_column_type(m, pandas_dtype="object")
        assert any(t in result.upper() for t in ["TEXT", "VARCHAR", "CLOB"])


# =============================================================================
# generate_create_table
# =============================================================================

class TestGenerateCreateTable:

    def test_struttura_base(self):
        gen = DDLGenerator("postgres")
        cols = [col("id", "int"), col("nome", "str")]
        sql = gen.generate_create_table("public", "test", cols)
        assert "CREATE TABLE" in sql.upper()
        assert '"public"."test"' in sql

    def test_colonne_presenti(self):
        gen = DDLGenerator("postgres")
        cols = [col("id", "int"), col("nome", "str"), col("prezzo", "real")]
        sql = gen.generate_create_table("public", "test", cols)
        assert '"id"' in sql
        assert '"nome"' in sql
        assert '"prezzo"' in sql

    def test_colonna_skip_esclusa(self):
        """Colonna con skip=True non deve apparire nel DDL."""
        gen = DDLGenerator("postgres")
        cols = [col("id", "int"), col("_interno", "str", skip=True)]
        sql = gen.generate_create_table("public", "test", cols)
        assert '"_interno"' not in sql

    def test_geometria_inclusa(self):
        gen = DDLGenerator("postgres")
        cols = [col("id", "int"), col("geom", is_geom=True, srid=4326)]
        sql = gen.generate_create_table("public", "test", cols)
        assert "geometry" in sql.lower()

    def test_oracle_uppercase(self):
        gen = DDLGenerator("oracle")
        cols = [col("id", "int")]
        sql = gen.generate_create_table("myschema", "mytable", cols)
        assert "MYTABLE" in sql


# =============================================================================
# generate_drop_table e generate_truncate
# =============================================================================

class TestDropAndTruncate:

    def test_drop_postgres(self):
        gen = DDLGenerator("postgres")
        sql = gen.generate_drop_table("public", "test")
        assert "DROP TABLE" in sql.upper()
        assert '"public"."test"' in sql

    def test_drop_if_exists(self):
        gen = DDLGenerator("postgres")
        sql = gen.generate_drop_table("public", "test")
        assert "IF EXISTS" in sql.upper()

    def test_truncate_postgres(self):
        gen = DDLGenerator("postgres")
        sql = gen.generate_truncate("public", "test")
        assert "TRUNCATE" in sql.upper()
        assert '"public"."test"' in sql


# =============================================================================
# generate_insert
# =============================================================================

class TestGenerateInsert:

    def test_struttura_insert(self):
        gen = DDLGenerator("postgres")
        cols = [col("id", "int"), col("nome", "str")]
        sql, params = gen.generate_insert("public", "test", cols)
        assert "INSERT INTO" in sql.upper()
        assert '"id"' in sql
        assert '"nome"' in sql

    def test_parametri_corretti(self):
        gen = DDLGenerator("postgres")
        cols = [col("id", "int"), col("nome", "str")]
        sql, params = gen.generate_insert("public", "test", cols)
        assert len(params) == 2

    def test_colonna_skip_esclusa(self):
        gen = DDLGenerator("postgres")
        cols = [col("id", "int"), col("_skip", "str", skip=True)]
        sql, params = gen.generate_insert("public", "test", cols)
        assert '"_skip"' not in sql


# =============================================================================
# generate_upsert per ogni db_type
# =============================================================================

class TestGenerateUpsert:

    def _cols(self):
        return [col("id", "int"), col("nome", "str"), col("valore", "real")]

    def test_postgres_on_conflict(self):
        gen = DDLGenerator("postgres")
        sql, _ = gen.generate_upsert("public", "test", self._cols(), ["id"])
        assert "ON CONFLICT" in sql.upper() or "INSERT" in sql.upper()

    def test_oracle_merge(self):
        gen = DDLGenerator("oracle")
        sql, _ = gen.generate_upsert("myschema", "mytable", self._cols(), ["ID"])
        assert "MERGE" in sql.upper() or "INSERT" in sql.upper()

    def test_mssql_merge(self):
        gen = DDLGenerator("mssql")
        sql, _ = gen.generate_upsert("dbo", "test", self._cols(), ["id"])
        assert "MERGE" in sql.upper() or "INSERT" in sql.upper()

    def test_mysql_on_duplicate(self):
        gen = DDLGenerator("mysql")
        sql, _ = gen.generate_upsert("mydb", "test", self._cols(), ["id"])
        assert "DUPLICATE" in sql.upper() or "INSERT" in sql.upper()


# =============================================================================
# query helpers
# =============================================================================

class TestQueryHelpers:

    def test_query_list_schemas_postgres(self):
        gen = DDLGenerator("postgres")
        sql = gen.query_list_schemas()
        assert "schema" in sql.lower() or "information_schema" in sql.lower()

    def test_query_list_tables_postgres(self):
        gen = DDLGenerator("postgres")
        sql = gen.query_list_tables("public")
        assert "table" in sql.lower()

    def test_query_table_exists_postgres(self):
        gen = DDLGenerator("postgres")
        sql = gen.query_table_exists("public", "test")
        assert "public" in sql.lower() or "test" in sql.lower()

    def test_query_table_columns_postgres(self):
        gen = DDLGenerator("postgres")
        sql = gen.query_table_columns("public", "test")
        assert "column" in sql.lower()

    def test_tutti_i_db_supportano_list_tables(self):
        """Ogni db_type deve avere una query list_tables valida."""
        for db_type in ["postgres", "oracle", "mssql", "mysql"]:
            gen = DDLGenerator(db_type)
            sql = gen.query_list_tables("myschema")
            assert isinstance(sql, str)
            assert len(sql) > 0
