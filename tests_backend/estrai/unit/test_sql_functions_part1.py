"""
tests/estrai/unit/test_sql_functions.py
========================================
Unit test su SqlFunctionTranslator, get_translator e translate_calculated_column.

NON richiede DB, API o Celery.

Obiettivi coverage:
  - SqlFunctionTranslator.translate() per ogni funzione e ogni DB
  - _qualify_column(): con e senza alias, con/senza punto, con espressione SQL
  - translate_calculated_column(): con e senza wrapping WKT geometria
  - _wrappa_geometria_come_wkt(): tutti i DB
  - get_translator(): tutti i DB validi + DB invalido
  - Mutation score: assert sui valori esatti delle stringhe SQL prodotte
"""

import pytest

pytestmark = pytest.mark.unit

from app.db.sql_functions import (
    SqlFunctionTranslator,
    get_translator,
    translate_calculated_column,
    get_function_catalog,
    DbType,
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def tr(db_type: str) -> SqlFunctionTranslator:
    return get_translator(db_type)




# =============================================================================
# get_translator — factory
# =============================================================================

class TestGetTranslator:

    def test_postgres(self):
        t = get_translator("postgres")
        assert t.db_type == DbType.POSTGRES

    def test_mssql(self):
        t = get_translator("mssql")
        assert t.db_type == DbType.MSSQL

    def test_mysql(self):
        t = get_translator("mysql")
        assert t.db_type == DbType.MYSQL

    def test_oracle(self):
        t = get_translator("oracle")
        assert t.db_type == DbType.ORACLE

    def test_sqlite(self):
        t = get_translator("sqlite")
        assert t.db_type == DbType.SQLITE

    def test_db_invalido_raise(self):
        """Mutation target: DB non in enum → ValueError."""
        with pytest.raises(ValueError):
            get_translator("db2_inesistente")


# =============================================================================
# _qualify_column
# =============================================================================

class TestQualifyColumn:

    @pytest.fixture
    def t(self):
        return tr("postgres")

    def test_senza_alias(self, t):
        assert t._qualify_column("col") == "col"

    def test_con_alias(self, t):
        """Mutation target: alias deve essere anteposto."""
        assert t._qualify_column("col", "t") == "t.col"

    def test_gia_qualificata_non_modificata(self, t):
        """Colonna già con punto → non deve diventare t.tabella.col."""
        assert t._qualify_column("tabella.col", "t") == "tabella.col"

    def test_espressione_con_parentesi_non_qualificata(self, t):
        """Espressione SQL complessa → non qualificare."""
        expr = "ST_Buffer(geom, 100)"
        assert t._qualify_column(expr, "t") == expr

    def test_non_stringa_convertita(self, t):
        """Valore non stringa → convertito a stringa."""
        result = t._qualify_column(42, "t")
        assert result == "42"


# =============================================================================
# translate() — funzioni geometriche postgres
# =============================================================================

class TestTranslateGeometricPostgres:

    @pytest.fixture
    def t(self):
        return tr("postgres")

    def test_centroid(self, t):
        result = t.translate("CENTROID", {"column": "geom"}, "tab")
        assert "tab.geom" in result
        assert "centroid" in result.lower()

    def test_buffer(self, t):
        result = t.translate("BUFFER", {"column": "geom", "distance": 100}, "tab")
        assert "tab.geom" in result
        assert "100" in result
        assert "buffer" in result.lower()

    def test_area(self, t):
        result = t.translate("AREA", {"column": "geom"}, "tab")
        assert "tab.geom" in result
        assert "area" in result.lower()

    def test_length(self, t):
        result = t.translate("LENGTH", {"column": "geom"}, "tab")
        assert "tab.geom" in result

    def test_simplify(self, t):
        result = t.translate("SIMPLIFY", {"column": "geom", "tolerance": 0.01}, "tab")
        assert "tab.geom" in result
        assert "0.01" in result

    def test_convex_hull(self, t):
        result = t.translate("CONVEX_HULL", {"column": "geom"}, "tab")
        assert "tab.geom" in result

    def test_envelope(self, t):
        result = t.translate("ENVELOPE", {"column": "geom"}, "tab")
        assert "tab.geom" in result

    def test_transform(self, t):
        result = t.translate("TRANSFORM", {"column": "geom", "srid": 4326}, "tab")
        assert "tab.geom" in result
        assert "4326" in result

    def test_funzione_non_supportata_raise(self, t):
        with pytest.raises(ValueError):
            t.translate("FUNZIONE_INESISTENTE", {}, "tab")


# =============================================================================
# translate() — funzioni geometriche multi-DB
# =============================================================================

class TestTranslateGeometricMultiDB:

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_centroid_tutti_db(self, db_type):
        result = tr(db_type).translate("CENTROID", {"column": "geom"}, "t")
        assert "geom" in result, f"CENTROID su {db_type}: geom mancante"

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_buffer_tutti_db(self, db_type):
        result = tr(db_type).translate("BUFFER", {"column": "geom", "distance": 50}, "t")
        assert "50" in result, f"BUFFER su {db_type}: distance mancante"
        assert "geom" in result, f"BUFFER su {db_type}: geom mancante"

    def test_buffer_mssql_usa_stbuffer(self):
        """Mutation target: MSSQL usa .STBuffer() non ST_Buffer()."""
        result = tr("mssql").translate("BUFFER", {"column": "geom", "distance": 100}, "t")
        assert "stbuffer" in result.lower()
        assert "ST_Buffer" not in result

    def test_buffer_oracle_usa_sdo(self):
        """Mutation target: Oracle usa SDO_GEOM."""
        result = tr("oracle").translate("BUFFER", {"column": "geom", "distance": 100}, "t")
        assert "SDO" in result.upper()


# =============================================================================
# translate() — funzioni stringa
# =============================================================================

class TestTranslateString:

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_upper_tutti_db(self, db_type):
        result = tr(db_type).translate("UPPER", {"column": "nome"}, "t")
        assert "nome" in result
        assert "upper" in result.lower()

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_lower_tutti_db(self, db_type):
        result = tr(db_type).translate("LOWER", {"column": "nome"}, "t")
        assert "lower" in result.lower()

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_trim_tutti_db(self, db_type):
        result = tr(db_type).translate("TRIM", {"column": "nome"}, "t")
        assert "trim" in result.lower()

    def test_concat_postgres(self):
        result = tr("postgres").translate("CONCAT", {"values": ["col1", "col2"]}, "t")
        assert "col1" in result and "col2" in result

    def test_substring_postgres(self):
        result = tr("postgres").translate("SUBSTRING",
                                           {"column": "nome", "start": 1, "length": 5}, "t")
        assert "1" in result and "5" in result


# =============================================================================
# translate() — funzioni numeriche
# =============================================================================

class TestTranslateNumeric:

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_round_tutti_db(self, db_type):
        result = tr(db_type).translate("ROUND", {"column": "valore", "decimals": 2}, "t")
        assert "2" in result
        assert "valore" in result

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_abs_tutti_db(self, db_type):
        result = tr(db_type).translate("ABS", {"column": "valore"}, "t")
        assert "abs" in result.lower()

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_ceil_tutti_db(self, db_type):
        result = tr(db_type).translate("CEIL", {"column": "valore"}, "t")
        assert "ceil" in result.lower()

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_floor_tutti_db(self, db_type):
        result = tr(db_type).translate("FLOOR", {"column": "valore"}, "t")
        assert "floor" in result.lower()


# =============================================================================
# translate() — funzioni data
# =============================================================================

class TestTranslateDate:

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_year_tutti_db(self, db_type):
        result = tr(db_type).translate("YEAR", {"column": "data"}, "t")
        assert "data" in result
        assert "year" in result.lower() or "strftime" in result.lower()

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_month_tutti_db(self, db_type):
        result = tr(db_type).translate("MONTH", {"column": "data"}, "t")
        assert "data" in result

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_day_tutti_db(self, db_type):
        result = tr(db_type).translate("DAY", {"column": "data"}, "t")
        assert "data" in result


# =============================================================================
# translate_calculated_column()
# =============================================================================

class TestTranslateCalculatedColumn:

    def test_upper_postgres_con_alias(self):
        """Colonna calcolata UPPER → SQL con AS."""
        result = translate_calculated_column(
            db_type="postgres",
            column_name="nome_maiuscolo",
            expression={"function": "UPPER", "params": {"column": "nome"}},
        )
        assert "AS nome_maiuscolo" in result
        assert "nome" in result
        assert "upper" in result.lower()

    def test_round_con_alias(self):
        result = translate_calculated_column(
            db_type="postgres",
            column_name="valore_arrotondato",
            expression={"function": "ROUND", "params": {"column": "valore", "decimals": 2}},
        )
        assert "AS valore_arrotondato" in result
        assert "2" in result

    def test_geometry_wrappata_st_astext_postgres(self):
        """Funzione che produce geometria → wrappata con ST_AsText."""
        result = translate_calculated_column(
            db_type="postgres",
            column_name="centro",
            expression={"function": "CENTROID", "params": {"column": "geom"}},
            table_alias="t",
        )
        assert "AS centro" in result
        assert "st_astext" in result.lower()

    def test_geometry_wrappata_oracle(self):
        """Oracle: geometria wrappata con SDO_UTIL.TO_WKTGEOMETRY."""
        result = translate_calculated_column(
            db_type="oracle",
            column_name="centro",
            expression={"function": "CENTROID", "params": {"column": "geom"}},
            table_alias="t",
        )
        assert "AS centro" in result
        assert "sdo_util" in result.lower()

    def test_geometry_wrappata_mssql(self):
        """MSSQL: geometria wrappata con .STAsText()."""
        result = translate_calculated_column(
            db_type="mssql",
            column_name="centro",
            expression={"function": "CENTROID", "params": {"column": "geom"}},
            table_alias="t",
        )
        assert "AS centro" in result
        assert "stastext" in result.lower()

    def test_non_geometry_non_wrappata(self):
        """Funzione che NON produce geometria → NON wrappata con ST_AsText."""
        result = translate_calculated_column(
            db_type="postgres",
            column_name="val",
            expression={"function": "ROUND", "params": {"column": "valore", "decimals": 0}},
        )
        assert "ST_AsText" not in result
        assert "AS val" in result

    def test_con_table_alias(self):
        result = translate_calculated_column(
            db_type="postgres",
            column_name="nome_up",
            expression={"function": "UPPER", "params": {"column": "nome"}},
            table_alias="c",
        )
        assert "nome" in result
        assert "AS nome_up" in result


# =============================================================================
# get_function_catalog
# =============================================================================

class TestGetFunctionCatalog:

    def test_catalogo_non_vuoto(self):
        catalog = get_function_catalog()
        assert len(catalog) >= 40

    def test_contiene_chiavi_obbligatorie(self):
        catalog = get_function_catalog()
        for name, func in catalog.items():
            assert "label" in func, f"{name}: campo label mancante"
            assert "category" in func, f"{name}: campo category mancante"

    def test_centroid_nel_catalogo(self):
        catalog = get_function_catalog()
        assert "CENTROID" in catalog

    def test_buffer_nel_catalogo(self):
        catalog = get_function_catalog()
        assert "BUFFER" in catalog

    # --- Nuove funzioni nel catalogo ---

    @pytest.mark.parametrize("func_name", [
        "MOD", "POWER", "SQRT", "SIGN", "GREATEST", "LEAST",
        "REPLACE", "LEFT", "RIGHT", "POSITION", "LTRIM", "RTRIM",
        "NOW", "CURRENT_DATE", "DATE_DIFF", "DATE_ADD", "EXTRACT",
        "COALESCE", "NULLIF", "CASE_WHEN", "IIF",
        "TO_INTEGER", "TO_FLOAT", "TO_STRING", "TO_DATE",
    ])
    def test_nuova_funzione_nel_catalogo(self, func_name):
        catalog = get_function_catalog()
        assert func_name in catalog, f"{func_name} mancante dal catalogo"


# =============================================================================
# translate() — nuove funzioni numeriche
# =============================================================================

class TestTranslateNumericNew:

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_mod_tutti_db(self, db_type):
        result = tr(db_type).translate("MOD", {"column": "valore", "divisor": 3}, "t")
        assert "valore" in result, f"MOD su {db_type}: valore mancante"
        assert "3" in result, f"MOD su {db_type}: divisor mancante"

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_power_tutti_db(self, db_type):
        result = tr(db_type).translate("POWER", {"column": "valore", "exponent": 2}, "t")
        assert "valore" in result, f"POWER su {db_type}: valore mancante"
        assert "2" in result, f"POWER su {db_type}: exponent mancante"
        assert "power" in result.lower(), f"POWER su {db_type}: POWER() mancante"

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_sqrt_tutti_db(self, db_type):
        result = tr(db_type).translate("SQRT", {"column": "valore"}, "t")
        assert "valore" in result, f"SQRT su {db_type}: valore mancante"
        assert "sqrt" in result.lower(), f"SQRT su {db_type}: SQRT() mancante"

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_sign_tutti_db(self, db_type):
        result = tr(db_type).translate("SIGN", {"column": "valore"}, "t")
        assert "valore" in result, f"SIGN su {db_type}: valore mancante"
        assert "sign" in result.lower(), f"SIGN su {db_type}: SIGN() mancante"

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_greatest_tutti_db(self, db_type):
        result = tr(db_type).translate("GREATEST", {"values": ["col1", "col2"]}, "t")
        assert "col1" in result, f"GREATEST su {db_type}: col1 mancante"
        assert "col2" in result, f"GREATEST su {db_type}: col2 mancante"

    def test_greatest_mssql_usa_iif(self):
        """MSSQL non ha GREATEST nativo, usa IIF."""
        result = tr("mssql").translate("GREATEST", {"values": ["col1", "col2"]}, "t")
        assert "iif" in result.lower()

    def test_greatest_sqlite_usa_max(self):
        """SQLite non ha GREATEST nativo, usa MAX()."""
        result = tr("sqlite").translate("GREATEST", {"values": ["col1", "col2"]}, "t")
        assert "max" in result.lower()

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_least_tutti_db(self, db_type):
        result = tr(db_type).translate("LEAST", {"values": ["col1", "col2"]}, "t")
        assert "col1" in result, f"LEAST su {db_type}: col1 mancante"
        assert "col2" in result, f"LEAST su {db_type}: col2 mancante"

    def test_least_mssql_usa_iif(self):
        """MSSQL non ha LEAST nativo, usa IIF."""
        result = tr("mssql").translate("LEAST", {"values": ["col1", "col2"]}, "t")
        assert "iif" in result.lower()

    def test_least_sqlite_usa_min(self):
        """SQLite non ha LEAST nativo, usa MIN()."""
        result = tr("sqlite").translate("LEAST", {"values": ["col1", "col2"]}, "t")
        assert "min" in result.lower()
