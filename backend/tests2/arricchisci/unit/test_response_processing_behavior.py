"""Behavioral unit tests for EnrichmentResponseProcessingMixin.

Target module: app/core/arricchisci/enrichment_response_processing.py

The mixin is pure in-memory data transformation (no DB / HTTP). We exercise it
through a minimal DummyExecutor that provides the two collaborator attributes the
mixin reads: ``output_mapping`` and ``detected_column_types``.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from app.core.arricchisci.enrichment_response_processing import (
    EnrichmentResponseProcessingMixin,
    _geojson_to_wkt,
)


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci]


class DummyExecutor(EnrichmentResponseProcessingMixin):
    """Minimal host providing the attributes the mixin depends on."""

    def __init__(self, output_mapping=None, detected_column_types=None) -> None:
        self.output_mapping = output_mapping if output_mapping is not None else []
        self.detected_column_types = (
            detected_column_types if detected_column_types is not None else {}
        )


def make_executor(output_mapping=None, detected_column_types=None) -> DummyExecutor:
    return DummyExecutor(output_mapping, detected_column_types)


# ---------------------------------------------------------------------------
# _geojson_to_wkt (module-level helper) + delegation
# ---------------------------------------------------------------------------


def test_geojson_to_wkt_point_produces_wkt() -> None:
    wkt = _geojson_to_wkt({"type": "Point", "coordinates": [1.0, 2.0]})
    assert wkt == "POINT (1 2)"


def test_geojson_to_wkt_invalid_falls_back_to_json() -> None:
    # A malformed geometry that shapely cannot build -> JSON-string fallback.
    garbage = {"type": "NotAGeometry", "coordinates": "x"}
    out = _geojson_to_wkt(garbage)
    assert json.loads(out) == garbage


# ---------------------------------------------------------------------------
# _response_type_detector property (caching + re-creation on mapping swap)
# ---------------------------------------------------------------------------


def test_response_type_detector_is_cached_for_same_mapping() -> None:
    ex = make_executor()
    d1 = ex._response_type_detector
    d2 = ex._response_type_detector
    assert d1 is d2
    assert d1.detected_column_types is ex.detected_column_types


def test_response_type_detector_recreated_when_mapping_object_replaced() -> None:
    ex = make_executor()
    d1 = ex._response_type_detector
    # Swap the underlying dict object -> detector must be rebuilt to track it.
    ex.detected_column_types = {}
    d2 = ex._response_type_detector
    assert d1 is not d2
    assert d2.detected_column_types is ex.detected_column_types


# ---------------------------------------------------------------------------
# _auto_flatten (no output_mapping path)
# ---------------------------------------------------------------------------


def test_auto_flatten_feature_collection_explodes_features() -> None:
    ex = make_executor()
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "id": 7,
                "properties": {"name": "alpha", "pop": 1000.0, "ratio": 0.5},
                "geometry": {"type": "Point", "coordinates": [10.0, 20.0]},
            },
            {
                "id": 8,
                "properties": {"name": "beta", "pop": 2000.0, "ratio": 0.25},
                "geometry": {"type": "Point", "coordinates": [11.0, 21.0]},
            },
        ],
    }
    rows = ex._extract_output(data)
    assert len(rows) == 2
    # whole float -> int, decimal float kept
    assert rows[0]["pop"] == 1000
    assert isinstance(rows[0]["pop"], int)
    assert rows[0]["ratio"] == 0.5
    assert rows[0]["name"] == "alpha"
    assert rows[0]["geometry"] == "POINT (10 20)"
    assert rows[0]["id"] == 7
    # detected types from first feature
    assert ex.detected_column_types["name"] == "str"
    assert ex.detected_column_types["pop"] == "real"
    assert ex.detected_column_types["geometry"] == "geometry:point"
    assert ex.detected_column_types["id"] == "int"


def test_auto_flatten_empty_feature_collection_returns_single_empty_row() -> None:
    ex = make_executor()
    rows = ex._extract_output({"type": "FeatureCollection", "features": []})
    assert rows == [{}]
    # No features -> no types detected.
    assert ex.detected_column_types == {}


def test_auto_flatten_feature_without_geometry_or_id() -> None:
    ex = make_executor()
    data = {
        "type": "FeatureCollection",
        "features": [{"properties": {"k": "v"}}],
    }
    rows = ex._extract_output(data)
    assert rows == [{"k": "v"}]
    assert "geometry" not in rows[0]
    assert "id" not in rows[0]


def test_auto_flatten_feature_null_properties_handled() -> None:
    ex = make_executor()
    data = {
        "type": "FeatureCollection",
        "features": [{"properties": None, "geometry": {"type": "Point", "coordinates": [0, 0]}}],
    }
    rows = ex._extract_output(data)
    assert rows[0]["geometry"] == "POINT (0 0)"
    assert ex.detected_column_types["geometry"] == "geometry:point"


def test_auto_flatten_list_of_dicts_one_row_per_element() -> None:
    ex = make_executor()
    data = [
        {"a": 1, "b": 2.0, "nested": {"x": 1}},
        {"a": 3, "b": 4.5, "nested": {"y": 2}},
    ]
    rows = ex._extract_output(data)
    assert len(rows) == 2
    assert rows[0]["a"] == 1
    assert rows[0]["b"] == 2  # whole float -> int via flatten_dict
    assert rows[0]["nested"] == '{"x": 1}'
    # types inferred from first element
    assert ex.detected_column_types["a"] == "int"
    assert ex.detected_column_types["b"] == "real"
    assert ex.detected_column_types["nested"] == "str"


def test_auto_flatten_list_of_scalars_wraps_in_response_column() -> None:
    ex = make_executor()
    rows = ex._extract_output([1, 2, 3])
    assert rows == [{"_response": "[1, 2, 3]"}]


def test_auto_flatten_empty_list_wraps_in_response_column() -> None:
    ex = make_executor()
    rows = ex._extract_output([])
    assert rows == [{"_response": "[]"}]


def test_auto_flatten_generic_dict_single_row() -> None:
    ex = make_executor()
    data = {"full_name": "Mario", "count": 5.0, "meta": {"k": 1}}
    rows = ex._extract_output(data)
    assert len(rows) == 1
    assert rows[0]["full_name"] == "Mario"
    assert rows[0]["count"] == 5
    assert rows[0]["meta"] == '{"k": 1}'
    assert ex.detected_column_types["full_name"] == "str"
    assert ex.detected_column_types["count"] == "real"


def test_auto_flatten_scalar_wraps_as_string_response() -> None:
    ex = make_executor()
    rows = ex._extract_output("plain")
    assert rows == [{"_response": "plain"}]


def test_auto_flatten_none_wraps_as_string_response() -> None:
    ex = make_executor()
    rows = ex._extract_output(None)
    assert rows == [{"_response": "None"}]


# ---------------------------------------------------------------------------
# _extract_output with flat output_mapping (max_depth == 0)
# ---------------------------------------------------------------------------


def test_extract_output_flat_mapping_single_row() -> None:
    ex = make_executor(
        output_mapping=[
            {"path": "data.name", "column": "name"},
            {"path": "data.value", "column": "value"},
        ]
    )
    data = {"data": {"name": "x", "value": 42}}
    rows = ex._extract_output(data)
    assert rows == [{"name": "x", "value": 42}]


def test_extract_output_flat_mapping_column_defaults_to_path() -> None:
    ex = make_executor(output_mapping=[{"path": "data.name"}])
    rows = ex._extract_output({"data": {"name": "z"}})
    assert rows == [{"data.name": "z"}]


def test_extract_output_flat_mapping_all_none_returns_none_row() -> None:
    ex = make_executor(
        output_mapping=[
            {"path": "missing.a", "column": "a"},
            {"path": "missing.b", "column": "b"},
        ]
    )
    rows = ex._extract_output({"results": [], "fields": []})
    # Does NOT fall back to auto_flatten; returns single all-None row.
    assert rows == [{"a": None, "b": None}]
    assert "results" not in rows[0]


def test_extract_output_flat_mapping_jsonpath_root_marker() -> None:
    ex = make_executor(output_mapping=[{"path": "$.full_name", "column": "name"}])
    rows = ex._extract_output({"full_name": "Anna"})
    assert rows == [{"name": "Anna"}]


# ---------------------------------------------------------------------------
# _extract_output with multi-row mapping ([] / [*])
# ---------------------------------------------------------------------------


def test_extract_output_single_level_explosion() -> None:
    ex = make_executor(
        output_mapping=[
            {"path": "features[].properties.name", "column": "name"},
            {"path": "features[].properties.code", "column": "code"},
        ]
    )
    data = {
        "features": [
            {"properties": {"name": "a", "code": 1}},
            {"properties": {"name": "b", "code": 2}},
        ]
    }
    rows = ex._extract_output(data)
    assert rows == [{"name": "a", "code": 1}, {"name": "b", "code": 2}]


def test_extract_output_star_marker_normalized_to_brackets() -> None:
    ex = make_executor(output_mapping=[{"path": "items[*].v", "column": "v"}])
    data = {"items": [{"v": 10}, {"v": 20}]}
    rows = ex._extract_output(data)
    assert rows == [{"v": 10}, {"v": 20}]


def test_extract_output_cartesian_product_nested_levels() -> None:
    ex = make_executor(
        output_mapping=[
            {"path": "features[*].nome", "column": "nome"},
            {"path": "features[*].valori[*].anno", "column": "anno"},
        ]
    )
    data = {
        "features": [
            {"nome": "F1", "valori": [{"anno": 2020}, {"anno": 2021}]},
            {"nome": "F2", "valori": [{"anno": 2022}]},
        ]
    }
    rows = ex._extract_output(data)
    assert rows == [
        {"nome": "F1", "anno": 2020},
        {"nome": "F1", "anno": 2021},
        {"nome": "F2", "anno": 2022},
    ]


def test_extract_output_depth_zero_mapping_mixed_with_exploded() -> None:
    # One mapping is depth-0 (constant across rows), the other explodes.
    ex = make_executor(
        output_mapping=[
            {"path": "meta.source", "column": "source"},
            {"path": "rows[].id", "column": "id"},
        ]
    )
    data = {"meta": {"source": "S"}, "rows": [{"id": 1}, {"id": 2}]}
    rows = ex._extract_output(data)
    assert rows == [{"source": "S", "id": 1}, {"source": "S", "id": 2}]


def test_extract_output_explosion_non_list_returns_empty_row() -> None:
    ex = make_executor(output_mapping=[{"path": "features[].x", "column": "x"}])
    data = {"features": {"not": "a list"}}
    rows = ex._extract_output(data)
    # No rows produced -> sentinel single empty dict.
    assert rows == [{}]


def test_extract_output_leaf_context_used_when_no_suffix() -> None:
    # Path ending in [] (no trailing field) -> the whole leaf item becomes value.
    ex = make_executor(output_mapping=[{"path": "vals[]", "column": "v"}])
    data = {"vals": [1, 2, 3]}
    rows = ex._extract_output(data)
    assert rows == [{"v": 1}, {"v": 2}, {"v": 3}]


# ---------------------------------------------------------------------------
# _resolve_nested_list
# ---------------------------------------------------------------------------


def test_resolve_nested_list_no_marker_delegates_to_extract() -> None:
    ex = make_executor()
    assert ex._resolve_nested_list({"a": {"b": 5}}, "a.b") == 5


def test_resolve_nested_list_single_level_returns_items() -> None:
    ex = make_executor()
    data = {"features": [{"x": 1}, {"x": 2}]}
    assert ex._resolve_nested_list(data, "features[]") == [{"x": 1}, {"x": 2}]


def test_resolve_nested_list_flattens_nested_markers() -> None:
    ex = make_executor()
    data = {
        "features": [
            {"properties": {"valori": [1, 2]}},
            {"properties": {"valori": [3]}},
        ]
    }
    assert ex._resolve_nested_list(data, "features[].properties.valori[]") == [1, 2, 3]


def test_resolve_nested_list_accepts_star_marker() -> None:
    ex = make_executor()
    data = {"items": [{"k": "a"}, {"k": "b"}]}
    assert ex._resolve_nested_list(data, "items[*].k") == ["a", "b"]


def test_resolve_nested_list_non_list_prefix_returns_empty() -> None:
    ex = make_executor()
    assert ex._resolve_nested_list({"features": "scalar"}, "features[].x") == []


def test_resolve_nested_list_scalar_leaf_appended() -> None:
    # When per-item resolution yields a scalar, it is appended (not extended).
    ex = make_executor()
    data = {"features": [{"name": "a"}, {"name": "b"}]}
    assert ex._resolve_nested_list(data, "features[].name") == ["a", "b"]


# ---------------------------------------------------------------------------
# _extract_json_value delegation + GeoJSON auto-WKT
# ---------------------------------------------------------------------------


def test_extract_json_value_empty_path_returns_data() -> None:
    ex = make_executor()
    obj = {"a": 1}
    assert ex._extract_json_value(obj, "") is obj


def test_extract_json_value_geometry_converted_to_wkt() -> None:
    ex = make_executor()
    data = {"geom": {"type": "Point", "coordinates": [3, 4]}}
    assert ex._extract_json_value(data, "geom") == "POINT (3 4)"


def test_extract_json_value_missing_path_returns_none() -> None:
    ex = make_executor()
    assert ex._extract_json_value({"a": 1}, "a.b.c") is None


# ---------------------------------------------------------------------------
# legacy private delegates
# ---------------------------------------------------------------------------


def test_infer_scalar_type_delegate() -> None:
    ex = make_executor()
    assert ex._infer_scalar_type(True) == "bool"
    assert ex._infer_scalar_type(3) == "int"
    assert ex._infer_scalar_type(3.14) == "real"
    assert ex._infer_scalar_type("s") == "str"
    assert ex._infer_scalar_type({"type": "Polygon", "coordinates": []}) == "geometry:polygon"


def test_detect_geometry_type_delegate_writes_into_mapping() -> None:
    ex = make_executor()
    ex._detect_geometry_type("g", "multilinestring")
    assert ex.detected_column_types == {"g": "geometry:line"}


def test_detect_geometry_type_unknown_falls_back_to_geometry() -> None:
    ex = make_executor()
    ex._detect_geometry_type("g", "weirdtype")
    assert ex.detected_column_types == {"g": "geometry"}


def test_flatten_dict_delegate() -> None:
    ex = make_executor()
    out = ex._flatten_dict({"n": 7.0, "d": 1.5, "nested": {"x": 1}, "lst": [1, 2]})
    assert out == {"n": 7, "d": 1.5, "nested": '{"x": 1}', "lst": "[1, 2]"}


# ---------------------------------------------------------------------------
# _apply_result -> writes into a real DataFrame
# ---------------------------------------------------------------------------


def test_apply_result_writes_cells_into_dataframe() -> None:
    ex = make_executor()
    df = pd.DataFrame({"name": ["", ""], "age": [0, 0]})
    ex._apply_result(df, 1, {"name": "Lucia", "age": 30})
    assert df.loc[1, "name"] == "Lucia"
    assert df.loc[1, "age"] == 30
    # untouched row stays default
    assert df.loc[0, "name"] == ""


def test_apply_result_empty_result_is_noop() -> None:
    ex = make_executor()
    df = pd.DataFrame({"name": ["keep"]})
    ex._apply_result(df, 0, {})
    assert df.loc[0, "name"] == "keep"
