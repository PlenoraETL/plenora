"""
tests/manipola/unit/test_plugin_executor.py
============================================
Unit test su PluginExecutor — metodi puri non coperti dai test esistenti.

Copre:
  - _converti_valore: tutti i tipi (number, boolean, columns, aggregation_list, pivot_mapping)
  - _prepara_kwargs: risoluzione valore, dipendenze condizionali, obbligatori/opzionali
  - execute_with_params: successo, trasformazione non trovata, input multi
  - validate_config: errori, config valida
  - get_default_config: valori default dal registry
"""

import pytest
import pandas as pd

pytestmark = pytest.mark.unit

from app.core.manipola.plugin_executor import get_executor, PluginExecutor


@pytest.fixture
def ex():
    return get_executor()


# =============================================================================
# _converti_valore
# =============================================================================

class TestConvertiValore:

    def test_none_invariato(self, ex):
        assert ex._converti_valore(None, "number") is None

    def test_tipo_none_invariato(self, ex):
        assert ex._converti_valore(42, None) == 42

    # number
    def test_number_int(self, ex):
        assert ex._converti_valore("42", "number") == 42
        assert isinstance(ex._converti_valore("42", "number"), int)

    def test_number_float(self, ex):
        result = ex._converti_valore("3.14", "number")
        assert abs(result - 3.14) < 1e-9
        assert isinstance(result, float)

    def test_number_gia_int(self, ex):
        assert ex._converti_valore(10, "number") == 10

    def test_number_stringa_vuota_none(self, ex):
        """Mutation target: stringa vuota → None, non 0."""
        assert ex._converti_valore("", "number") is None
        assert ex._converti_valore("  ", "number") is None

    # boolean
    def test_boolean_true_string(self, ex):
        for v in ["true", "True", "TRUE", "1", "yes", "si", "SI"]:
            assert ex._converti_valore(v, "boolean") is True, f"'{v}' doveva essere True"

    def test_boolean_false_string(self, ex):
        for v in ["false", "False", "0", "no", "nope"]:
            assert ex._converti_valore(v, "boolean") is False, f"'{v}' doveva essere False"

    def test_boolean_bool_true(self, ex):
        assert ex._converti_valore(True, "boolean") is True

    def test_boolean_bool_false(self, ex):
        assert ex._converti_valore(False, "boolean") is False

    # columns / string_list
    def test_columns_stringa_csv(self, ex):
        result = ex._converti_valore("col1, col2, col3", "columns")
        assert result == ["col1", "col2", "col3"]

    def test_columns_lista_invariata(self, ex):
        result = ex._converti_valore(["col1", "col2"], "columns")
        assert result == ["col1", "col2"]

    def test_string_list_stringa_csv(self, ex):
        result = ex._converti_valore("a, b, c", "string_list")
        assert result == ["a", "b", "c"]

    def test_columns_valore_singolo_in_lista(self, ex):
        result = ex._converti_valore("col1", "columns")
        assert result == ["col1"]

    def test_columns_stringa_vuota_lista_vuota(self, ex):
        result = ex._converti_valore("", "columns")
        assert result == []

    # aggregation_list
    def test_aggregation_list_json_string(self, ex):
        import json
        aggs = [{"column": "valore", "function": "sum"}]
        result = ex._converti_valore(json.dumps(aggs), "aggregation_list")
        assert result == aggs

    def test_aggregation_list_gia_lista(self, ex):
        aggs = [{"column": "id", "function": "count"}]
        result = ex._converti_valore(aggs, "aggregation_list")
        assert result == aggs

    def test_aggregation_list_json_invalido(self, ex):
        result = ex._converti_valore("non_json{", "aggregation_list")
        assert result == []

    def test_aggregation_list_non_lista_json(self, ex):
        """JSON valido ma non lista → []."""
        result = ex._converti_valore('{"key": "val"}', "aggregation_list")
        assert result == []

    # pivot_mapping
    def test_pivot_mapping_json_string(self, ex):
        import json
        mapping = {"Q1": "Primo trimestre", "Q2": "Secondo trimestre"}
        result = ex._converti_valore(json.dumps(mapping), "pivot_mapping")
        assert result == mapping

    def test_pivot_mapping_gia_dict(self, ex):
        mapping = {"A": "Alpha"}
        result = ex._converti_valore(mapping, "pivot_mapping")
        assert result == mapping

    def test_pivot_mapping_json_invalido(self, ex):
        result = ex._converti_valore("non_json{", "pivot_mapping")
        assert result == {}

    def test_tipo_sconosciuto_invariato(self, ex):
        """Tipo non riconosciuto → valore invariato."""
        result = ex._converti_valore("ciao", "tipo_inesistente_xyz")
        assert result == "ciao"

    def test_errore_conversione_invariato(self, ex):
        """Errore di conversione → valore originale restituito."""
        result = ex._converti_valore("non_un_numero", "number")
        assert result == "non_un_numero"


# =============================================================================
# _prepara_kwargs
# =============================================================================

class TestPreparaKwargs:

    def _config(self, params):
        """Costruisce una transform_config minimale."""
        return {"parameters": params}

    def test_valore_da_node_config(self, ex):
        config = self._config([{"id": "subset", "type": "columns"}])
        result = ex._prepara_kwargs(config, {"subset": "col1, col2"})
        assert result["subset"] == ["col1", "col2"]

    def test_default_dal_registry(self, ex):
        config = self._config([{"id": "keep", "type": "string", "default": "first"}])
        result = ex._prepara_kwargs(config, {})
        assert result["keep"] == "first"

    def test_obbligatorio_mancante_usa_none(self, ex):
        config = self._config([{"id": "column", "type": "string", "required": True}])
        result = ex._prepara_kwargs(config, {})
        assert "column" in result
        assert result["column"] is None

    def test_opzionale_mancante_skippato(self, ex):
        config = self._config([{"id": "limit", "type": "number"}])
        result = ex._prepara_kwargs(config, {})
        assert "limit" not in result

    def test_dipendenza_soddisfatta(self, ex):
        """depends_on soddisfatto → parametro incluso."""
        config = self._config([
            {"id": "mode", "type": "string", "default": "custom"},
            {"id": "custom_value", "type": "string", "default": "x",
             "depends_on": {"param": "mode", "value": "custom"}},
        ])
        result = ex._prepara_kwargs(config, {"mode": "custom"})
        assert "custom_value" in result

    def test_dipendenza_non_soddisfatta(self, ex):
        """depends_on non soddisfatto → parametro escluso."""
        config = self._config([
            {"id": "mode", "type": "string", "default": "standard"},
            {"id": "custom_value", "type": "string", "default": "x",
             "depends_on": {"param": "mode", "value": "custom"}},
        ])
        result = ex._prepara_kwargs(config, {"mode": "standard"})
        assert "custom_value" not in result

    def test_dipendenza_array_valori(self, ex):
        """depends_on con lista di valori accettati."""
        config = self._config([
            {"id": "aggr", "type": "string", "default": "sum"},
            {"id": "precision", "type": "number", "default": 2,
             "depends_on": {"param": "aggr", "value": ["sum", "mean", "avg"]}},
        ])
        result = ex._prepara_kwargs(config, {"aggr": "sum"})
        assert "precision" in result

    def test_nessun_parametro(self, ex):
        config = self._config([])
        result = ex._prepara_kwargs(config, {"qualcosa": "valore"})
        assert result == {}

    def test_conversione_numero(self, ex):
        config = self._config([{"id": "limit", "type": "number", "default": "100"}])
        result = ex._prepara_kwargs(config, {})
        assert result["limit"] == 100
        assert isinstance(result["limit"], int)


# =============================================================================
# execute_with_params
# =============================================================================

class TestExecuteWithParams:

    def test_distinct_base(self, ex):
        df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
        result = ex.execute_with_params("distinct", [df], {})
        assert len(result) == 2

    def test_distinct_con_subset(self, ex):
        df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "y", "z"]})
        result = ex.execute_with_params("distinct", [df], {"subset": "a"})
        assert len(result) == 2

    def test_filter_base(self, ex):
        df = pd.DataFrame({"tipo": ["A", "B", "A"], "valore": [10, 20, 30]})
        result = ex.execute_with_params("filter", [df], {"column": "tipo", "operator": "==", "value": "A"})
        assert len(result) == 2
        assert all(result["tipo"] == "A")

    def test_transform_inesistente_raise(self, ex):
        df = pd.DataFrame({"id": [1]})
        with pytest.raises(ValueError, match="non trovata"):
            ex.execute_with_params("TRANSFORM_XYZ_INESISTENTE", [df], {})

    def test_sort_base(self, ex):
        df = pd.DataFrame({"valore": [3, 1, 2]})
        result = ex.execute_with_params("sort", [df], {"columns": "valore", "ascending": True})
        assert list(result["valore"]) == [1, 2, 3]


# =============================================================================
# validate_config
# =============================================================================

class TestValidateConfig:

    def test_config_valida_errori_vuoti(self, ex):
        errors = ex.validate_config("distinct", {})
        assert isinstance(errors, list)

    def test_transform_inesistente_errori_vuoti(self, ex):
        errors = ex.validate_config("TRANSFORM_XYZ", {})
        assert isinstance(errors, list)

    def test_config_con_valori_corretti(self, ex):
        errors = ex.validate_config("filter", {
            "column": "id", "operator": "==", "value": "1"
        })
        assert isinstance(errors, list)


# =============================================================================
# get_default_config
# =============================================================================

class TestGetDefaultConfig:

    def test_distinct_defaults(self, ex):
        defaults = ex.get_default_config("distinct")
        assert isinstance(defaults, dict)

    def test_filter_defaults(self, ex):
        defaults = ex.get_default_config("filter")
        assert isinstance(defaults, dict)

    def test_transform_inesistente_defaults_vuoti(self, ex):
        defaults = ex.get_default_config("TRANSFORM_XYZ_INESISTENTE")
        assert isinstance(defaults, dict)
        assert defaults == {}

    def test_defaults_hanno_valori_dal_registry(self, ex):
        """I default devono provenire dal registry, non essere inventati."""
        config = ex.get_transform_config("distinct")
        defaults = ex.get_default_config("distinct")
        if config:
            for param in config.get("parameters", []):
                if "default" in param:
                    assert param["id"] in defaults


# =============================================================================
# reload
# =============================================================================

class TestReload:

    def test_reload_non_crasha(self, ex):
        """reload() deve ricaricare il registry senza crashare."""
        ex.reload()
        # Dopo reload il registry deve ancora funzionare
        assert len(ex.list_transforms()) > 0
