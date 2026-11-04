"""L'incertezza richiede due esecuzioni con identico schema in ingresso."""

import pandas as pd
import pytest

from app.core.manipola.plugin_executor import get_executor
from app.core.manipola.canvas_schema_runtime import build_columns_with_types
from tests2.contracts.test_inference_runtime_parity import frames


def text_frame(values):
    return pd.DataFrame({"s": pd.Series(values, dtype=object)})


WITNESSES = [
    ("formula", {"new_column": "z", "formula": "s * 2"},
     [text_frame(["1", "2"])], [text_frame(["x", "y"])], "types"),
    ("fill_na", {"column": "s", "value": "POINT (0 0)"},
     [text_frame([None, None])], [text_frame(["x", None])], "types"),
    ("replace", {"column": "s", "old_value": "x", "new_value": "POINT (0 0)"},
     [text_frame(["x", "x"])], [text_frame(["y", "x"])], "types"),
    ("type_cast", {"column": "s", "target_type": "float", "errors": "coerce"},
     [text_frame(["1", "2"])], [text_frame(["x", "y"])], "types"),
    ("conditional", {"column": "s", "conditions": [{"operator": "==", "value": "a", "result": "1"}], "default": "x"},
     [text_frame(["a", "a"])], [text_frame(["a", "b"])], "types"),
    ("date_extract", {"column": "s", "parts": ["year"]},
     [text_frame(["2024-01-01", "2024-01-02"])], [text_frame(["2024-01-01", "bad"])], "types"),
    ("flatten_json", {"column": "s"},
     [text_frame(['{"a":1}', '{"a":2}'])], [text_frame(['{"a":"x"}', '{"a":"y"}'])], "types"),
    ("flatten_json", {"column": "s"},
     [text_frame(['{"a":1}'])], [text_frame(['{"b":1}'])], "names"),
    ("split_column", {"column": "s", "separator": ","},
     [text_frame(["a,b"])], [text_frame(["a,b,c"])], "names"),
    ("string_pad", {"column": "a"},
     [pd.DataFrame({"a": [1]})], [pd.DataFrame({"a": pd.Series([], dtype="int64")})], "types"),
    ("lookup", {"column": "s", "mapping": {"a": "1"}, "unmatched": "null"},
     [text_frame(["a", "a"])], [text_frame(["a", "b"])], "types"),
    ("string_extract", {"column": "s", "pattern": "x(.*)"},
     [text_frame(["xPOINT (0 0)"])], [text_frame(["xabc"])], "types"),
    ("table_diff", {"left_keys": ["id"], "right_keys": ["id"]},
     [pd.DataFrame({"id": [1], "a": [2], "solo": [3]}), pd.DataFrame({"id": [1], "a": [2]})],
     [pd.DataFrame({"id": [1], "a": [2], "solo": [3]}), pd.DataFrame({"id": [1], "a": [9]})], "names"),
]


def spatial_witnesses():
    import geopandas as gpd
    from shapely.geometry import Point
    left = gpd.GeoDataFrame({"a": [1]}, geometry=[Point(0, 0).buffer(2)], crs=3857)
    near = gpd.GeoDataFrame({"b": [2]}, geometry=[Point(0, 0).buffer(1)], crs=3857)
    far = gpd.GeoDataFrame({"b": [2]}, geometry=[Point(10, 0).buffer(1)], crs=3857)
    cases = []
    for op in ("geo_union", "geo_symmetric_difference", "geo_overlay"):
        cases.append((op, {"left_geom_column": "geometry", "right_geom_column": "geometry", "how": "union"},
                      [left, near], [left, far], "types"))
    cases.append(("geo_nearest", {"left_geom_column": "geometry", "right_geom_column": "geometry", "max_distance": 1},
                  [left, near], [left, far], "types"))
    points = gpd.GeoDataFrame({"p": [1]}, geometry=[Point(0, 0)], crs=3857)
    cases.append(("geo_count_points_in_polygons", {"polygon_geom_column": "geometry", "point_geom_column": "geometry", "aggregate_column": "p"},
                  [left, points], [left, points.iloc[:0]], "names"))
    distant = points.copy()
    distant.geometry = distant.geometry.translate(xoff=10)
    cases.append(("geo_count_points_in_polygons", {"polygon_geom_column": "geometry", "point_geom_column": "geometry", "aggregate_column": "p"},
                  [left, points], [left, distant], "types"))
    cases.append(("sjoin", {"left_geom_column": "geometry", "right_geom_column": "geometry", "how": "left"},
                  [left, near], [left, far], "types"))
    return cases


WITNESSES += spatial_witnesses()

# Le coppie pieno/vuoto mantengono dtype e nomi identici in ingresso.
for operation, config, aspect in [
    ("pivot", {"index_col": "id", "pivot_col": "s", "value_col": "a"}, "names"),
    ("transpose", {}, "names"), ("uuid_generator", {}, "names"),
    ("reorder_columns", {"columns": ["b", "a"]}, "names"),
    ("window_function", {"column": "a"}, "names"),
    ("string_length", {"column": "s"}, "names"),
    ("pivot", {"index_col": "id", "pivot_col": "s", "value_col": "a"}, "types"),
    ("table_diff", {"left_keys": ["id"], "right_keys": ["id"]}, "types"),
    ("string_length", {"column": "s", "output_column": "s"}, "types"),
    ("window_function", {"column": "a", "output_column": "s"}, "types"),
    ("geo_to_wkt", {"geom_column": "geometry"}, "types"),
    ("geo_nearest", {"left_geom_column": "geometry", "right_geom_column": "geometry"}, "names"),
    ("statistics", {"column": "a"}, "types"),
    ("geo_distance", {"left_geom_column": "geometry", "right_geom_column": "geometry"}, "types"),
    ("geo_line_builder", {"group_column": "id", "geom_column": "geometry"}, "types"),
    ("geo_polygon_builder", {"group_column": "id", "geom_column": "geometry"}, "types"),
]:
    inputs = frames(operation, False)
    if operation == "geo_polygon_builder":
        inputs[0]["id"] = 1
    alternative = [frame.iloc[:1] if operation == "pivot" and aspect == "types" else frame.iloc[:0] for frame in inputs]
    WITNESSES.append((operation, config, inputs, alternative, aspect))


@pytest.mark.parametrize("op,config,left,right,aspect", WITNESSES)
def test_data_dependent_aspect_has_witness(op, config, left, right, aspect):
    assert [build_columns_with_types(f, {}) for f in left] == [build_columns_with_types(f, {}) for f in right]
    outputs = [get_executor().execute_with_params(op, data, config) for data in (left, right)]
    schemas = [build_columns_with_types(output, {}) for output in outputs]
    if aspect == "names":
        assert [c["name"] for c in schemas[0]] != [c["name"] for c in schemas[1]]
    else:
        by_name = [{c["name"]: c["type"] for c in schema} for schema in schemas]
        assert any(by_name[0][name] != by_name[1][name] for name in by_name[0].keys() & by_name[1].keys())


def test_every_declared_uncertain_aspect_has_runtime_evidence():
    from tests2.contracts.inference_runtime_cases import CASES, EXTRA
    from tests2.contracts.test_a1_correction import canvas_frames
    from tests2.contracts.test_inference_runtime_parity import infer_canvas
    evidence = {(op, aspect) for op, _, _, _, aspect in WITNESSES}
    for op, raw_config in list(CASES.items()) + EXTRA:
        config = dict(raw_config)
        for param in get_executor().registry[op].get("parameters", []):
            if "geom" in param["id"] and param["id"] != "keep_geom_type":
                config.setdefault(param["id"], "geometry")
        typed = infer_canvas(canvas_frames(op, frames(op, False), config), "t")
        for aspect in ("names", "types"):
            if getattr(typed, aspect + "_status") == "indeterminate":
                assert (op, aspect) in evidence, f"Manca prova runtime: {op}/{aspect}"
