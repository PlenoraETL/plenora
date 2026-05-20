"""test_303 - MANIPOLA multi-input geospatial transforms execute directly.

Scenario covered:
- every multi-input geo transform missing from the first registry matrix;
- spatial join, clip, overlay variants, distance, nearest, point counting and
  within filtering on deterministic layers.
"""

from __future__ import annotations

from _e2e import assert_equal, assert_true
from _manipola_runtime_full import (
    overlapping_polygon_frame,
    point_frame,
    polygon_frame,
    run_silent,
    run_transform,
)


def test_multi_input_geo_transforms() -> None:
    points = point_frame()
    polygons = polygon_frame()
    overlaps = overlapping_polygon_frame()

    sjoin = run_transform("sjoin", [points, polygons], {"left_geom": "geom", "right_geom": "geom", "predicate": "within", "how": "inner"})
    assert_equal(len(sjoin), 4, "sjoin must match all fixture points to containing polygons")

    clipped = run_transform("geo_clip", [overlaps, polygons.iloc[[0]]], {"input_geom": "geom", "mask_geom": "geom"})
    assert_true(len(clipped) >= 1, "geo_clip must keep geometries intersecting the mask")

    intersection = run_transform("geo_intersection", [overlaps.iloc[[0]], overlaps.iloc[[1]]], {"left_geom": "geom", "right_geom": "geom"})
    assert_equal(len(intersection), 1, "geo_intersection must return the overlap area")
    assert_true(intersection.geometry.iloc[0].area > 0, "geo_intersection must create positive-area overlap geometry")

    union = run_transform("geo_union", [overlaps.iloc[[0]], overlaps.iloc[[1]]], {"left_geom": "geom", "right_geom": "geom"})
    assert_true(len(union) >= 3, "geo_union must split both overlapping layers into union pieces")

    difference = run_transform("geo_difference", [overlaps.iloc[[0]], overlaps.iloc[[1]]], {"left_geom": "geom", "right_geom": "geom"})
    assert_equal(len(difference), 1, "geo_difference must return left-only area")
    assert_true(difference.geometry.iloc[0].area > 0, "geo_difference must create positive-area geometry")

    symdiff = run_transform("geo_symmetric_difference", [overlaps.iloc[[0]], overlaps.iloc[[1]]], {"left_geom_column": "geom", "right_geom_column": "geom"})
    assert_true(len(symdiff) >= 2, "geo_symmetric_difference must return non-overlapping pieces from both layers")

    counted = run_transform(
        "geo_count_points_in_polygons",
        [polygons, points],
        {"polygon_geom_column": "geom", "point_geom_column": "geom", "count_column": "n_points", "aggregate_column": "value", "aggregate_func": "sum"},
    )
    assert_equal(counted["n_points"].tolist(), [3, 1], "geo_count_points_in_polygons must count points per polygon")
    assert_equal(counted["value_sum"].tolist(), [60, 40], "geo_count_points_in_polygons must aggregate point values")

    overlay = run_transform("geo_overlay", [overlaps.iloc[[0]], overlaps.iloc[[1]]], {"left_geom_column": "geom", "right_geom_column": "geom", "how": "intersection", "keep_geom_type": True})
    assert_equal(len(overlay), 1, "geo_overlay intersection must return the overlap piece")

    distance = run_transform("geo_distance", [points.iloc[[0]], points.iloc[[1]]], {"left_geom_column": "geom", "right_geom_column": "geom", "output_column": "dist", "metric_crs": "EPSG:3857"})
    assert_true(distance["dist"].iloc[0] > 100000, "geo_distance must compute projected metric distance")

    nearest = run_transform("geo_nearest", [points.iloc[[0]], points.iloc[[1, 2]]], {"left_geom_column": "geom", "right_geom_column": "geom", "output_column": "dist", "metric_crs": "EPSG:3857"})
    assert_equal(len(nearest), 1, "geo_nearest must keep one left row")
    assert_true(nearest["dist"].iloc[0] > 100000, "geo_nearest must attach distance to nearest feature")

    within = run_transform("geo_within", [points, polygons.iloc[[0]]], {"left_geom_column": "geom", "right_geom_column": "geom"})
    assert_equal(within["id"].tolist(), [1, 2, 3], "geo_within must retain only points inside the selected polygon")


if __name__ == "__main__":
    run_silent(test_multi_input_geo_transforms)
