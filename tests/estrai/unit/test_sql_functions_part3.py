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
# TestCoverageGaps — uncovered lines
# =============================================================================

class TestCoverageGapsDateTruncFormat:
    """Tests targeting specific uncovered lines to push coverage above 90%."""

    # --- Lines 857-865: CONCAT with separator on Oracle/SQLite (|| operator) ---

    def test_concat_separator_oracle(self):
        result = tr("oracle").translate("CONCAT", {"values": ["col1", "col2", "col3"], "separator": "-"}, "t")
        assert "||" in result
        assert "'-'" in result

    def test_concat_separator_sqlite(self):
        result = tr("sqlite").translate("CONCAT", {"values": ["col1", "col2", "col3"], "separator": "-"}, "t")
        assert "||" in result
        assert "'-'" in result

    def test_concat_no_separator_oracle(self):
        result = tr("oracle").translate("CONCAT", {"values": ["a", "b"], "separator": ""}, "t")
        assert "||" in result
        assert "'-'" not in result

    # --- Lines 848, 854: CONCAT with single CSV string input + with separator ---

    def test_concat_csv_string_input(self):
        result = tr("postgres").translate("CONCAT", {"values": "col1, col2, col3"}, "t")
        assert "CONCAT(" in result
        assert "t.col1" in result
        assert "t.col2" in result
        assert "t.col3" in result

    def test_concat_csv_string_with_separator(self):
        result = tr("postgres").translate("CONCAT", {"values": "col1, col2", "separator": ";"}, "t")
        assert "CONCAT_WS" in result
        assert "';'" in result

    # --- Lines 884-898: SUBSTRING without length on each DB ---

    def test_substring_no_length_postgres(self):
        result = tr("postgres").translate("SUBSTRING", {"column": "nome", "start": 3}, "t")
        assert "SUBSTRING(t.nome FROM 3)" == result

    def test_substring_no_length_mssql(self):
        result = tr("mssql").translate("SUBSTRING", {"column": "nome", "start": 3}, "t")
        assert "LEN(" in result
        assert "SUBSTRING(t.nome, 3, LEN(t.nome))" == result

    def test_substring_no_length_mysql(self):
        result = tr("mysql").translate("SUBSTRING", {"column": "nome", "start": 3}, "t")
        assert "SUBSTRING(t.nome, 3)" == result

    def test_substring_no_length_oracle(self):
        result = tr("oracle").translate("SUBSTRING", {"column": "nome", "start": 3}, "t")
        assert "SUBSTR(t.nome, 3)" == result

    def test_substring_no_length_sqlite(self):
        result = tr("sqlite").translate("SUBSTRING", {"column": "nome", "start": 3}, "t")
        assert "SUBSTR(t.nome, 3)" == result

    # --- Lines 954-984: MATH division zero-protection per DB ---

    def test_math_division_postgres(self):
        result = tr("postgres").translate("MATH", {"column1": "a", "operator": "/", "value": 10}, "t")
        assert "NULLIF" in result
        assert "t.a" in result

    def test_math_division_mssql(self):
        result = tr("mssql").translate("MATH", {"column1": "a", "operator": "/", "value": 5}, "t")
        assert "CASE WHEN" in result
        assert "THEN NULL" in result

    def test_math_division_mysql(self):
        result = tr("mysql").translate("MATH", {"column1": "a", "operator": "/", "value": 5}, "t")
        assert "IF(" in result
        assert "NULL" in result

    def test_math_division_oracle(self):
        result = tr("oracle").translate("MATH", {"column1": "a", "operator": "/", "value": 5}, "t")
        assert "CASE WHEN" in result

    def test_math_division_sqlite(self):
        result = tr("sqlite").translate("MATH", {"column1": "a", "operator": "/", "value": 5}, "t")
        assert "NULLIF" in result

    def test_math_division_with_column2(self):
        result = tr("postgres").translate("MATH", {"column1": "a", "operator": "/", "column2": "b"}, "t")
        assert "NULLIF(t.b, 0)" in result
        assert "t.a" in result

    def test_math_addition(self):
        result = tr("postgres").translate("MATH", {"column1": "a", "operator": "+", "value": 1}, "t")
        assert "(t.a + 1)" == result

    # --- Lines 726-735: PERIMETER on MSSQL and MySQL ---

    def test_perimeter_mssql(self):
        result = tr("mssql").translate("PERIMETER", {"column": "geom"}, "t")
        assert "STBoundary" in result
        assert "STLength" in result

    def test_perimeter_mysql(self):
        result = tr("mysql").translate("PERIMETER", {"column": "geom"}, "t")
        assert "ST_ExteriorRing" in result
        assert "ST_Length" in result

    def test_perimeter_postgres(self):
        result = tr("postgres").translate("PERIMETER", {"column": "geom"}, "t")
        assert "ST_Perimeter" in result

    def test_perimeter_oracle(self):
        result = tr("oracle").translate("PERIMETER", {"column": "geom"}, "t")
        assert "SDO_GEOM.SDO_LENGTH" in result

    def test_perimeter_sqlite(self):
        result = tr("sqlite").translate("PERIMETER", {"column": "geom"}, "t")
        assert "Perimeter" in result

    # --- Line 1023: ROUND with decimals=None and decimals="" ---

    def test_round_decimals_none(self):
        result = tr("postgres").translate("ROUND", {"column": "val", "decimals": None}, "t")
        assert "ROUND" in result
        assert "0" in result

    def test_round_decimals_empty_string(self):
        result = tr("postgres").translate("ROUND", {"column": "val", "decimals": ""}, "t")
        assert "ROUND" in result
        assert "0" in result

    # --- Line 1040: GREATEST with 3+ values on MSSQL (nested IIF) ---

    def test_greatest_mssql_three_values(self):
        result = tr("mssql").translate("GREATEST", {"values": ["a", "b", "c"]}, "t")
        assert "IIF" in result
        # Nested: IIF(IIF(a > b, a, b) > c, IIF(a > b, a, b), c) — 3 IIF occurrences
        assert result.count("IIF") == 3

    def test_greatest_postgres(self):
        result = tr("postgres").translate("GREATEST", {"values": ["a", "b", "c"]}, "t")
        assert "GREATEST(" in result

    def test_greatest_sqlite(self):
        result = tr("sqlite").translate("GREATEST", {"values": ["a", "b"]}, "t")
        assert "MAX(" in result

    def test_least_mssql_three_values(self):
        result = tr("mssql").translate("LEAST", {"values": ["x", "y", "z"]}, "t")
        assert "IIF" in result
        assert result.count("IIF") == 3

    def test_greatest_csv_string_input(self):
        result = tr("mssql").translate("GREATEST", {"values": "a, b, c"}, "t")
        assert "IIF" in result
        assert "t.a" in result

    def test_least_csv_string_input(self):
        result = tr("mssql").translate("LEAST", {"values": "a, b, c"}, "t")
        assert "IIF" in result
        assert "t.a" in result

    # --- Line 1317: TO_DATE with format param on postgres, mysql, oracle ---

    def test_to_date_with_format_postgres(self):
        result = tr("postgres").translate("TO_DATE", {"column": "data_str", "format": "YYYY-MM-DD"}, "t")
        assert "TO_DATE" in result
        assert "YYYY-MM-DD" in result

    def test_to_date_with_format_mysql(self):
        result = tr("mysql").translate("TO_DATE", {"column": "data_str", "format": "%Y-%m-%d"}, "t")
        assert "STR_TO_DATE" in result
        assert "%Y-%m-%d" in result

    def test_to_date_with_format_oracle(self):
        result = tr("oracle").translate("TO_DATE", {"column": "data_str", "format": "YYYY-MM-DD"}, "t")
        assert "TO_DATE" in result
        assert "YYYY-MM-DD" in result

    def test_to_date_with_format_mssql(self):
        result = tr("mssql").translate("TO_DATE", {"column": "data_str", "format": "YYYY-MM-DD"}, "t")
        assert "CONVERT(DATE" in result

    def test_to_date_with_format_sqlite(self):
        result = tr("sqlite").translate("TO_DATE", {"column": "data_str", "format": "YYYY-MM-DD"}, "t")
        assert "date(" in result


# =============================================================================
# TestCoverageGaps — copertura linee mancanti
# =============================================================================

class TestCoverageGaps:
    """Tests mirati per portare la copertura di sql_functions.py oltre il 90%."""

    # ── CONCAT con separatore su Oracle/SQLite (linee 857-865) ───────────

    def test_concat_separator_oracle_uses_pipe(self):
        result = tr("oracle").translate(
            "CONCAT", {"values": ["col1", "col2", "col3"], "separator": "-"}, "t"
        )
        assert "||" in result
        assert "'-'" in result

    def test_concat_separator_sqlite_uses_pipe(self):
        result = tr("sqlite").translate(
            "CONCAT", {"values": ["col1", "col2", "col3"], "separator": "-"}, "t"
        )
        assert "||" in result
        assert "'-'" in result

    def test_concat_no_separator_oracle_uses_pipe(self):
        result = tr("oracle").translate(
            "CONCAT", {"values": ["col1", "col2"], "separator": ""}, "t"
        )
        assert "||" in result
        assert "CONCAT" not in result

    def test_concat_no_separator_sqlite_uses_pipe(self):
        result = tr("sqlite").translate(
            "CONCAT", {"values": ["a", "b"], "separator": ""}, "t"
        )
        assert "||" in result

    # ── CONCAT con stringa CSV singola (linea 848) ───────────────────────

    def test_concat_csv_string_input(self):
        result = tr("postgres").translate(
            "CONCAT", {"values": "col1, col2, col3"}, "t"
        )
        assert "CONCAT(" in result
        assert "t.col1" in result
        assert "t.col2" in result
        assert "t.col3" in result

    def test_concat_csv_string_with_separator(self):
        """Linea 854: stringa CSV + separatore su postgres/mssql/mysql."""
        result = tr("postgres").translate(
            "CONCAT", {"values": "col1, col2", "separator": ";"}, "t"
        )
        assert "CONCAT_WS" in result
        assert "';'" in result

    # ── SUBSTRING senza length per ogni DB (linee 884-898) ───────────────

    def test_substring_no_length_postgres(self):
        result = tr("postgres").translate(
            "SUBSTRING", {"column": "nome", "start": 3}, "t"
        )
        assert "SUBSTRING(t.nome FROM 3)" == result

    def test_substring_no_length_mssql(self):
        result = tr("mssql").translate(
            "SUBSTRING", {"column": "nome", "start": 3}, "t"
        )
        assert "LEN(" in result
        assert "SUBSTRING(t.nome, 3, LEN(t.nome))" == result

    def test_substring_no_length_mysql(self):
        result = tr("mysql").translate(
            "SUBSTRING", {"column": "nome", "start": 3}, "t"
        )
        assert "SUBSTRING(t.nome, 3)" == result

    def test_substring_no_length_oracle(self):
        result = tr("oracle").translate(
            "SUBSTRING", {"column": "nome", "start": 3}, "t"
        )
        assert "SUBSTR(t.nome, 3)" == result

    def test_substring_no_length_sqlite(self):
        result = tr("sqlite").translate(
            "SUBSTRING", {"column": "nome", "start": 3}, "t"
        )
        assert "SUBSTR(t.nome, 3)" == result

    # ── MATH divisione zero-protection (linee 954-984) ───────────────────

    def test_math_division_postgres_nullif(self):
        result = tr("postgres").translate(
            "MATH", {"column1": "prezzo", "operator": "/", "value": 10}, "t"
        )
        assert "NULLIF" in result
        assert "t.prezzo" in result

    def test_math_division_mssql_case(self):
        result = tr("mssql").translate(
            "MATH", {"column1": "prezzo", "operator": "/", "value": 10}, "t"
        )
        assert "CASE WHEN" in result
        assert "THEN NULL" in result

    def test_math_division_mysql_if(self):
        result = tr("mysql").translate(
            "MATH", {"column1": "prezzo", "operator": "/", "value": 10}, "t"
        )
        assert "IF(" in result
        assert "NULL" in result

    def test_math_division_oracle_case(self):
        result = tr("oracle").translate(
            "MATH", {"column1": "prezzo", "operator": "/", "value": 10}, "t"
        )
        assert "CASE WHEN" in result

    def test_math_division_sqlite_nullif(self):
        result = tr("sqlite").translate(
            "MATH", {"column1": "prezzo", "operator": "/", "value": 10}, "t"
        )
        assert "NULLIF" in result

    def test_math_division_with_column2(self):
        """Usa column2 invece di value (linea 966-967)."""
        result = tr("postgres").translate(
            "MATH", {"column1": "prezzo", "operator": "/", "column2": "quantita"}, "t"
        )
        assert "NULLIF(t.quantita, 0)" in result
        assert "t.prezzo" in result

    def test_math_addition_with_column2(self):
        result = tr("postgres").translate(
            "MATH", {"column1": "a", "operator": "+", "column2": "b"}, "t"
        )
        assert result == "(t.a + t.b)"

    # ── PERIMETER su MSSQL e MySQL (linee 726-735) ───────────────────────

    def test_perimeter_mssql_stboundary(self):
        result = tr("mssql").translate(
            "PERIMETER", {"column": "geom"}, "t"
        )
        assert "STBoundary" in result
        assert "STLength" in result

    def test_perimeter_mysql_exterior_ring(self):
        result = tr("mysql").translate(
            "PERIMETER", {"column": "geom"}, "t"
        )
        assert "ST_ExteriorRing" in result
        assert "ST_Length" in result

    def test_perimeter_postgres(self):
        result = tr("postgres").translate(
            "PERIMETER", {"column": "geom"}, "t"
        )
        assert "ST_Perimeter" in result

    def test_perimeter_oracle(self):
        result = tr("oracle").translate(
            "PERIMETER", {"column": "geom"}, "t"
        )
        assert "SDO_LENGTH" in result

    def test_perimeter_sqlite(self):
        result = tr("sqlite").translate(
            "PERIMETER", {"column": "geom"}, "t"
        )
        assert "Perimeter(" in result

    # ── ROUND con decimals=None e decimals="" (linea 787) ────────────────

    def test_round_decimals_none_defaults_zero(self):
        result = tr("postgres").translate(
            "ROUND", {"column": "prezzo", "decimals": None}, "t"
        )
        assert "ROUND(t.prezzo::numeric, 0)" == result

    def test_round_decimals_empty_string_defaults_zero(self):
        result = tr("postgres").translate(
            "ROUND", {"column": "prezzo", "decimals": ""}, "t"
        )
        assert "ROUND(t.prezzo::numeric, 0)" == result

    def test_round_decimals_none_mysql(self):
        result = tr("mysql").translate(
            "ROUND", {"column": "prezzo", "decimals": None}, "t"
        )
        assert "ROUND(t.prezzo, 0)" == result

    # ── GREATEST con 3+ valori su MSSQL → nested IIF (linea 1026-1031) ──

    def test_greatest_mssql_three_values_nested_iif(self):
        result = tr("mssql").translate(
            "GREATEST", {"values": ["a", "b", "c"]}, "t"
        )
        assert "IIF(" in result
        # Nested IIF: outer IIF wraps inner IIF in both condition and true-branch
        assert result.count("IIF(") >= 2

    def test_greatest_mssql_two_values(self):
        result = tr("mssql").translate(
            "GREATEST", {"values": ["x", "y"]}, "t"
        )
        assert "IIF(t.x > t.y, t.x, t.y)" == result

    def test_greatest_postgres(self):
        result = tr("postgres").translate(
            "GREATEST", {"values": ["a", "b", "c"]}, "t"
        )
        assert "GREATEST(t.a, t.b, t.c)" == result

    def test_greatest_sqlite_uses_max(self):
        result = tr("sqlite").translate(
            "GREATEST", {"values": ["a", "b"]}, "t"
        )
        assert "MAX(" in result

    def test_greatest_csv_string_input(self):
        """values come stringa CSV (linea 1022-1023)."""
        result = tr("mssql").translate(
            "GREATEST", {"values": "a, b, c"}, "t"
        )
        assert "IIF(" in result
        assert result.count("IIF(") >= 2

    # ── LEAST su MSSQL con stringa CSV (linea 1040) ──────────────────────

    def test_least_mssql_csv_string(self):
        result = tr("mssql").translate(
            "LEAST", {"values": "x, y, z"}, "t"
        )
        assert "IIF(" in result
        assert result.count("IIF(") >= 2

    def test_least_sqlite_uses_min(self):
        result = tr("sqlite").translate(
            "LEAST", {"values": ["a", "b"]}, "t"
        )
        assert "MIN(" in result

    # ── TO_DATE con format su postgres, mysql, oracle (linea 1317) ───────

    def test_to_date_format_postgres(self):
        result = tr("postgres").translate(
            "TO_DATE", {"column": "data_str", "format": "DD/MM/YYYY"}, "t"
        )
        assert "TO_DATE(t.data_str, 'DD/MM/YYYY')" == result

    def test_to_date_format_mysql(self):
        result = tr("mysql").translate(
            "TO_DATE", {"column": "data_str", "format": "%d/%m/%Y"}, "t"
        )
        assert "STR_TO_DATE(t.data_str, '%d/%m/%Y')" == result

    def test_to_date_format_oracle(self):
        result = tr("oracle").translate(
            "TO_DATE", {"column": "data_str", "format": "DD-MON-YYYY"}, "t"
        )
        assert "TO_DATE(t.data_str, 'DD-MON-YYYY')" == result
