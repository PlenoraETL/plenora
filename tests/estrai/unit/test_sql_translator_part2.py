"""
tests/estrai/unit/test_sql_translator.py
=========================================
Unit test su SQLFunctionTranslator.

NON richiede DB, API o Celery.
Testa la traduzione di funzioni SQL dal formato astratto al dialetto
specifico di ogni database.

Obiettivi coverage:
  - translate(): ogni db_type per ST_AsText, ST_Buffer, UPPER, ROUND
  - get_spatial_predicate(): ogni predicato spaziale
  - build_buffer_join(): combinazione buffer + predicato
  - Errori: funzione inesistente, db non supportato, predicato invalido
  - Mutation score: assert sui valori esatti delle stringhe SQL prodotte
"""

import pytest

pytestmark = pytest.mark.unit

from app.db.sql_translator import SQLFunctionTranslator, sql_translator


# =============================================================================
# Setup
# =============================================================================

@pytest.fixture
def t():
    return SQLFunctionTranslator()




# =============================================================================
# translate() — Nuove funzioni stringa
# =============================================================================

class TestTranslateNewString:

    # ---- LEFT ----

    def test_left_postgres(self, t):
        result = t.translate("LEFT", "postgres", value="t.nome", length=5)
        assert "LEFT" in result
        assert "t.nome" in result
        assert "5" in result

    def test_left_mssql(self, t):
        result = t.translate("LEFT", "mssql", value="t.nome", length=5)
        assert "LEFT" in result
        assert "t.nome" in result
        assert "5" in result

    def test_left_oracle_substr(self, t):
        """Oracle usa SUBSTR(value, 1, length) per LEFT."""
        result = t.translate("LEFT", "oracle", value="t.nome", length=5)
        assert "SUBSTR" in result
        assert "t.nome" in result
        assert "5" in result

    def test_left_sqlite_substr(self, t):
        """SQLite usa SUBSTR(value, 1, length) per LEFT."""
        result = t.translate("LEFT", "sqlite", value="t.nome", length=5)
        assert "SUBSTR" in result
        assert "t.nome" in result

    # ---- RIGHT ----

    def test_right_postgres(self, t):
        result = t.translate("RIGHT", "postgres", value="t.nome", length=3)
        assert "RIGHT" in result
        assert "t.nome" in result
        assert "3" in result

    def test_right_mssql(self, t):
        result = t.translate("RIGHT", "mssql", value="t.nome", length=3)
        assert "RIGHT" in result
        assert "t.nome" in result

    def test_right_oracle_substr(self, t):
        """Oracle usa SUBSTR(value, -length) per RIGHT."""
        result = t.translate("RIGHT", "oracle", value="t.nome", length=3)
        assert "SUBSTR" in result
        assert "t.nome" in result
        assert "-3" in result

    def test_right_sqlite_substr(self, t):
        """SQLite usa SUBSTR(value, -length) per RIGHT."""
        result = t.translate("RIGHT", "sqlite", value="t.nome", length=3)
        assert "SUBSTR" in result
        assert "t.nome" in result

    # ---- POSITION ----

    def test_position_postgres(self, t):
        """PG usa POSITION(search IN value)."""
        result = t.translate("POSITION", "postgres", search="abc", value="t.nome")
        assert "t.nome" in result
        assert "abc" in result
        assert "POSITION" in result

    def test_position_mssql_charindex(self, t):
        """MSSQL usa CHARINDEX(search, value)."""
        result = t.translate("POSITION", "mssql", search="abc", value="t.nome")
        assert "CHARINDEX" in result
        assert "abc" in result
        assert "t.nome" in result

    def test_position_mysql_locate(self, t):
        """MySQL usa LOCATE(search, value)."""
        result = t.translate("POSITION", "mysql", search="abc", value="t.nome")
        assert "LOCATE" in result
        assert "abc" in result
        assert "t.nome" in result

    def test_position_oracle_instr(self, t):
        """Oracle usa INSTR(value, search)."""
        result = t.translate("POSITION", "oracle", search="abc", value="t.nome")
        assert "INSTR" in result
        assert "abc" in result
        assert "t.nome" in result

    def test_position_sqlite_instr(self, t):
        """SQLite usa INSTR(value, search)."""
        result = t.translate("POSITION", "sqlite", search="abc", value="t.nome")
        assert "INSTR" in result
        assert "abc" in result
        assert "t.nome" in result

    # ---- LTRIM ----

    def test_ltrim_postgres(self, t):
        result = t.translate("LTRIM", "postgres", value="t.nome")
        assert "LTRIM" in result
        assert "t.nome" in result

    def test_ltrim_mssql(self, t):
        result = t.translate("LTRIM", "mssql", value="t.nome")
        assert "LTRIM" in result
        assert "t.nome" in result

    def test_ltrim_tutti_db(self, t):
        """LTRIM deve essere supportato su tutti i DB."""
        for db in ["postgres", "mssql", "mysql", "oracle", "sqlite"]:
            result = t.translate("LTRIM", db, value="t.nome")
            assert "t.nome" in result, f"LTRIM su {db}: colonna non trovata"

    # ---- RTRIM ----

    def test_rtrim_postgres(self, t):
        result = t.translate("RTRIM", "postgres", value="t.nome")
        assert "RTRIM" in result
        assert "t.nome" in result

    def test_rtrim_oracle(self, t):
        result = t.translate("RTRIM", "oracle", value="t.nome")
        assert "RTRIM" in result
        assert "t.nome" in result

    def test_rtrim_tutti_db(self, t):
        """RTRIM deve essere supportato su tutti i DB."""
        for db in ["postgres", "mssql", "mysql", "oracle", "sqlite"]:
            result = t.translate("RTRIM", db, value="t.nome")
            assert "t.nome" in result, f"RTRIM su {db}: colonna non trovata"


# =============================================================================
# translate() — DATE_TRUNC e DATE_FORMAT (registry)
# =============================================================================

class TestTranslateDateTruncFormatRegistry:

    def test_date_trunc_nel_registry(self, t):
        assert t.has_function("DATE_TRUNC") is True

    def test_date_format_nel_registry(self, t):
        assert t.has_function("DATE_FORMAT") is True

    def test_date_trunc_postgres(self, t):
        result = t.translate("DATE_TRUNC", "postgres", unit="month", date="t.data")
        assert "DATE_TRUNC" in result
        assert "t.data" in result
        assert "month" in result

    def test_date_trunc_mssql(self, t):
        result = t.translate("DATE_TRUNC", "mssql", unit="year", date="t.data")
        assert "DATETRUNC" in result
        assert "t.data" in result

    def test_date_trunc_oracle(self, t):
        result = t.translate("DATE_TRUNC", "oracle", unit="month", date="t.data")
        assert "TRUNC" in result
        assert "t.data" in result

    def test_date_trunc_sqlite(self, t):
        result = t.translate("DATE_TRUNC", "sqlite", unit="month", date="t.data")
        assert "date(" in result
        assert "t.data" in result

    def test_date_trunc_tutti_db(self, t):
        for db in ["postgres", "mssql", "mysql", "oracle", "sqlite"]:
            result = t.translate("DATE_TRUNC", db, unit="month", date="t.data")
            assert "t.data" in result, f"DATE_TRUNC su {db}: colonna mancante"

    def test_date_format_postgres(self, t):
        result = t.translate("DATE_FORMAT", "postgres", date="t.data", format="DD/MM/YYYY")
        assert "TO_CHAR" in result
        assert "t.data" in result

    def test_date_format_mssql(self, t):
        result = t.translate("DATE_FORMAT", "mssql", date="t.data", format="dd/MM/yyyy")
        assert "FORMAT" in result
        assert "t.data" in result

    def test_date_format_mysql(self, t):
        result = t.translate("DATE_FORMAT", "mysql", date="t.data", format="%d/%m/%Y")
        assert "DATE_FORMAT" in result
        assert "t.data" in result

    def test_date_format_oracle(self, t):
        result = t.translate("DATE_FORMAT", "oracle", date="t.data", format="DD/MM/YYYY")
        assert "TO_CHAR" in result
        assert "t.data" in result

    def test_date_format_sqlite(self, t):
        result = t.translate("DATE_FORMAT", "sqlite", date="t.data", format="%d/%m/%Y")
        assert "STRFTIME" in result
        assert "t.data" in result

    def test_date_format_tutti_db(self, t):
        for db in ["postgres", "mssql", "mysql", "oracle", "sqlite"]:
            result = t.translate("DATE_FORMAT", db, date="t.data", format="YYYY-MM-DD")
            assert "t.data" in result, f"DATE_FORMAT su {db}: colonna mancante"
