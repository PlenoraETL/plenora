"""
tests/estrai/unit/test_type_mapper.py
=======================================
Unit test diretti su TypeMapper e ResultProcessor.

NON richiede DB, API o Celery.
Testa mapping tipo nativo DB → tipo interno URBY, che nei test di integrazione
viene esercitato solo implicitamente (non si verifica il mapping isolato).

Obiettivi coverage:
  - Ogni ramo di map_postgres / map_mssql / map_mysql / map_oracle / map_sqlite
  - Casi speciali: Oracle NUMBER variants, MySQL tinyint(1), SQLite euristiche
  - ResultProcessor: process_*_rows su righe sintetiche (nessun DB reale)
  - Mutation score: assert sui valori esatti, non solo "non crasha"
"""

import pytest

pytestmark = pytest.mark.unit

from app.core.estrai.database_inspector import TypeMapper, ResultProcessor


# =============================================================================
# TypeMapper — PostgreSQL
# =============================================================================

class TestTypeMapperPostgres:

    # Interi
    def test_integer(self):
        assert TypeMapper.map_postgres("integer", "int4", None, None) == "int"

    def test_bigint(self):
        assert TypeMapper.map_postgres("bigint", "int8", None, None) == "int"

    def test_smallint(self):
        assert TypeMapper.map_postgres("smallint", "int2", None, None) == "int"

    def test_serial(self):
        assert TypeMapper.map_postgres("integer", "serial", None, None) == "int"

    # Decimali
    def test_numeric(self):
        assert TypeMapper.map_postgres("numeric", "numeric", None, None) == "real"

    def test_double_precision(self):
        assert TypeMapper.map_postgres("double precision", "float8", None, None) == "real"

    def test_real(self):
        assert TypeMapper.map_postgres("real", "float4", None, None) == "real"

    # Stringhe
    def test_varchar(self):
        assert TypeMapper.map_postgres("character varying", "varchar", None, None) == "str"

    def test_text(self):
        assert TypeMapper.map_postgres("text", "text", None, None) == "str"

    def test_json(self):
        assert TypeMapper.map_postgres("json", "json", None, None) == "str"

    def test_jsonb(self):
        assert TypeMapper.map_postgres("jsonb", "jsonb", None, None) == "str"

    # Booleani
    def test_boolean(self):
        assert TypeMapper.map_postgres("boolean", "bool", None, None) == "bool"

    # Date
    def test_date(self):
        assert TypeMapper.map_postgres("date", "date", None, None) == "date"

    def test_timestamp(self):
        assert TypeMapper.map_postgres("timestamp without time zone", "timestamp", None, None) == "date"

    def test_timestamptz(self):
        assert TypeMapper.map_postgres("timestamp with time zone", "timestamptz", None, None) == "date"

    # Geometry — casi critici (mutation target: presenza/assenza srid)
    def test_geometry_user_defined_senza_srid(self):
        """USER-DEFINED senza geom_type → geometry base."""
        result = TypeMapper.map_postgres("USER-DEFINED", "geometry", None, None)
        assert result == "geometry"

    def test_geometry_point_con_srid(self):
        """Mutation target: srid presente → deve comparire nel tipo."""
        result = TypeMapper.map_postgres("USER-DEFINED", "geometry", "Point", 4326)
        assert result == "geometry:point:4326"
        assert "4326" in result    # se mutato rimuovendo srid, fallisce

    def test_geometry_polygon_con_srid(self):
        result = TypeMapper.map_postgres("USER-DEFINED", "geometry", "Polygon", 32632)
        assert result == "geometry:polygon:32632"

    def test_geometry_linestring_con_srid(self):
        result = TypeMapper.map_postgres("USER-DEFINED", "geometry", "LineString", 4326)
        assert result == "geometry:line:4326"

    def test_geometry_multipolygon_con_srid(self):
        result = TypeMapper.map_postgres("USER-DEFINED", "geometry", "MultiPolygon", 4326)
        assert result == "geometry:multipolygon:4326"

    def test_geometry_con_geom_type_ma_senza_srid(self):
        """geom_type presente ma srid=None → geometry base."""
        result = TypeMapper.map_postgres("USER-DEFINED", "geometry", "Point", None)
        assert result == "geometry"

    def test_tipo_sconosciuto_fallback_str(self):
        result = TypeMapper.map_postgres("tipo_inesistente_xyz", "xyz", None, None)
        assert result == "str"


# =============================================================================
# TypeMapper — SQL Server
# =============================================================================

class TestTypeMapperMssql:

    def test_int(self):
        assert TypeMapper.map_mssql("int") == "int"

    def test_bigint(self):
        assert TypeMapper.map_mssql("bigint") == "int"

    def test_nvarchar(self):
        assert TypeMapper.map_mssql("nvarchar") == "str"

    def test_varchar(self):
        assert TypeMapper.map_mssql("varchar") == "str"

    def test_decimal(self):
        assert TypeMapper.map_mssql("decimal") == "real"

    def test_float(self):
        assert TypeMapper.map_mssql("float") == "real"

    def test_bit(self):
        """Mutation target: bit → bool, non str."""
        assert TypeMapper.map_mssql("bit") == "bool"

    def test_datetime(self):
        assert TypeMapper.map_mssql("datetime") == "date"

    def test_datetime2(self):
        assert TypeMapper.map_mssql("datetime2") == "date"

    def test_geometry(self):
        """geometry type → geometry."""
        assert TypeMapper.map_mssql("geometry") == "geometry"

    def test_geography(self):
        assert TypeMapper.map_mssql("geography") == "geometry"

    def test_sconosciuto(self):
        assert TypeMapper.map_mssql("unknown_type") == "str"


# =============================================================================
# TypeMapper — MySQL / MariaDB
# =============================================================================

class TestTypeMapperMysql:

    def test_int(self):
        assert TypeMapper.map_mysql("int", "int(11)") == "int"

    def test_bigint(self):
        assert TypeMapper.map_mysql("bigint", "bigint(20)") == "int"

    def test_varchar(self):
        assert TypeMapper.map_mysql("varchar", "varchar(255)") == "str"

    def test_text(self):
        assert TypeMapper.map_mysql("text", "text") == "str"

    def test_decimal(self):
        assert TypeMapper.map_mysql("decimal", "decimal(10,2)") == "real"

    def test_double(self):
        assert TypeMapper.map_mysql("double", "double") == "real"

    def test_date(self):
        assert TypeMapper.map_mysql("date", "date") == "date"

    def test_datetime(self):
        assert TypeMapper.map_mysql("datetime", "datetime") == "date"

    def test_tinyint_1_e_bool(self):
        """Mutation target: tinyint(1) → bool (caso speciale MySQL per booleani)."""
        result = TypeMapper.map_mysql("tinyint", "tinyint(1)")
        assert result == "bool"

    def test_tinyint_2_non_bool(self):
        """Mutation target: tinyint(2) → int, NON bool."""
        result = TypeMapper.map_mysql("tinyint", "tinyint(2)")
        assert result == "int"
        assert result != "bool"  # se il branch viene mutato, fallisce

    def test_tinyint_4_non_bool(self):
        result = TypeMapper.map_mysql("tinyint", "tinyint(4)")
        assert result != "bool"

    def test_geometry_senza_srid(self):
        result = TypeMapper.map_mysql("geometry", "geometry", srs_id=None)
        assert result.startswith("geometry")
        assert ":" not in result or result == "geometry"

    def test_point_con_srid(self):
        result = TypeMapper.map_mysql("point", "point", srs_id=4326)
        assert "4326" in result
        assert "point" in result.lower()

    def test_polygon_con_srid(self):
        result = TypeMapper.map_mysql("polygon", "polygon", srs_id=4326)
        assert "polygon" in result.lower()

    def test_sconosciuto(self):
        assert TypeMapper.map_mysql("tipo_xyz", "tipo_xyz") == "str"


# =============================================================================
# TypeMapper — Oracle
# =============================================================================

class TestTypeMapperOracle:

    def test_number_precision_1_e_bool(self):
        """Mutation target: NUMBER(1) → bool."""
        result = TypeMapper.map_oracle("NUMBER", precision=1, scale=None)
        assert result == "bool"

    def test_number_scale_0_e_int(self):
        """Mutation target: NUMBER(10,0) → int (nessun decimale)."""
        result = TypeMapper.map_oracle("NUMBER", precision=10, scale=0)
        assert result == "int"

    def test_number_scale_positivo_e_real(self):
        """NUMBER(10,2) → real (ha decimali)."""
        result = TypeMapper.map_oracle("NUMBER", precision=10, scale=2)
        assert result == "real"

    def test_number_senza_precisione_e_real(self):
        """NUMBER senza precision/scale → real (default)."""
        result = TypeMapper.map_oracle("NUMBER", precision=None, scale=None)
        assert result == "real"

    def test_number_precision_2_non_bool(self):
        """Mutation target: NUMBER(2) → real/int, NON bool."""
        result = TypeMapper.map_oracle("NUMBER", precision=2, scale=None)
        assert result != "bool"

    def test_varchar2(self):
        assert TypeMapper.map_oracle("VARCHAR2", None, None) == "str"

    def test_clob(self):
        assert TypeMapper.map_oracle("CLOB", None, None) == "str"

    def test_date(self):
        assert TypeMapper.map_oracle("DATE", None, None) == "date"

    def test_timestamp(self):
        assert TypeMapper.map_oracle("TIMESTAMP", None, None) == "date"

    def test_sdo_geometry(self):
        assert TypeMapper.map_oracle("SDO_GEOMETRY", None, None) == "geometry"

    def test_mdsys_sdo_geometry(self):
        assert TypeMapper.map_oracle("MDSYS.SDO_GEOMETRY", None, None) == "geometry"

    def test_sconosciuto(self):
        assert TypeMapper.map_oracle("TIPO_SCONOSCIUTO", None, None) == "str"


# =============================================================================
# TypeMapper — SQLite
# =============================================================================

class TestTypeMapperSqlite:

    def test_integer(self):
        assert TypeMapper.map_sqlite("integer") == "int"

    def test_int(self):
        assert TypeMapper.map_sqlite("int") == "int"

    def test_real(self):
        assert TypeMapper.map_sqlite("real") == "real"

    def test_float(self):
        assert TypeMapper.map_sqlite("float") == "real"

    def test_text(self):
        assert TypeMapper.map_sqlite("text") == "str"

    def test_varchar_con_params(self):
        """VARCHAR(255) → strip params → varchar → str."""
        assert TypeMapper.map_sqlite("VARCHAR(255)") == "str"

    def test_char_con_params(self):
        assert TypeMapper.map_sqlite("CHAR(10)") == "str"

    def test_date(self):
        assert TypeMapper.map_sqlite("date") == "date"

    def test_datetime(self):
        assert TypeMapper.map_sqlite("datetime") == "date"

    def test_boolean(self):
        assert TypeMapper.map_sqlite("boolean") == "bool"

    def test_euristica_int_substring(self):
        """UNSIGNED BIG INT → contiene 'int' → int."""
        assert TypeMapper.map_sqlite("UNSIGNED BIG INT") == "int"

    def test_euristica_real_substring(self):
        """DOUBLE PRECISION → contiene 'double' → real."""
        assert TypeMapper.map_sqlite("DOUBLE PRECISION") == "real"

    def test_euristica_bool_substring(self):
        assert TypeMapper.map_sqlite("MYBOOL") == "bool"

    def test_tipo_completamente_sconosciuto(self):
        """Tipo non riconosciuto → str (default SQLite)."""
        assert TypeMapper.map_sqlite("CUSTOM_TYPE_XYZ") == "str"

    def test_tipo_vuoto(self):
        """Tipo vuoto (colonna senza tipo dichiarato in SQLite) → str."""
        result = TypeMapper.map_sqlite("")
        assert result == "str"


# =============================================================================
# ResultProcessor — process_*_rows con righe sintetiche
# =============================================================================

class TestResultProcessor:

    def test_process_postgres_basic(self):
        rows = [
            ("utenti", "id",    "integer",           "int4",   None, None),
            ("utenti", "nome",  "character varying", "varchar",None, None),
            ("utenti", "attivo","boolean",            "bool",   None, None),
        ]
        result = ResultProcessor.process_postgres_rows(rows)
        assert "utenti" in result
        assert result["utenti"][0] == {"name": "id",     "type": "int"}
        assert result["utenti"][1] == {"name": "nome",   "type": "str"}
        assert result["utenti"][2] == {"name": "attivo", "type": "bool"}

    def test_process_postgres_geometry_con_srid(self):
        rows = [
            ("luoghi", "id",   "integer",    "int4",     None,    None),
            ("luoghi", "geom", "USER-DEFINED","geometry", "Point", 4326),
        ]
        result = ResultProcessor.process_postgres_rows(rows)
        geom_col = result["luoghi"][1]
        assert geom_col["name"] == "geom"
        assert geom_col["type"] == "geometry:point:4326"

    def test_process_postgres_tabelle_multiple(self):
        rows = [
            ("tab_a", "id",   "integer", "int4", None, None),
            ("tab_b", "nome", "text",    "text", None, None),
        ]
        result = ResultProcessor.process_postgres_rows(rows)
        assert "tab_a" in result
        assert "tab_b" in result

    def test_process_mssql_basic(self):
        rows = [
            ("prodotti", "id",          "int"),
            ("prodotti", "nome",        "nvarchar"),
            ("prodotti", "prezzo",      "decimal"),
            ("prodotti", "disponibile", "bit"),
            ("prodotti", "creato_il",   "datetime2"),
        ]
        result = ResultProcessor.process_mssql_rows(rows)
        assert result["prodotti"][0]["type"] == "int"
        assert result["prodotti"][1]["type"] == "str"
        assert result["prodotti"][2]["type"] == "real"
        assert result["prodotti"][3]["type"] == "bool"
        assert result["prodotti"][4]["type"] == "date"

    def test_process_mysql_basic(self):
        rows = [
            ("vendite", "id",        "int",     "int(11)",       None),
            ("vendite", "prodotto",  "varchar", "varchar(100)",  None),
            ("vendite", "quantita",  "int",     "int(11)",       None),
            ("vendite", "data_vend", "datetime","datetime",      None),
        ]
        result = ResultProcessor.process_mysql_rows(rows, has_srid=False)
        assert result["vendite"][0]["type"] == "int"
        assert result["vendite"][1]["type"] == "str"
        assert result["vendite"][3]["type"] == "date"

    def test_process_mysql_tinyint1_bool(self):
        """Mutation target: tinyint(1) in righe reali → bool."""
        rows = [
            ("flags", "attivo", "tinyint", "tinyint(1)", None),
            ("flags", "rank",   "tinyint", "tinyint(4)", None),
        ]
        result = ResultProcessor.process_mysql_rows(rows, has_srid=False)
        assert result["flags"][0]["type"] == "bool"
        assert result["flags"][1]["type"] == "int"

    def test_process_oracle_basic(self):
        rows = [
            ("tab", "ID",     "NUMBER",   10, 0),   # int
            ("tab", "PREZZO", "NUMBER",   10, 2),   # real
            ("tab", "FLAG",   "NUMBER",    1, None), # bool
            ("tab", "NOME",   "VARCHAR2", None, None),
            ("tab", "GEOM",   "SDO_GEOMETRY", None, None),
        ]
        result = ResultProcessor.process_oracle_rows(rows)
        types = {c["name"]: c["type"] for c in result["tab"]}
        assert types["ID"]     == "int"
        assert types["PREZZO"] == "real"
        assert types["FLAG"]   == "bool"
        assert types["NOME"]   == "str"
        assert types["GEOM"]   == "geometry"

    def test_process_sqlite_table(self):
        # Formato PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
        rows = [
            (0, "id",    "INTEGER", 1, None, 1),
            (1, "nome",  "TEXT",    0, None, 0),
            (2, "valore","REAL",    0, None, 0),
            (3, "flag",  "BOOLEAN", 0, None, 0),
        ]
        result = ResultProcessor.process_sqlite_table(rows)
        types = {c["name"]: c["type"] for c in result}
        assert types["id"]    == "int"
        assert types["nome"]  == "str"
        assert types["valore"]== "real"
        assert types["flag"]  == "bool"

    def test_process_postgres_righe_vuote(self):
        """Nessuna riga → dict vuoto."""
        result = ResultProcessor.process_postgres_rows([])
        assert result == {}
