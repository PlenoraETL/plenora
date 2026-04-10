"""
tests/arricchisci/unit/test_extract_json_value.py
==================================================
Unit test per extract_json_value e _tokenize_path in app/shared/api_utils.py.

Copre le estensioni aggiunte in sessione 2026-04-10:
  - Filter syntax: list[key=value], list[key!=value]
  - Nested filter: list[nested.key=value]
  - Longest-first greedy dict match per chiavi con punti/spazi
  - Backward compat: dot notation, index, wildcard, GeoJSON→WKT

NON richiede backend, API o Celery.
"""

import pytest
pytestmark = pytest.mark.unit

from app.shared.api_utils import extract_json_value, _tokenize_path


# =============================================================================
# _tokenize_path
# =============================================================================

class TestTokenizePath:

    def test_simple_dots(self):
        assert _tokenize_path("a.b.c") == ["a", "b", "c"]

    def test_index(self):
        assert _tokenize_path("features[0].name") == ["features", "0", "name"]

    def test_wildcard(self):
        assert _tokenize_path("list[*].field") == ["list", "*", "field"]

    def test_top_filter(self):
        assert _tokenize_path("results[layerId=0].attributes.X") == [
            "results", "layerId=0", "attributes", "X"
        ]

    def test_nested_filter_dots_protected(self):
        """Dots inside brackets are preserved as part of the filter key."""
        assert _tokenize_path("features[properties.origine=asc3_2021].properties.p1") == [
            "features", "properties.origine=asc3_2021", "properties", "p1"
        ]

    def test_empty_path(self):
        assert _tokenize_path("") == [""]

    def test_multiple_brackets(self):
        assert _tokenize_path("a[0].b[1].c") == ["a", "0", "b", "1", "c"]


# =============================================================================
# extract_json_value — backward compat
# =============================================================================

class TestExtractJsonValueBackwardCompat:

    def test_empty_path_returns_data(self):
        data = {"x": 1}
        assert extract_json_value(data, "") == data

    def test_none_path_returns_data(self):
        data = {"x": 1}
        assert extract_json_value(data, None) == data

    def test_dot_notation(self):
        assert extract_json_value({"a": {"b": {"c": 42}}}, "a.b.c") == 42

    def test_array_index(self):
        assert extract_json_value({"items": [10, 20, 30]}, "items[1]") == 20

    def test_nested_array_index(self):
        data = {"features": [{"name": "X"}, {"name": "Y"}]}
        assert extract_json_value(data, "features[1].name") == "Y"

    def test_wildcard_first_element(self):
        data = {"features": [{"name": "X"}, {"name": "Y"}]}
        assert extract_json_value(data, "features[*].name") == "X"

    def test_wildcard_empty_list(self):
        assert extract_json_value({"features": []}, "features[*].name") is None

    def test_missing_key_returns_none(self):
        assert extract_json_value({"a": 1}, "b") is None

    def test_deep_missing_returns_none(self):
        assert extract_json_value({"a": {"b": 1}}, "a.c") is None

    def test_index_out_of_range(self):
        assert extract_json_value({"items": [1]}, "items[5]") is None

    def test_path_on_scalar_returns_none(self):
        assert extract_json_value({"x": 42}, "x.y.z") is None

    def test_real_classificazione_sismica(self):
        """Path reale usato nella classificazione sismica comunale."""
        data = {"features": [{"attributes": {"Classificazione": "3", "COMUNE": "Milano"}}]}
        assert extract_json_value(data, "features[0].attributes.Classificazione") == "3"
        assert extract_json_value(data, "features[0].attributes.COMUNE") == "Milano"

    def test_real_coordinates(self):
        """Path reale usato per estrarre coordinate catasto."""
        data = {"features": [{"geometry": {"coordinates": [10.42, 44.02]}}]}
        assert extract_json_value(data, "features[*].geometry.coordinates[0]") == 10.42
        assert extract_json_value(data, "features[*].geometry.coordinates[1]") == 44.02


# =============================================================================
# extract_json_value — filter syntax [key=value]
# =============================================================================

class TestExtractJsonValueFilter:

    @pytest.fixture
    def identify_full(self):
        """Risposta /identify con 3 layer (P3, P2, P1) — ordine completo."""
        return {"results": [
            {"layerId": 0, "attributes": {"Descrizione": "P3 elevata"}},
            {"layerId": 1, "attributes": {"Descrizione": "P2 media"}},
            {"layerId": 2, "attributes": {"Descrizione": "P1 bassa"}},
        ]}

    @pytest.fixture
    def identify_partial(self):
        """Risposta /identify parziale (solo P2 e P1, P3 mancante)."""
        return {"results": [
            {"layerId": 1, "attributes": {"Descrizione": "P2 media"}},
            {"layerId": 2, "attributes": {"Descrizione": "P1 bassa"}},
        ]}

    def test_filter_numeric_match(self, identify_full):
        assert extract_json_value(identify_full, "results[layerId=0].attributes.Descrizione") == "P3 elevata"

    def test_filter_numeric_second(self, identify_full):
        assert extract_json_value(identify_full, "results[layerId=1].attributes.Descrizione") == "P2 media"

    def test_filter_numeric_third(self, identify_full):
        assert extract_json_value(identify_full, "results[layerId=2].attributes.Descrizione") == "P1 bassa"

    def test_filter_missing_returns_none(self, identify_partial):
        """P3 (layerId=0) non presente nella risposta parziale."""
        assert extract_json_value(identify_partial, "results[layerId=0].attributes.Descrizione") is None

    def test_filter_partial_others_work(self, identify_partial):
        assert extract_json_value(identify_partial, "results[layerId=1].attributes.Descrizione") == "P2 media"
        assert extract_json_value(identify_partial, "results[layerId=2].attributes.Descrizione") == "P1 bassa"

    def test_filter_empty_results(self):
        assert extract_json_value({"results": []}, "results[layerId=0].attributes.X") is None

    def test_filter_string_value(self, identify_full):
        assert extract_json_value(identify_full, "results[layerId=0].layerId") == 0

    def test_filter_no_match(self, identify_full):
        assert extract_json_value(identify_full, "results[layerId=99].attributes.Descrizione") is None

    def test_filter_on_non_list_returns_none(self):
        assert extract_json_value({"results": {"layerId": 0}}, "results[layerId=0].x") is None

    def test_filter_non_dict_elements_skipped(self):
        data = {"items": ["a", "b", {"id": 1, "val": "found"}]}
        assert extract_json_value(data, "items[id=1].val") == "found"


# =============================================================================
# extract_json_value — greedy dotted keys
# =============================================================================

class TestExtractJsonValueGreedyDottedKeys:

    def test_single_dot_key(self):
        """Chiave tipo Raster.Value — tipica ArcGIS."""
        data = {"attributes": {"Raster.Value": "2", "Raster.Count": "100"}}
        assert extract_json_value(data, "attributes.Raster.Value") == "2"

    def test_double_dot_key(self):
        """Chiave tipo UniqueValue.Pixel Value — 2 punti nel nome."""
        data = {"attributes": {"UniqueValue.Pixel Value": "3.000000"}}
        assert extract_json_value(data, "attributes.UniqueValue.Pixel Value") == "3.000000"

    def test_greedy_prefers_longer_key(self):
        """Se esiste sia 'x' che 'x.y', la chiave composta vince."""
        data = {"x": {"y": "short"}, "x.y": "long"}
        assert extract_json_value(data, "x.y") == "long"

    def test_triple_dot_key(self):
        data = {"a.b.c": 42}
        assert extract_json_value(data, "a.b.c") == 42

    def test_combined_filter_and_greedy(self):
        """Filter + greedy dotted key (caso reale Pericolosita sismica INGV)."""
        data = {"results": [
            {"layerId": 0, "attributes": {"Raster.Value": "2", "Raster.Count": "100"}},
            {"layerId": 1, "attributes": {"Raster.Value": "3"}},
        ]}
        assert extract_json_value(data, "results[layerId=0].attributes.Raster.Value") == "2"
        assert extract_json_value(data, "results[layerId=1].attributes.Raster.Value") == "3"

    def test_tiranti_idrici_path(self):
        """Path reale per tiranti idrici: filter + Raster.TIRANTI."""
        data = {"results": [
            {"layerId": 0, "attributes": {"Raster.TIRANTI": ">3.0"}},
            {"layerId": 1, "attributes": {"Raster.TIRANTI": "2.0-3.0"}},
        ]}
        assert extract_json_value(data, "results[layerId=0].attributes.Raster.TIRANTI") == ">3.0"
        assert extract_json_value(data, "results[layerId=1].attributes.Raster.TIRANTI") == "2.0-3.0"


# =============================================================================
# extract_json_value — nested filter [nested.key=value]
# =============================================================================

class TestExtractJsonValueNestedFilter:

    @pytest.fixture
    def geojson_istat(self):
        """FeatureCollection ISTAT con 4 livelli (come Plenora sezioni_censimento)."""
        return {"type": "FeatureCollection", "features": [
            {"properties": {"origine": "asc1_2021", "p1": 53309}},
            {"properties": {"origine": "asc2_2021", "p1": 14988}},
            {"properties": {"origine": "asc3_2021", "p1": 10108}},
            {"properties": {"origine": "sezioni_censimento_2021", "p1": 1}},
        ]}

    def test_nested_filter_asc3(self, geojson_istat):
        assert extract_json_value(geojson_istat, "features[properties.origine=asc3_2021].properties.p1") == 10108

    def test_nested_filter_asc1(self, geojson_istat):
        assert extract_json_value(geojson_istat, "features[properties.origine=asc1_2021].properties.p1") == 53309

    def test_nested_filter_sezione(self, geojson_istat):
        assert extract_json_value(geojson_istat, "features[properties.origine=sezioni_censimento_2021].properties.p1") == 1

    def test_nested_filter_no_match(self, geojson_istat):
        assert extract_json_value(geojson_istat, "features[properties.origine=nonexistent].properties.p1") is None

    def test_nested_filter_different_field(self, geojson_istat):
        """Filtra per p1 (valore numerico) e leggi origine."""
        assert extract_json_value(geojson_istat, "features[properties.p1=1].properties.origine") == "sezioni_censimento_2021"
