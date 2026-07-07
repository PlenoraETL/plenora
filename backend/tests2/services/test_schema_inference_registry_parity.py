"""
Contract test BE-SCHEMA-INFER-001A: transform schema dispatch parity.

This file is intentionally scoped to parity and representative behavior.
Focused handler tests own exhaustive per-transform edge cases.
"""

from __future__ import annotations

import json
import re
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.projects.schema_inference import ColumnSchema, SchemaInferenceService
from tests2.shared._ast_identifiers import function_def_names


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_SCHEMA_TRANSFORM_HANDLERS_PY = (
    _BACKEND_ROOT / "app" / "core" / "projects" / "schema_transform_handlers.py"
)
_REGISTRY_JSON = _BACKEND_ROOT / "app" / "core" / "manipola" / "transforms_registry.json"

_REGISTRY_BLOCK_RE = re.compile(
    r"#\s*DISPATCH-REGISTRY:start\s*\n(.*?)#\s*DISPATCH-REGISTRY:end",
    re.DOTALL,
)
_REGISTRY_KEY_RE = re.compile(r'^\s*"([a-zA-Z0-9_]+)"\s*:', re.MULTILINE)


def _parse_dispatcher_branches() -> set[str]:
    src = _SCHEMA_TRANSFORM_HANDLERS_PY.read_text(encoding="utf-8")
    block_match = _REGISTRY_BLOCK_RE.search(src)
    assert block_match is not None, (
        "DISPATCH-REGISTRY sentinel missing in schema_transform_handlers.py"
    )
    return set(_REGISTRY_KEY_RE.findall(block_match.group(1)))


def _load_registry_ids() -> set[str]:
    data = json.loads(_REGISTRY_JSON.read_text(encoding="utf-8"))
    return set(data.get("transforms", {}).keys())


EXPECTED_DISPATCHER_BRANCHES: set[str] = {
    "join", "sjoin", "aggregate", "formula", "filter", "concat", "sort",
    "distinct", "rename", "drop", "drop_columns", "fill_na", "replace",
    "split_column",
    "geo_buffer", "geo_centroid", "geo_area", "geo_clip", "geo_dissolve",
    "geo_simplify", "geo_convex_hull", "geo_reproject", "geo_intersection",
    "geo_union", "geo_difference", "geo_to_crs",
    "md5_hash", "mask_data",
    "type_cast", "conditional", "text_normalize",
    "concat_columns", "pivot", "geo_envelope",
    "geo_from_coords",
    "add_row_number", "uuid_generator", "string_length", "date_extract",
    "geo_distance", "geo_to_wkt",
    "geo_length", "geo_perimeter", "geo_vertex_count",
    "string_extract", "bin", "string_pad",
    "geo_bounds_extractor", "geo_count_points_in_polygons",
    "geo_line_builder", "geo_polygon_builder",
    "geo_point_on_surface", "geo_boundary",
    "reorder_columns", "geo_voronoi",
    "cross_join", "geo_within", "geo_overlay",
    "geo_symmetric_difference", "geo_nearest", "lookup",
    "table_diff", "melt", "statistics", "window_function",
    "flatten_json", "transpose",
}

PASSTHROUGH_WRONG_GAP: set[str] = set()
MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT: set[str] = set()
PASSTHROUGH_OK_GAP: set[str] = {
    "sample",
    "dedup_advanced",
    "geo_clean_topology",
    "geo_make_valid",
    "geo_explode",
}


def _service() -> SchemaInferenceService:
    return SchemaInferenceService(project_service=None)


def _node(transform_op: str, config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id="n1",
        config=config or {},
        transform_op=transform_op,
        data=None,
    )


def _col(name: str, ctype: str, source: str = "src", calculated: bool = False):
    return ColumnSchema(
        name=name,
        type=ctype,
        source_node=source,
        is_calculated=calculated,
    )


def _base_schema():
    return [_col("a", "int"), _col("b", "str")]


def _geo_schema():
    return [
        _col("id", "int"),
        _col("name", "str"),
        _col("geometry", "geometry:polygon"),
    ]


def _infer(
    op: str,
    config: dict | None = None,
    schemas: list[list[ColumnSchema]] | None = None,
):
    return _service()._infer_transform_schema(
        _node(op, config),
        schemas if schemas is not None else [_base_schema()],
        edges_by_target={},
    )


def _pairs(schema):
    return [(col.name, col.type, col.is_calculated) for col in schema]


def _name_type(schema):
    return [(col.name, col.type) for col in schema]


def test_dispatcher_source_and_runtime_match_expected_snapshot():
    actual = _parse_dispatcher_branches()
    assert actual == EXPECTED_DISPATCHER_BRANCHES

    handlers = _service()._build_transform_handlers(edges_by_target={})
    assert set(handlers.keys()) == EXPECTED_DISPATCHER_BRANCHES
    assert all(callable(handler) for handler in handlers.values())


def test_schema_inference_service_does_not_magic_delegate_schema_handlers():
    source = inspect.getsource(SchemaInferenceService)
    service = _service()

    assert "__getattr__" not in function_def_names(source)
    assert not hasattr(service, "_schema_join")
    assert callable(service._build_transform_handlers(edges_by_target={})["join"])


def test_registry_loads_and_is_fully_classified():
    registry = _load_registry_ids()
    dispatcher = _parse_dispatcher_branches()

    assert len(registry) == 70
    assert dispatcher - registry == {"drop", "geo_to_crs"}
    assert registry - dispatcher == PASSTHROUGH_OK_GAP
    assert PASSTHROUGH_WRONG_GAP == set()
    assert MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT == PASSTHROUGH_WRONG_GAP


def test_registry_full_coverage_dispatcher_plus_passthrough_ok():
    registry = _load_registry_ids()
    classified = (EXPECTED_DISPATCHER_BRANCHES & registry) | PASSTHROUGH_OK_GAP
    assert registry == classified


def test_unknown_transform_falls_back_to_passthrough():
    out = _infer("op_che_non_esiste", {})
    assert _name_type(out) == [("a", "int"), ("b", "str")]


@pytest.mark.parametrize(
    ("op", "config"),
    [
        ("sample", {}),
        ("dedup_advanced", {}),
        ("geo_clean_topology", {}),
        ("geo_make_valid", {}),
        ("geo_explode", {}),
    ],
)
def test_rows_only_registry_ops_preserve_schema_via_passthrough(op, config):
    out = _infer(op, config)
    assert _pairs(out) == [("a", "int", False), ("b", "str", False)]


def test_registry_aliases_bind_to_expected_handlers():
    assert _name_type(_infer("drop", {"columns": ["a"]})) == _name_type(
        _infer("drop_columns", {"columns": ["a"]})
    )
    assert _name_type(_infer("geo_to_crs", {})) == _name_type(
        _infer("geo_reproject", {})
    )


@pytest.mark.parametrize(
    ("op", "config", "expected"),
    [
        ("add_row_number", {}, [("a", "int", False), ("b", "str", False), ("row_number", "int", True)]),
        ("uuid_generator", {"output_column": "rec_id"}, [("a", "int", False), ("b", "str", False), ("rec_id", "str", True)]),
        ("string_length", {"column": "b"}, [("a", "int", False), ("b", "str", False), ("b_length", "int", True)]),
        ("bin", {"column": "a", "bins": 3}, [("a", "int", False), ("b", "str", False), ("a_bin", "str", True)]),
        ("statistics", {"column": "a", "stats": ["count", "mean"], "output_prefix": "s_"}, [("a", "int", False), ("b", "str", False), ("s_count", "int", True), ("s_mean", "real", True)]),
        ("window_function", {"column": "a", "function": "cumcount"}, [("a", "int", False), ("b", "str", False), ("a_cumcount", "int", True)]),
    ],
)
def test_additive_transform_families_have_representative_schema(op, config, expected):
    assert _pairs(_infer(op, config)) == expected


@pytest.mark.parametrize(
    ("op", "config"),
    [
        ("string_length", {}),
        ("date_extract", {}),
        ("string_extract", {"column": "b", "pattern": "["}),
        ("bin", {}),
        ("lookup", {"column": "b", "mapping": {}}),
        ("statistics", {"column": "missing"}),
        ("window_function", {"column": "a", "function": "unknown"}),
    ],
)
def test_invalid_or_incomplete_configs_passthrough(op, config):
    assert _name_type(_infer(op, config)) == [("a", "int"), ("b", "str")]


def test_date_extract_normalizes_parts_and_prefixes_output():
    out = _infer(
        "date_extract",
        {"column": "b", "parts": ["year", "millennium", "month"], "prefix": "dt_"},
    )
    assert _pairs(out) == [
        ("a", "int", False),
        ("b", "str", False),
        ("dt_year", "int", True),
        ("dt_month", "int", True),
    ]


def test_string_extract_named_groups_expand_to_typed_columns():
    out = _infer(
        "string_extract",
        {"column": "b", "pattern": r"(?P<code>[A-Z]+)-(?P<num>\d+)"},
    )
    assert _pairs(out) == [
        ("a", "int", False),
        ("b", "str", False),
        ("code", "str", True),
        ("num", "str", True),
    ]


def test_string_pad_can_overwrite_source_column_type():
    out = _infer("string_pad", {"column": "a", "width": 4})
    assert _pairs(out) == [("a", "str", True), ("b", "str", False)]


@pytest.mark.parametrize(
    ("op", "config", "expected_tail"),
    [
        ("geo_distance", {"output_column": "dist"}, [("dist", "real", True)]),
        ("geo_to_wkt", {"mode": "both"}, [("geometry_wkt", "str", True), ("latitude", "real", True), ("longitude", "real", True)]),
        ("geo_length", {}, [("length_m", "real", True)]),
        ("geo_perimeter", {}, [("perimeter_m", "real", True)]),
        ("geo_vertex_count", {}, [("vertex_count", "int", True)]),
        ("geo_bounds_extractor", {"prefix": "bbox_"}, [("bbox_min_x", "real", True), ("bbox_min_y", "real", True), ("bbox_max_x", "real", True), ("bbox_max_y", "real", True)]),
        ("geo_count_points_in_polygons", {"aggregate_column": "value", "aggregate_func": "max"}, [("point_count", "int", True), ("value_max", "real", True)]),
    ],
)
def test_geo_additive_families_have_representative_schema(op, config, expected_tail):
    out = _infer(op, config, [_geo_schema(), [_col("value", "real", "points")]])
    assert _pairs(out) == _pairs(_geo_schema()) + expected_tail


@pytest.mark.parametrize(
    ("op", "config", "expected"),
    [
        ("geo_line_builder", {"group_column": "name", "output_column": "line"}, [("name", "str", False), ("line", "geometry:line", True)]),
        ("geo_polygon_builder", {"group_column": "id"}, [("id", "int", False), ("geometry", "geometry:polygon", True)]),
        ("geo_point_on_surface", {"geom_column": "geometry", "output_column": "geometry"}, [("id", "int", False), ("name", "str", False), ("geometry", "geometry:point", True)]),
        ("geo_boundary", {"geom_column": "geometry", "output_column": "geometry"}, [("id", "int", False), ("name", "str", False), ("geometry", "geometry", True)]),
        ("geo_voronoi", {}, [("id", "int", False), ("name", "str", False), ("geometry", "geometry:polygon", True)]),
    ],
)
def test_geo_rebuild_and_geometry_retype_families_have_representative_schema(
    op, config, expected
):
    assert _pairs(_infer(op, config, [_geo_schema()])) == expected


def test_join_handler_receives_edges_by_target_and_keeps_single_common_key():
    left = [_col("id", "int", "L")]
    right = [_col("id", "int", "R")]
    out = _infer("join", {"left_on": "id", "right_on": "id"}, [left, right])
    assert _name_type(out) == [("id", "int")]


def test_cross_join_collision_suffixes_right_columns():
    left = [_col("id", "int", "L"), _col("shared", "str", "L")]
    right = [_col("shared", "real", "R"), _col("other", "str", "R")]
    out = _infer("cross_join", {}, [left, right])
    assert _name_type(out) == [
        ("id", "int"),
        ("shared_x", "str"),
        ("shared_y", "real"),
        ("other", "str"),
    ]


def test_geo_overlay_dispatch_reads_how_from_config():
    left = [_col("a", "int", "L"), _col("g", "geometry:polygon", "L")]
    right = [_col("a", "str", "R"), _col("b", "str", "R"), _col("g", "geometry:polygon", "R")]
    out_overlay = _infer("geo_overlay", {"how": "union"}, [left, right])
    out_union = _infer("geo_union", {}, [left, right])
    assert _name_type(out_overlay) == _name_type(out_union)


def test_geo_nearest_adds_right_non_geometry_columns_and_distance():
    left = [_col("id", "int", "L"), _col("name", "str", "L"), _col("g", "geometry:point", "L")]
    right = [_col("name", "str", "R"), _col("score", "real", "R"), _col("g", "geometry:point", "R")]
    out = _infer("geo_nearest", {"output_column": "meters"}, [left, right])
    assert _name_type(out) == [
        ("id", "int"),
        ("name", "str"),
        ("g", "geometry:point"),
        ("name_right", "str"),
        ("score", "real"),
        ("meters", "real"),
    ]


def test_lookup_explicit_output_column_uses_source_type():
    out = _infer(
        "lookup",
        {"column": "a", "mapping": {"1": "one"}, "output_column": "label"},
    )
    assert _pairs(out) == [
        ("a", "int", False),
        ("b", "str", False),
        ("label", "int", True),
    ]


def test_table_diff_full_schema_with_matching_keys():
    left = [_col("id", "int", "L"), _col("status", "str", "L"), _col("old_only", "real", "L")]
    right = [_col("id", "int", "R"), _col("status", "str", "R"), _col("new_only", "bool", "R")]
    out = _infer(
        "table_diff",
        {"left_keys": ["id"], "right_keys": ["id"]},
        [left, right],
    )
    assert _name_type(out) == [
        ("id", "int"),
        ("status", "str"),
        ("old_only", "real"),
        ("new_only", "bool"),
        ("_diff_status", "str"),
        ("_diff_columns", "str"),
        ("_diff_old_values", "str"),
    ]


def test_melt_heterogeneous_value_types_falls_back_to_str():
    schema = [_col("id", "int"), _col("revenue", "real"), _col("label", "str")]
    out = _infer("melt", {"id_columns": ["id"]}, [schema])
    assert _name_type(out) == [("id", "int"), ("variable", "str"), ("value", "str")]


def test_pivot_mapping_outputs_index_and_declared_columns():
    schema = [
        _col("cd_id", "int"),
        _col("valuation_type", "str"),
        _col("amount_valuation", "real"),
    ]
    out = _infer(
        "pivot",
        {
            "index_col": "cd_id",
            "pivot_col": "valuation_type",
            "value_col": "amount_valuation",
            "aggr_func": "max",
            "mapping": {
                "A": "rendita",
                "I": "omi",
                "M": "perizia",
                "S": "acquisto",
            },
        },
        [schema],
    )
    assert _name_type(out) == [
        ("cd_id", "int"),
        ("rendita", "real"),
        ("omi", "real"),
        ("perizia", "real"),
        ("acquisto", "real"),
    ]


def test_reorder_columns_explicit_order_ignores_unknown_names():
    out = _infer("reorder_columns", {"columns": ["b", "missing", "a"]})
    assert _name_type(out) == [("b", "str"), ("a", "int")]


def test_flatten_json_config_explicit_contract_and_safe_fallback():
    assert _name_type(_infer("flatten_json", {"column": "b"})) == [
        ("a", "int"),
        ("b", "str"),
    ]

    out = _infer(
        "flatten_json",
        {
            "column": "b",
            "prefix": "json_",
            "output_columns": [
                "city",
                None,
                {"name": ""},
                {"name": "score", "type": "real"},
            ],
        },
    )
    assert _pairs(out) == [
        ("a", "int", False),
        ("b", "str", False),
        ("json_city", "str", True),
        ("json_score", "real", True),
    ]


def test_transpose_config_explicit_contract_and_id_column_dedupe():
    assert _name_type(_infer("transpose", {})) == [("a", "int"), ("b", "str")]

    schema = [_col("metric", "str"), _col("q1", "real")]
    out = _infer(
        "transpose",
        {
            "id_column": "metric",
            "output_columns": ["metric", {"name": "revenue", "type": "real"}],
        },
        [schema],
    )
    assert _pairs(out) == [
        ("metric", "str", False),
        ("revenue", "real", True),
    ]
