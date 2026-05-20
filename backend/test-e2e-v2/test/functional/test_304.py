"""test_304 - MANIPOLA registry-wide negative execution coverage.

Scenario covered:
- every transform in the Manipola registry is exercised on an invalid or
  missing-column configuration;
- each transform must either raise a readable exception or return a DataFrame
  object without expanding rows unexpectedly.
"""

from __future__ import annotations

import pandas as pd

from _e2e import assert_true
from _manipola_runtime_full import assert_non_empty_exception, registry_transform_ids, run_silent, run_transform


def _bad_params(transform_id: str) -> dict:
    missing = "__missing_column__"
    common = {
        "column": missing,
        "columns": [missing],
        "left_on": missing,
        "right_on": missing,
        "left_keys": [missing],
        "right_keys": [missing],
        "left_geom": missing,
        "right_geom": missing,
        "left_geom_column": missing,
        "right_geom_column": missing,
        "geom_column": missing,
        "geometry_column": missing,
        "input_geom": missing,
        "mask_geom": missing,
        "point_geom_column": missing,
        "polygon_geom_column": missing,
        "lat_column": missing,
        "lon_column": missing,
        "wkt_column": missing,
        "group_column": missing,
        "order_column": missing,
        "id_column": missing,
        "value_columns": [missing],
        "id_columns": [missing],
        "subset": [missing],
        "compare_columns": [missing],
        "aggregate_column": missing,
        "by_column": missing,
        "formula": f"{missing} * 2",
        "new_column": "out",
        "output_column": "out",
        "renames": [{"old_name": missing, "new_name": "renamed"}],
        "aggregations": [{"column": missing, "function": "sum", "alias": "sum_missing"}],
        "maskings": [{"column": missing, "mask_type": "email"}],
        "mapping": {"x": "y"},
        "conditions": [{"operator": "==", "value": "x", "result": "hit"}],
        "default_value": "miss",
        "target_type": "int",
        "mode": "latlon",
        "distance": -1,
        "tolerance": -1,
        "target_crs": "EPSG:3857",
        "source_crs": "EPSG:4326",
        "how": "inner",
        "predicate": "intersects",
    }
    if transform_id == "cross_join":
        return {}
    if transform_id == "concat":
        return {"ignore_index": True}
    return common


def test_registry_negative_paths() -> None:
    left = pd.DataFrame([{"id": 1, "value": 10}])
    right = pd.DataFrame([{"id": 2, "value": 20}])
    covered = set()

    for transform_id in sorted(registry_transform_ids()):
        covered.add(transform_id)
        before_rows = len(left)
        try:
            result = run_transform(transform_id, [left, right], _bad_params(transform_id))
        except Exception as exc:
            assert_non_empty_exception(transform_id, exc)
            continue

        assert_true(hasattr(result, "columns"), f"{transform_id} negative path must return a DataFrame-like object")
        assert_true(len(result) <= 100, f"{transform_id} negative path expanded rows beyond a bounded fixture size")

    assert_true(len(covered) >= 70, "negative coverage must exercise the whole registry")


if __name__ == "__main__":
    run_silent(test_registry_negative_paths)
