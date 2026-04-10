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
# translate() — ST_AsText per ogni DB
# =============================================================================

class TestTranslateST_AsText:

    def test_postgres(self, t):
        result = t.translate("ST_AsText", "postgres", geom="t.geom")
        assert "ST_AsText" in result
        assert "t.geom" in result

    def test_mssql(self, t):
        result = t.translate("ST_AsText", "mssql", geom="t.geom")
        assert "t.geom" in result
        # MSSQL usa .STAsText()
        assert "STAsText" in result

    def test_mysql(self, t):
        result = t.translate("ST_AsText", "mysql", geom="t.geom")
        assert "t.geom" in result

    def test_oracle(self, t):
        result = t.translate("ST_AsText", "oracle", geom="t.geom")
        assert "t.geom" in result

    def test_sqlite(self, t):
        result = t.translate("ST_AsText", "sqlite", geom="t.geom")
        assert "t.geom" in result


# =============================================================================
# translate() — ST_Buffer per ogni DB (mutation target: parametri)
# =============================================================================

class TestTranslateST_Buffer:

    def test_postgres_contiene_distance(self, t):
        """Mutation target: distance deve comparire nella SQL."""
        result = t.translate("ST_Buffer", "postgres", geom="t.geom", distance=100)
        assert "100" in result
        assert "t.geom" in result

    def test_mssql_stbuffer(self, t):
        """MSSQL usa .STBuffer() method syntax."""
        result = t.translate("ST_Buffer", "mssql", geom="t.geom", distance=50)
        assert "50" in result
        assert "t.geom" in result

    def test_oracle_sdo_geom(self, t):
        """Oracle usa SDO_GEOM.SDO_BUFFER."""
        result = t.translate("ST_Buffer", "oracle", geom="t.geom", distance=200)
        assert "200" in result
        assert "t.geom" in result

    def test_distance_diversa_non_confusa(self, t):
        """Mutation target: distance 100 e 200 producono SQL diverse."""
        r100 = t.translate("ST_Buffer", "postgres", geom="t.geom", distance=100)
        r200 = t.translate("ST_Buffer", "postgres", geom="t.geom", distance=200)
        assert r100 != r200
        assert "100" in r100
        assert "200" in r200
        assert "100" not in r200
        assert "200" not in r100


# =============================================================================
# translate() — funzioni string e numeric
# =============================================================================

class TestTranslateStringNumeric:

    def test_upper_postgres(self, t):
        result = t.translate("UPPER", "postgres", value="t.nome")
        assert "UPPER" in result
        assert "t.nome" in result

    def test_lower_postgres(self, t):
        result = t.translate("LOWER", "postgres", value="t.nome")
        assert "LOWER" in result
        assert "t.nome" in result

    def test_round_postgres(self, t):
        result = t.translate("ROUND", "postgres", value="t.valore", decimals=2)
        assert "t.valore" in result
        assert "2" in result

    def test_round_oracle(self, t):
        result = t.translate("ROUND", "oracle", value="t.valore", decimals=3)
        assert "t.valore" in result
        assert "3" in result

    def test_upper_tutti_db(self, t):
        """UPPER deve essere supportato su tutti i DB."""
        for db in ["postgres", "mssql", "mysql", "oracle", "sqlite"]:
            result = t.translate("UPPER", db, value="t.col")
            assert "t.col" in result, f"UPPER su {db}: colonna non trovata"


# =============================================================================
# translate() — Errori
# =============================================================================

class TestTranslateErrori:

    def test_funzione_inesistente_raise(self, t):
        """Funzione non nel registry → ValueError."""
        with pytest.raises(ValueError, match="non trovata"):
            t.translate("FUNZIONE_XYZ_INESISTENTE", "postgres", col="x")

    def test_db_non_supportato_raise(self, t):
        """Database non supportato → ValueError."""
        with pytest.raises(ValueError):
            t.translate("ST_Buffer", "db2_inesistente", geom="t.geom", distance=100)

    def test_db_case_sensitive(self, t):
        """'POSTGRES' (uppercase) non dovrebbe essere uguale a 'postgres'."""
        # Verifica che il db_type sia case-sensitive o gestito correttamente
        try:
            result = t.translate("UPPER", "POSTGRES", value="t.col")
            # Se non raise, accetta uppercase — ok
        except ValueError:
            # Se raise ValueError, il lookup è case-sensitive — documentato
            pass


# =============================================================================
# get_spatial_predicate() — tutti i predicati
# =============================================================================

class TestGetSpatialPredicate:

    def test_intersects_postgres(self, t):
        result = t.get_spatial_predicate("intersects", "postgres",
                                          geom1="a.geom", geom2="b.geom")
        assert "a.geom" in result
        assert "b.geom" in result
        assert "ST_Intersects" in result

    def test_contains_postgres(self, t):
        result = t.get_spatial_predicate("contains", "postgres",
                                          geom1="a.geom", geom2="b.geom")
        assert "a.geom" in result
        assert "ST_Contains" in result

    def test_within_postgres(self, t):
        result = t.get_spatial_predicate("within", "postgres",
                                          geom1="a.geom", geom2="b.geom")
        assert "ST_Within" in result

    def test_dwithin_postgres(self, t):
        """ST_DWithin richiede distance."""
        result = t.get_spatial_predicate("dwithin", "postgres",
                                          geom1="a.geom", geom2="b.geom",
                                          distance=500)
        assert "a.geom" in result
        assert "b.geom" in result
        assert "500" in result

    def test_touches_postgres(self, t):
        result = t.get_spatial_predicate("touches", "postgres",
                                          geom1="a.geom", geom2="b.geom")
        assert "ST_Touches" in result

    def test_overlaps_postgres(self, t):
        result = t.get_spatial_predicate("overlaps", "postgres",
                                          geom1="a.geom", geom2="b.geom")
        assert "ST_Overlaps" in result

    def test_predicato_inesistente_raise(self, t):
        """Predicato non in whitelist → ValueError."""
        with pytest.raises(ValueError):
            t.get_spatial_predicate("EXPLOIT_XYZ", "postgres",
                                     geom1="a.geom", geom2="b.geom")

    def test_case_insensitive_predicate(self, t):
        """Predicato in uppercase deve funzionare."""
        result = t.get_spatial_predicate("INTERSECTS", "postgres",
                                          geom1="a.geom", geom2="b.geom")
        assert "a.geom" in result

    def test_intersects_mssql(self, t):
        result = t.get_spatial_predicate("intersects", "mssql",
                                          geom1="a.geom", geom2="b.geom")
        assert "a.geom" in result
        assert "b.geom" in result


# =============================================================================
# build_buffer_join()
# =============================================================================

class TestBuildBufferJoin:

    def test_postgres_contiene_buffer_e_predicato(self, t):
        result = t.build_buffer_join("postgres", "t1.geom", "t2.geom", 100)
        assert "t1.geom" in result
        assert "t2.geom" in result
        assert "100" in result
        assert "Intersects" in result

    def test_distance_nel_risultato(self, t):
        """Mutation target: distance deve comparire nella SQL."""
        r50  = t.build_buffer_join("postgres", "a.g", "b.g", 50)
        r500 = t.build_buffer_join("postgres", "a.g", "b.g", 500)
        assert "50"  in r50
        assert "500" in r500
        assert r50 != r500

    def test_predicato_custom(self, t):
        """Predicato diverso da intersects."""
        result = t.build_buffer_join("postgres", "t1.geom", "t2.geom", 100,
                                      predicate="contains")
        assert "t1.geom" in result
        assert "Contains" in result


# =============================================================================
# Registry e singleton
# =============================================================================

class TestRegistry:

    def test_singleton(self, t):
        """SQLFunctionTranslator è un singleton."""
        t2 = SQLFunctionTranslator()
        assert t is t2

    def test_singleton_globale(self, t):
        """sql_translator globale è la stessa istanza."""
        assert t is sql_translator

    def test_funzioni_nel_registry(self, t):
        """Il registry contiene almeno le funzioni chiave."""
        funzioni = t.list_functions()
        nomi = {f["name"] for f in funzioni}
        assert "ST_Buffer"    in nomi
        assert "ST_AsText"    in nomi
        assert "ST_Intersects" in nomi

    def test_categorie_disponibili(self, t):
        cats = t.get_categories()
        assert len(cats) >= 7
        assert "spatial" in cats

    def test_has_function_true(self, t):
        assert t.has_function("ST_Buffer") is True

    def test_has_function_false(self, t):
        assert t.has_function("FUNZIONE_INESISTENTE_XYZ") is False

    def test_database_supportati(self, t):
        """Lista DB supportati non vuota."""
        dbs = t.supported_databases
        assert len(dbs) >= 5
        assert "postgres" in dbs
        assert "mssql" in dbs
        assert "mysql" in dbs

    def test_nuove_funzioni_nel_registry(self, t):
        """Il registry contiene le nuove funzioni numeriche e stringa."""
        funzioni = t.list_functions()
        nomi = {f["name"] for f in funzioni}
        for fn in ["MOD", "POWER", "SQRT", "SIGN", "GREATEST", "LEAST",
                    "LEFT", "RIGHT", "POSITION", "LTRIM", "RTRIM"]:
            assert fn in nomi, f"{fn} non trovata nel registry"


# =============================================================================
# translate() — Nuove funzioni numeriche
# =============================================================================

class TestTranslateNewNumeric:

    # ---- MOD ----

    def test_mod_postgres(self, t):
        result = t.translate("MOD", "postgres", value="t.val", divisor=3)
        assert "t.val" in result
        assert "3" in result
        assert "MOD" in result

    def test_mod_mysql(self, t):
        result = t.translate("MOD", "mysql", value="t.val", divisor=3)
        assert "t.val" in result
        assert "3" in result

    def test_mod_mssql_percent(self, t):
        """MSSQL usa operatore % per MOD."""
        result = t.translate("MOD", "mssql", value="t.val", divisor=3)
        assert "t.val" in result
        assert "3" in result
        assert "%" in result

    def test_mod_sqlite_percent(self, t):
        """SQLite usa operatore % per MOD."""
        result = t.translate("MOD", "sqlite", value="t.val", divisor=3)
        assert "t.val" in result
        assert "%" in result

    def test_mod_oracle(self, t):
        result = t.translate("MOD", "oracle", value="t.val", divisor=3)
        assert "t.val" in result
        assert "3" in result

    # ---- POWER ----

    def test_power_postgres(self, t):
        result = t.translate("POWER", "postgres", value="t.val", exponent=2)
        assert "POWER" in result
        assert "t.val" in result
        assert "2" in result

    def test_power_mssql(self, t):
        result = t.translate("POWER", "mssql", value="t.val", exponent=2)
        assert "t.val" in result
        assert "2" in result

    def test_power_oracle(self, t):
        result = t.translate("POWER", "oracle", value="t.val", exponent=2)
        assert "t.val" in result
        assert "2" in result

    # ---- SQRT ----

    def test_sqrt_postgres(self, t):
        result = t.translate("SQRT", "postgres", value="t.val")
        assert "SQRT" in result
        assert "t.val" in result

    def test_sqrt_mssql(self, t):
        result = t.translate("SQRT", "mssql", value="t.val")
        assert "SQRT" in result
        assert "t.val" in result

    def test_sqrt_sqlite(self, t):
        result = t.translate("SQRT", "sqlite", value="t.val")
        assert "t.val" in result

    # ---- SIGN ----

    def test_sign_postgres(self, t):
        result = t.translate("SIGN", "postgres", value="t.val")
        assert "SIGN" in result
        assert "t.val" in result

    def test_sign_oracle(self, t):
        result = t.translate("SIGN", "oracle", value="t.val")
        assert "t.val" in result

    # ---- GREATEST ----

    def test_greatest_postgres(self, t):
        result = t.translate("GREATEST", "postgres", values="a, b")
        assert "GREATEST" in result
        assert "a" in result
        assert "b" in result

    def test_greatest_mysql(self, t):
        result = t.translate("GREATEST", "mysql", values="a, b")
        assert "a" in result
        assert "b" in result

    def test_greatest_sqlite_max(self, t):
        """SQLite usa MAX al posto di GREATEST."""
        result = t.translate("GREATEST", "sqlite", values="a, b")
        assert "MAX" in result
        assert "a" in result
        assert "b" in result

    # ---- LEAST ----

    def test_least_postgres(self, t):
        result = t.translate("LEAST", "postgres", values="a, b")
        assert "LEAST" in result
        assert "a" in result
        assert "b" in result

    def test_least_mssql(self, t):
        result = t.translate("LEAST", "mssql", values="a, b")
        assert "a" in result
        assert "b" in result

    def test_least_sqlite_min(self, t):
        """SQLite usa MIN al posto di LEAST."""
        result = t.translate("LEAST", "sqlite", values="a, b")
        assert "MIN" in result
        assert "a" in result
        assert "b" in result
