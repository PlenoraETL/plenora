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
# translate() — nuove funzioni stringa
# =============================================================================

class TestTranslateStringNew:

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_replace_tutti_db(self, db_type):
        result = tr(db_type).translate("REPLACE", {"column": "nome", "search": "abc", "replace": "xyz"}, "t")
        assert "nome" in result, f"REPLACE su {db_type}: nome mancante"
        assert "abc" in result, f"REPLACE su {db_type}: search mancante"
        assert "xyz" in result, f"REPLACE su {db_type}: replace mancante"

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_left_tutti_db(self, db_type):
        result = tr(db_type).translate("LEFT", {"column": "nome", "length": 5}, "t")
        assert "nome" in result, f"LEFT su {db_type}: nome mancante"
        assert "5" in result, f"LEFT su {db_type}: length mancante"

    def test_left_oracle_usa_substr(self):
        """Oracle non ha LEFT(), usa SUBSTR."""
        result = tr("oracle").translate("LEFT", {"column": "nome", "length": 5}, "t")
        assert "substr" in result.lower()

    def test_left_sqlite_usa_substr(self):
        """SQLite non ha LEFT(), usa SUBSTR."""
        result = tr("sqlite").translate("LEFT", {"column": "nome", "length": 5}, "t")
        assert "substr" in result.lower()

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_right_tutti_db(self, db_type):
        result = tr(db_type).translate("RIGHT", {"column": "nome", "length": 3}, "t")
        assert "nome" in result, f"RIGHT su {db_type}: nome mancante"
        assert "3" in result, f"RIGHT su {db_type}: length mancante"

    def test_right_oracle_usa_substr(self):
        """Oracle non ha RIGHT(), usa SUBSTR con indice negativo."""
        result = tr("oracle").translate("RIGHT", {"column": "nome", "length": 3}, "t")
        assert "substr" in result.lower()

    def test_right_sqlite_usa_substr(self):
        """SQLite non ha RIGHT(), usa SUBSTR con indice negativo."""
        result = tr("sqlite").translate("RIGHT", {"column": "nome", "length": 3}, "t")
        assert "substr" in result.lower()

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_position_tutti_db(self, db_type):
        result = tr(db_type).translate("POSITION", {"column": "nome", "search": "abc"}, "t")
        assert "nome" in result, f"POSITION su {db_type}: nome mancante"
        assert "abc" in result, f"POSITION su {db_type}: search mancante"

    def test_position_postgres_usa_position(self):
        result = tr("postgres").translate("POSITION", {"column": "nome", "search": "abc"}, "t")
        assert "position" in result.lower()

    def test_position_mssql_usa_charindex(self):
        result = tr("mssql").translate("POSITION", {"column": "nome", "search": "abc"}, "t")
        assert "charindex" in result.lower()

    def test_position_mysql_usa_locate(self):
        result = tr("mysql").translate("POSITION", {"column": "nome", "search": "abc"}, "t")
        assert "locate" in result.lower()

    def test_position_oracle_usa_instr(self):
        result = tr("oracle").translate("POSITION", {"column": "nome", "search": "abc"}, "t")
        assert "instr" in result.lower()

    def test_position_sqlite_usa_instr(self):
        result = tr("sqlite").translate("POSITION", {"column": "nome", "search": "abc"}, "t")
        assert "instr" in result.lower()

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_ltrim_tutti_db(self, db_type):
        result = tr(db_type).translate("LTRIM", {"column": "nome"}, "t")
        assert "ltrim" in result.lower(), f"LTRIM su {db_type}: LTRIM() mancante"

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_rtrim_tutti_db(self, db_type):
        result = tr(db_type).translate("RTRIM", {"column": "nome"}, "t")
        assert "rtrim" in result.lower(), f"RTRIM su {db_type}: RTRIM() mancante"


# =============================================================================
# translate() — nuove funzioni data
# =============================================================================

class TestTranslateDateNew:

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_now_tutti_db(self, db_type):
        result = tr(db_type).translate("NOW", {}, "t")
        assert result is not None and len(result) > 0, f"NOW su {db_type}: risultato vuoto"

    def test_now_postgres(self):
        result = tr("postgres").translate("NOW", {}, "t")
        assert "now()" in result.lower()

    def test_now_mssql(self):
        result = tr("mssql").translate("NOW", {}, "t")
        assert "getdate()" in result.lower()

    def test_now_mysql(self):
        result = tr("mysql").translate("NOW", {}, "t")
        assert "now()" in result.lower()

    def test_now_oracle(self):
        result = tr("oracle").translate("NOW", {}, "t")
        assert "sysdate" in result.lower()

    def test_now_sqlite(self):
        result = tr("sqlite").translate("NOW", {}, "t")
        assert "datetime" in result.lower() and "now" in result.lower()

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_current_date_tutti_db(self, db_type):
        result = tr(db_type).translate("CURRENT_DATE", {}, "t")
        assert result is not None and len(result) > 0, f"CURRENT_DATE su {db_type}: risultato vuoto"

    def test_current_date_mssql(self):
        result = tr("mssql").translate("CURRENT_DATE", {}, "t")
        assert "getdate" in result.lower()

    def test_current_date_mysql(self):
        result = tr("mysql").translate("CURRENT_DATE", {}, "t")
        assert "curdate" in result.lower()

    def test_current_date_oracle(self):
        result = tr("oracle").translate("CURRENT_DATE", {}, "t")
        assert "trunc" in result.lower()
        assert "sysdate" in result.lower()

    def test_current_date_sqlite(self):
        result = tr("sqlite").translate("CURRENT_DATE", {}, "t")
        assert "date" in result.lower() and "now" in result.lower()

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_date_diff_tutti_db(self, db_type):
        result = tr(db_type).translate("DATE_DIFF", {"column": "data1", "column2": "data2"}, "t")
        assert "data1" in result, f"DATE_DIFF su {db_type}: data1 mancante"
        assert "data2" in result, f"DATE_DIFF su {db_type}: data2 mancante"

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_date_add_tutti_db(self, db_type):
        result = tr(db_type).translate("DATE_ADD", {"column": "data", "interval": 7, "unit": "day"}, "t")
        assert "data" in result, f"DATE_ADD su {db_type}: data mancante"
        assert "7" in result, f"DATE_ADD su {db_type}: interval mancante"

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_extract_tutti_db(self, db_type):
        result = tr(db_type).translate("EXTRACT", {"column": "data", "part": "year"}, "t")
        assert "data" in result, f"EXTRACT su {db_type}: data mancante"
        assert "year" in result.lower() or "strftime" in result.lower(), \
            f"EXTRACT su {db_type}: year/strftime mancante"


# =============================================================================
# translate() — funzioni condizionali
# =============================================================================

class TestTranslateConditional:

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_coalesce_tutti_db(self, db_type):
        result = tr(db_type).translate("COALESCE", {"values": ["col1", "col2"]}, "t")
        assert "coalesce" in result.lower(), f"COALESCE su {db_type}: COALESCE() mancante"
        assert "col1" in result, f"COALESCE su {db_type}: col1 mancante"
        assert "col2" in result, f"COALESCE su {db_type}: col2 mancante"

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_nullif_tutti_db(self, db_type):
        result = tr(db_type).translate("NULLIF", {"column": "nome", "value": "N/A"}, "t")
        assert "nullif" in result.lower(), f"NULLIF su {db_type}: NULLIF() mancante"
        assert "nome" in result, f"NULLIF su {db_type}: nome mancante"
        assert "N/A" in result, f"NULLIF su {db_type}: value mancante"

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_case_when_tutti_db(self, db_type):
        result = tr(db_type).translate("CASE_WHEN", {
            "column": "stato",
            "operator": "=",
            "compare_value": "A",
            "then_value": "Attivo",
            "else_value": "Inattivo",
        }, "t")
        assert "case" in result.lower(), f"CASE_WHEN su {db_type}: CASE mancante"
        assert "when" in result.lower(), f"CASE_WHEN su {db_type}: WHEN mancante"
        assert "then" in result.lower(), f"CASE_WHEN su {db_type}: THEN mancante"
        assert "else" in result.lower(), f"CASE_WHEN su {db_type}: ELSE mancante"
        assert "end" in result.lower(), f"CASE_WHEN su {db_type}: END mancante"
        assert "stato" in result
        assert "Attivo" in result
        assert "Inattivo" in result

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_iif_tutti_db(self, db_type):
        result = tr(db_type).translate("IIF", {
            "column": "stato",
            "operator": "=",
            "compare_value": "A",
            "true_value": "Attivo",
            "false_value": "Inattivo",
        }, "t")
        assert "stato" in result, f"IIF su {db_type}: stato mancante"
        assert "Attivo" in result, f"IIF su {db_type}: true_value mancante"
        assert "Inattivo" in result, f"IIF su {db_type}: false_value mancante"

    def test_iif_postgres_usa_case(self):
        """PG non ha IIF nativo, traduce in CASE WHEN."""
        result = tr("postgres").translate("IIF", {
            "column": "stato", "operator": "=", "compare_value": "A",
            "true_value": "Attivo", "false_value": "Inattivo",
        }, "t")
        assert "case" in result.lower()

    def test_iif_oracle_usa_case(self):
        """Oracle non ha IIF nativo, traduce in CASE WHEN."""
        result = tr("oracle").translate("IIF", {
            "column": "stato", "operator": "=", "compare_value": "A",
            "true_value": "Attivo", "false_value": "Inattivo",
        }, "t")
        assert "case" in result.lower()

    def test_iif_mssql_usa_iif(self):
        """MSSQL ha IIF nativo."""
        result = tr("mssql").translate("IIF", {
            "column": "stato", "operator": "=", "compare_value": "A",
            "true_value": "Attivo", "false_value": "Inattivo",
        }, "t")
        assert "iif" in result.lower()

    def test_iif_mysql_usa_if(self):
        """MySQL usa IF() al posto di IIF."""
        result = tr("mysql").translate("IIF", {
            "column": "stato", "operator": "=", "compare_value": "A",
            "true_value": "Attivo", "false_value": "Inattivo",
        }, "t")
        assert "if" in result.lower()

    def test_iif_sqlite_usa_iif(self):
        """SQLite ha IIF nativo."""
        result = tr("sqlite").translate("IIF", {
            "column": "stato", "operator": "=", "compare_value": "A",
            "true_value": "Attivo", "false_value": "Inattivo",
        }, "t")
        assert "iif" in result.lower()


# =============================================================================
# translate() — funzioni cast
# =============================================================================

class TestTranslateCast:

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_to_integer_tutti_db(self, db_type):
        result = tr(db_type).translate("TO_INTEGER", {"column": "valore"}, "t")
        assert "valore" in result, f"TO_INTEGER su {db_type}: valore mancante"
        assert "cast" in result.lower(), \
            f"TO_INTEGER su {db_type}: CAST mancante"

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_to_float_tutti_db(self, db_type):
        result = tr(db_type).translate("TO_FLOAT", {"column": "valore"}, "t")
        assert "valore" in result, f"TO_FLOAT su {db_type}: valore mancante"
        assert "cast" in result.lower(), \
            f"TO_FLOAT su {db_type}: CAST mancante"

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_to_string_tutti_db(self, db_type):
        result = tr(db_type).translate("TO_STRING", {"column": "valore"}, "t")
        assert "valore" in result, f"TO_STRING su {db_type}: valore mancante"

    def test_to_string_postgres_as_text(self):
        result = tr("postgres").translate("TO_STRING", {"column": "valore"}, "t")
        assert "text" in result.lower()

    def test_to_string_mssql_as_nvarchar(self):
        result = tr("mssql").translate("TO_STRING", {"column": "valore"}, "t")
        assert "nvarchar" in result.lower()

    def test_to_string_mysql_as_char(self):
        result = tr("mysql").translate("TO_STRING", {"column": "valore"}, "t")
        assert "char" in result.lower()

    def test_to_string_oracle_to_char(self):
        result = tr("oracle").translate("TO_STRING", {"column": "valore"}, "t")
        assert "to_char" in result.lower()

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_to_date_tutti_db(self, db_type):
        result = tr(db_type).translate("TO_DATE", {"column": "valore"}, "t")
        assert "valore" in result, f"TO_DATE su {db_type}: valore mancante"

    def test_to_date_sqlite_usa_date(self):
        result = tr("sqlite").translate("TO_DATE", {"column": "valore"}, "t")
        assert "date" in result.lower()


# =============================================================================
# translate() — DATE_TRUNC e DATE_FORMAT
# =============================================================================

class TestTranslateDateTruncFormat:

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_date_trunc_tutti_db(self, db_type):
        result = tr(db_type).translate("DATE_TRUNC", {"column": "data", "unit": "month"}, "t")
        assert "data" in result, f"DATE_TRUNC su {db_type}: colonna mancante"

    def test_date_trunc_postgres(self):
        result = tr("postgres").translate("DATE_TRUNC", {"column": "data", "unit": "month"}, "t")
        assert "DATE_TRUNC" in result
        assert "month" in result

    def test_date_trunc_mssql(self):
        result = tr("mssql").translate("DATE_TRUNC", {"column": "data", "unit": "year"}, "t")
        assert "DATETRUNC" in result
        assert "year" in result

    def test_date_trunc_oracle(self):
        result = tr("oracle").translate("DATE_TRUNC", {"column": "data", "unit": "month"}, "t")
        assert "TRUNC" in result

    def test_date_trunc_mysql_month(self):
        result = tr("mysql").translate("DATE_TRUNC", {"column": "data", "unit": "month"}, "t")
        assert "data" in result
        assert "DATE" in result

    def test_date_trunc_sqlite(self):
        result = tr("sqlite").translate("DATE_TRUNC", {"column": "data", "unit": "month"}, "t")
        assert "date" in result.lower()

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_date_format_tutti_db(self, db_type):
        result = tr(db_type).translate("DATE_FORMAT", {"column": "data", "format": "DD/MM/YYYY"}, "t")
        assert "data" in result, f"DATE_FORMAT su {db_type}: colonna mancante"

    def test_date_format_postgres(self):
        result = tr("postgres").translate("DATE_FORMAT", {"column": "data", "format": "DD/MM/YYYY"}, "t")
        assert "TO_CHAR" in result

    def test_date_format_mssql(self):
        result = tr("mssql").translate("DATE_FORMAT", {"column": "data", "format": "DD/MM/YYYY"}, "t")
        assert "FORMAT" in result

    def test_date_format_mysql(self):
        result = tr("mysql").translate("DATE_FORMAT", {"column": "data", "format": "DD/MM/YYYY"}, "t")
        assert "DATE_FORMAT" in result

    def test_date_format_oracle(self):
        result = tr("oracle").translate("DATE_FORMAT", {"column": "data", "format": "DD/MM/YYYY"}, "t")
        assert "TO_CHAR" in result

    def test_date_format_sqlite(self):
        result = tr("sqlite").translate("DATE_FORMAT", {"column": "data", "format": "DD/MM/YYYY"}, "t")
        assert "strftime" in result.lower()
