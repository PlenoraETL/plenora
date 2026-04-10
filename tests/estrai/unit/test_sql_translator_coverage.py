"""
tests/estrai/unit/test_sql_translator_coverage.py
===================================================
Test mirati a coprire le righe mancanti in sql_translator.py (coverage 55%).

Zona coperta:
  - _load_registry: FileNotFoundError e JSONDecodeError (registry corrotto)
  - _conta_funzioni()
  - list_functions() con filtro categoria
  - supports_database()
  - _applica_template(): parametri lista, separatore custom {:join:SEP}
  - translate_expression(): traduzione espressioni SQL testuali
  - _parsa_argomenti_funzione(): parentesi annidate
  - _get_all_function_names(): ordinamento per lunghezza
  - Funzioni shortcut: translate_sql_function, get_spatial_condition, list_sql_functions
"""

import pytest
import json
from unittest.mock import patch, MagicMock
from pathlib import Path

pytestmark = pytest.mark.unit

from app.db.sql_translator import (
    SQLFunctionTranslator,
    sql_translator,
    translate_sql_function,
    get_spatial_condition,
    list_sql_functions,
)


# ─── Helper: resetta il singleton per i test che ne hanno bisogno ─────────────

def fresh_translator():
    """Crea una nuova istanza NON singleton per test di _load_registry."""
    instance = object.__new__(SQLFunctionTranslator)
    return instance


# =============================================================================
# _load_registry — percorsi di errore
# =============================================================================

class TestLoadRegistry:

    def test_registry_non_trovato_fallback_vuoto(self):
        """FileNotFoundError → registry inizializzato vuoto, no crash."""
        instance = fresh_translator()
        with patch("builtins.open", side_effect=FileNotFoundError("file not found")):
            instance._load_registry()
        assert instance._registry == {
            "_meta": {}, "spatial": {}, "string": {}, "numeric": {}, "date": {}
        }

    def test_registry_json_corrotto_fallback_vuoto(self, caplog):
        """JSONDecodeError → registry inizializzato vuoto, no crash."""
        import logging
        instance = fresh_translator()
        with caplog.at_level(logging.CRITICAL):  # sopprime ERROR atteso
            with patch("builtins.open", MagicMock()), \
                 patch("json.load", side_effect=json.JSONDecodeError("err", "", 0)):
                instance._load_registry()
        assert instance._registry == {
            "_meta": {}, "spatial": {}, "string": {}, "numeric": {}, "date": {}
        }


# =============================================================================
# _conta_funzioni
# =============================================================================

class TestContaFunzioni:

    def test_conta_funzioni_non_zero(self):
        """Il registry ha almeno qualche funzione."""
        t = SQLFunctionTranslator()
        count = t._conta_funzioni()
        assert count > 0

    def test_conta_funzioni_registry_vuoto(self):
        """Registry vuoto → 0 funzioni."""
        instance = fresh_translator()
        instance._registry = {"_meta": {}, "spatial": {}, "string": {}}
        assert instance._conta_funzioni() == 0

    def test_conta_funzioni_ignora_meta(self):
        """_meta non conta come categoria funzioni."""
        instance = fresh_translator()
        instance._registry = {
            "_meta": {"version": "1.0"},
            "spatial": {"ST_Buffer": {}, "ST_Intersects": {}},
            "string": {"UPPER": {}},
        }
        assert instance._conta_funzioni() == 3


# =============================================================================
# list_functions con filtro categoria
# =============================================================================

class TestListFunctions:

    def test_list_all(self):
        t = SQLFunctionTranslator()
        funcs = t.list_functions()
        assert len(funcs) > 0

    def test_list_spatial_only(self):
        t = SQLFunctionTranslator()
        spatial = t.list_functions(category="spatial")
        assert len(spatial) > 0
        for f in spatial:
            assert f["category"] == "spatial"

    def test_list_categoria_inesistente_vuota(self):
        t = SQLFunctionTranslator()
        result = t.list_functions(category="categoria_inesistente_xyz")
        assert result == []

    def test_list_string_only(self):
        t = SQLFunctionTranslator()
        string_funcs = t.list_functions(category="string")
        assert len(string_funcs) > 0
        for f in string_funcs:
            assert f["category"] == "string"


# =============================================================================
# supports_database
# =============================================================================

class TestSupportsDatabase:

    def test_st_buffer_postgres_supportato(self):
        t = SQLFunctionTranslator()
        assert t.supports_database("ST_Buffer", "postgres") is True

    def test_st_buffer_db_inesistente_non_supportato(self):
        t = SQLFunctionTranslator()
        assert t.supports_database("ST_Buffer", "db2_inesistente") is False

    def test_funzione_inesistente_non_supportata(self):
        t = SQLFunctionTranslator()
        assert t.supports_database("FUNZIONE_XYZ", "postgres") is False

    def test_tutti_db_supportati_da_st_buffer(self):
        """ST_Buffer deve essere supportato su tutti i DB principali."""
        t = SQLFunctionTranslator()
        for db in ["postgres", "mssql", "mysql", "oracle", "sqlite"]:
            assert t.supports_database("ST_Buffer", db) is True, \
                f"ST_Buffer non supportato su {db}"


# =============================================================================
# _applica_template — parametri lista e separatore custom
# =============================================================================

class TestApplicaTemplate:

    @pytest.fixture
    def t(self):
        return SQLFunctionTranslator()

    def test_template_semplice(self, t):
        result = t._applica_template("UPPER({value})", {"value": "t.col"})
        assert result == "UPPER(t.col)"

    def test_template_lista_join_standard(self, t):
        """Lista → join con virgola."""
        result = t._applica_template(
            "CONCAT({values})",
            {"values": ["col1", "col2", "col3"]}
        )
        assert "col1" in result
        assert "col2" in result
        assert "col3" in result

    def test_template_lista_join_separatore_custom(self, t):
        """Lista con {key:join:SEP} → join con separatore custom."""
        result = t._applica_template(
            "CONCAT({values:join: || })",
            {"values": ["col1", "col2", "col3"]}
        )
        assert " || " in result
        assert "col1" in result
        assert "col3" in result

    def test_template_parametri_multipli(self, t):
        result = t._applica_template(
            "ROUND({value}, {decimals})",
            {"value": "t.col", "decimals": 2}
        )
        assert "t.col" in result
        assert "2" in result

    def test_template_parametro_non_presente_invariato(self, t):
        """Parametro assente → placeholder non sostituito."""
        result = t._applica_template("UPPER({value})", {})
        assert "{value}" in result


# =============================================================================
# _parsa_argomenti_funzione
# =============================================================================

class TestParsaArgomenti:

    @pytest.fixture
    def t(self):
        return SQLFunctionTranslator()

    def test_argomenti_semplici(self, t):
        result = t._parsa_argomenti_funzione("a, b, c")
        assert result == ["a", "b", "c"]

    def test_argomento_singolo(self, t):
        result = t._parsa_argomenti_funzione("geom")
        assert result == ["geom"]

    def test_parentesi_annidate(self, t):
        """Virgola dentro parentesi non è separatore."""
        result = t._parsa_argomenti_funzione("ST_Buffer(geom, 100), 'text'")
        assert len(result) == 2
        assert "ST_Buffer(geom, 100)" in result

    def test_parentesi_doppie_annidate(self, t):
        result = t._parsa_argomenti_funzione("ST_Area(ST_Buffer(geom, 100))")
        assert len(result) == 1

    def test_stringa_vuota(self, t):
        result = t._parsa_argomenti_funzione("")
        assert result == []


# =============================================================================
# _get_all_function_names — ordinamento per lunghezza
# =============================================================================

class TestGetAllFunctionNames:

    def test_restituisce_lista_non_vuota(self):
        t = SQLFunctionTranslator()
        names = t._get_all_function_names()
        assert len(names) > 0

    def test_ordinati_per_lunghezza_decrescente(self):
        """Mutation target: i nomi più lunghi devono venire per primi."""
        t = SQLFunctionTranslator()
        names = t._get_all_function_names()
        for i in range(len(names) - 1):
            assert len(names[i]) >= len(names[i + 1]), \
                f"Ordine sbagliato: '{names[i]}' ({len(names[i])}) prima di '{names[i+1]}' ({len(names[i+1])})"

    def test_contiene_st_buffer(self):
        t = SQLFunctionTranslator()
        names = t._get_all_function_names()
        assert "ST_Buffer" in names


# =============================================================================
# translate_expression
# =============================================================================

class TestTranslateExpression:

    def test_traduzione_funzione_semplice(self):
        t = SQLFunctionTranslator()
        result = t.translate_expression("UPPER(t.col)", "postgres")
        assert "t.col" in result
        assert "UPPER" in result or "upper" in result.lower()

    def test_espressione_senza_funzioni_invariata(self):
        """Espressione senza funzioni del registry → invariata."""
        t = SQLFunctionTranslator()
        expr = "t.id = 1"
        result = t.translate_expression(expr, "postgres")
        assert "t.id" in result
        assert "= 1" in result

    def test_traduzione_con_column_map(self):
        """column_map rimappa i nomi colonna nell'espressione."""
        t = SQLFunctionTranslator()
        result = t.translate_expression(
            "t.old_col",
            "postgres",
            column_map={"old_col": "new_col"}
        )
        assert "new_col" in result
        assert "old_col" not in result


# =============================================================================
# Funzioni shortcut
# =============================================================================

class TestShortcutFunctions:

    def test_translate_sql_function(self):
        result = translate_sql_function("UPPER", "postgres", value="t.col")
        assert "t.col" in result
        assert "upper" in result.lower() or "UPPER" in result

    def test_get_spatial_condition(self):
        result = get_spatial_condition(
            "intersects", "postgres",
            geom1="a.geom", geom2="b.geom"
        )
        assert "a.geom" in result
        assert "b.geom" in result

    def test_list_sql_functions_tutte(self):
        funcs = list_sql_functions()
        assert len(funcs) > 0

    def test_list_sql_functions_con_categoria(self):
        funcs = list_sql_functions(category="spatial")
        assert len(funcs) > 0
        for f in funcs:
            assert f["category"] == "spatial"

    def test_translate_sql_function_funzione_inesistente(self):
        with pytest.raises(ValueError):
            translate_sql_function("FUNZIONE_XYZ_INESISTENTE", "postgres", value="x")

    def test_get_spatial_condition_predicato_inesistente(self):
        with pytest.raises(ValueError):
            get_spatial_condition("PREDICATO_XYZ", "postgres",
                                   geom1="a.g", geom2="b.g")
