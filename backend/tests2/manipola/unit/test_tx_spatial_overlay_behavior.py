"""
Behavioral unit tests for app/core/manipola/transforms/spatial_overlay.py

Cover the public overlay / clip / spatial-join operators:
- happy path of every public op_* function
- the "< 2 inputs" short-circuit branches (with and without input)
- conversion of plain pandas DataFrames (WKT) -> GeoDataFrame
- fail-closed re-raise on internal failure
- count_points_in_polygons: empty-input branch, aggregation branch
- op_geo_overlay: invalid `how` falls back to intersection
"""

from __future__ import annotations

from unittest.mock import patch

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from app.core.manipola.transforms.spatial_overlay import (
    op_geo_clip,
    op_geo_count_points_in_polygons,
    op_geo_difference,
    op_geo_intersection,
    op_geo_overlay,
    op_geo_symmetric_difference,
    op_geo_union,
    op_sjoin,
)


pytestmark = [pytest.mark.unit, pytest.mark.manipola]


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------

def _square(x0, y0, side=1.0):
    return Polygon(
        [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]
    )


@pytest.fixture
def poly_a():
    # unit square at origin
    return gpd.GeoDataFrame(
        {"a_id": [1]},
        geometry=[_square(0, 0, 2.0)],
        crs="EPSG:4326",
    )


@pytest.fixture
def poly_b():
    # overlaps poly_a on the right half
    return gpd.GeoDataFrame(
        {"b_id": [9]},
        geometry=[_square(1, 0, 2.0)],
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# op_sjoin
# ---------------------------------------------------------------------------

def test_sjoin_happy_path_within_brings_right_attributes():
    points = gpd.GeoDataFrame(
        {"pid": [1]},
        geometry=[Point(0.5, 0.5)],
        crs="EPSG:4326",
    )
    polys = gpd.GeoDataFrame(
        {"zone": ["Z"]},
        geometry=[_square(0, 0)],
        crs="EPSG:4326",
    )
    result = op_sjoin([points, polys], "geometry", "geometry", predicate="within")
    assert "zone" in result.columns
    assert result["zone"].iloc[0] == "Z"
    # index_right must have been dropped
    assert "index_right" not in result.columns


def test_sjoin_converts_plain_dataframe_via_wkt():
    # plain pandas DataFrames carrying WKT -> must be converted internally
    left = pd.DataFrame({"pid": [1], "geometry": ["POINT (0.5 0.5)"]})
    right = pd.DataFrame({"zone": ["Z"], "geometry": ["POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))"]})
    result = op_sjoin([left, right], "geometry", "geometry", predicate="within")
    assert "zone" in result.columns
    assert result["zone"].iloc[0] == "Z"


def test_sjoin_fewer_than_two_inputs_returns_single_input():
    only = gpd.GeoDataFrame({"x": [1]}, geometry=[Point(0, 0)], crs="EPSG:4326")
    out = op_sjoin([only], "geometry", "geometry")
    assert out is only


def test_sjoin_empty_inputs_returns_empty_geodataframe():
    out = op_sjoin([], "geometry", "geometry")
    assert len(out) == 0


def test_sjoin_reraises_on_internal_failure():
    points = gpd.GeoDataFrame({"pid": [1]}, geometry=[Point(0.5, 0.5)], crs="EPSG:4326")
    polys = gpd.GeoDataFrame({"zone": ["Z"]}, geometry=[_square(0, 0)], crs="EPSG:4326")
    with patch(
        "app.core.manipola.transforms.spatial_overlay.geodataframe_sjoin",
        side_effect=RuntimeError("boom-sjoin"),
    ):
        with pytest.raises(RuntimeError, match="boom-sjoin"):
            op_sjoin([points, polys], "geometry", "geometry")


# ---------------------------------------------------------------------------
# op_geo_clip
# ---------------------------------------------------------------------------

def test_clip_happy_path_keeps_only_masked_region(poly_a):
    mask = gpd.GeoDataFrame(
        {"m": [1]},
        geometry=[_square(0, 0, 1.0)],
        crs="EPSG:4326",
    )
    result = op_geo_clip([poly_a, mask], "geometry", "geometry")
    assert len(result) == 1
    # clipped area must be smaller than (or equal to) the original
    assert result.geometry.iloc[0].area <= poly_a.geometry.iloc[0].area


def test_clip_fewer_than_two_inputs_returns_single_input(poly_a):
    out = op_geo_clip([poly_a], "geometry", "geometry")
    assert out is poly_a


def test_clip_empty_inputs_returns_empty_geodataframe():
    out = op_geo_clip([], "geometry", "geometry")
    assert len(out) == 0


def test_clip_reraises_on_internal_failure(poly_a):
    mask = gpd.GeoDataFrame({"m": [1]}, geometry=[_square(0, 0, 1.0)], crs="EPSG:4326")
    with patch(
        "app.core.manipola.transforms.spatial_overlay.geodataframe_clip",
        side_effect=RuntimeError("boom-clip"),
    ):
        with pytest.raises(RuntimeError, match="boom-clip"):
            op_geo_clip([poly_a, mask], "geometry", "geometry")


# ---------------------------------------------------------------------------
# op_geo_intersection
# ---------------------------------------------------------------------------

def test_intersection_happy_path(poly_a, poly_b):
    result = op_geo_intersection([poly_a, poly_b], "geometry", "geometry")
    assert len(result) >= 1
    total = sum(g.area for g in result.geometry)
    assert total > 0
    # intersection area must be <= each input area
    assert total <= poly_a.geometry.iloc[0].area + 1e-9


def test_intersection_fewer_than_two_inputs(poly_a):
    assert op_geo_intersection([poly_a], "geometry", "geometry") is poly_a
    assert len(op_geo_intersection([], "geometry", "geometry")) == 0


def test_intersection_reraises_on_internal_failure(poly_a, poly_b):
    with patch(
        "app.core.manipola.transforms.spatial_overlay.geodataframe_overlay",
        side_effect=RuntimeError("boom-intersection"),
    ):
        with pytest.raises(RuntimeError, match="boom-intersection"):
            op_geo_intersection([poly_a, poly_b], "geometry", "geometry")


# ---------------------------------------------------------------------------
# op_geo_union
# ---------------------------------------------------------------------------

def test_union_happy_path_covers_both(poly_a, poly_b):
    result = op_geo_union([poly_a, poly_b], "geometry", "geometry")
    assert len(result) >= 1
    total = sum(g.area for g in result.geometry)
    # union must cover at least one full input square
    assert total >= poly_a.geometry.iloc[0].area - 1e-9


def test_union_fewer_than_two_inputs(poly_a):
    assert op_geo_union([poly_a], "geometry", "geometry") is poly_a
    assert len(op_geo_union([], "geometry", "geometry")) == 0


# ---------------------------------------------------------------------------
# op_geo_difference
# ---------------------------------------------------------------------------

def test_difference_happy_path_subtracts(poly_a, poly_b):
    result = op_geo_difference([poly_a, poly_b], "geometry", "geometry")
    total = sum(g.area for g in result.geometry)
    # A minus the overlapping B must be strictly less than full A
    assert total < poly_a.geometry.iloc[0].area


def test_difference_fewer_than_two_inputs(poly_a):
    assert op_geo_difference([poly_a], "geometry", "geometry") is poly_a
    assert len(op_geo_difference([], "geometry", "geometry")) == 0


# ---------------------------------------------------------------------------
# op_geo_symmetric_difference
# ---------------------------------------------------------------------------

def test_symmetric_difference_happy_path(poly_a, poly_b):
    result = op_geo_symmetric_difference([poly_a, poly_b], "geometry", "geometry")
    total = sum(g.area for g in result.geometry)
    # XOR removes the shared region, so area is below the sum of both
    both = poly_a.geometry.iloc[0].area + poly_b.geometry.iloc[0].area
    assert 0 < total < both


def test_symmetric_difference_fewer_than_two_inputs(poly_a):
    assert op_geo_symmetric_difference([poly_a], "geometry", "geometry") is poly_a
    assert len(op_geo_symmetric_difference([], "geometry", "geometry")) == 0


def test_symmetric_difference_reraises_on_internal_failure(poly_a, poly_b):
    with patch(
        "app.core.manipola.transforms.spatial_overlay.geodataframe_overlay",
        side_effect=RuntimeError("boom-symdiff"),
    ):
        with pytest.raises(RuntimeError, match="boom-symdiff"):
            op_geo_symmetric_difference([poly_a, poly_b], "geometry", "geometry")


# ---------------------------------------------------------------------------
# op_geo_count_points_in_polygons
# ---------------------------------------------------------------------------

def test_count_points_happy_path():
    polys = gpd.GeoDataFrame(
        {"name": ["P0", "P1"]},
        geometry=[_square(0, 0), _square(10, 10)],
        crs="EPSG:4326",
    )
    points = gpd.GeoDataFrame(
        {"v": [5, 7, 3]},
        geometry=[Point(0.2, 0.2), Point(0.8, 0.8), Point(10.5, 10.5)],
        crs="EPSG:4326",
    )
    result = op_geo_count_points_in_polygons(
        [polys, points], "geometry", "geometry", count_column="n"
    )
    assert "n" in result.columns
    assert result["n"].dtype == int or str(result["n"].dtype).startswith("int")
    # 2 points in P0, 1 point in P1
    assert sorted(result["n"].tolist()) == [1, 2]


def test_count_points_with_aggregation_creates_named_column():
    polys = gpd.GeoDataFrame(
        {"name": ["P0"]},
        geometry=[_square(0, 0)],
        crs="EPSG:4326",
    )
    points = gpd.GeoDataFrame(
        {"v": [5, 7]},
        geometry=[Point(0.2, 0.2), Point(0.8, 0.8)],
        crs="EPSG:4326",
    )
    result = op_geo_count_points_in_polygons(
        [polys, points],
        "geometry",
        "geometry",
        count_column="n",
        aggregate_column="v",
        aggregate_func="sum",
    )
    assert "v_sum" in result.columns
    assert result["v_sum"].iloc[0] == 12


def test_count_points_unknown_aggregate_func_defaults_to_sum():
    polys = gpd.GeoDataFrame({"name": ["P0"]}, geometry=[_square(0, 0)], crs="EPSG:4326")
    points = gpd.GeoDataFrame(
        {"v": [4, 6]},
        geometry=[Point(0.2, 0.2), Point(0.8, 0.8)],
        crs="EPSG:4326",
    )
    result = op_geo_count_points_in_polygons(
        [polys, points],
        "geometry",
        "geometry",
        aggregate_column="v",
        aggregate_func="not_a_func",
    )
    # invalid func falls back to "sum" -> column suffix _sum
    assert "v_sum" in result.columns
    assert result["v_sum"].iloc[0] == 10


def test_count_points_empty_point_input_sets_zero_count():
    polys = gpd.GeoDataFrame({"name": ["P0"]}, geometry=[_square(0, 0)], crs="EPSG:4326")
    empty_points = gpd.GeoDataFrame(
        {"v": pd.Series([], dtype="float64")},
        geometry=gpd.GeoSeries([], crs="EPSG:4326"),
        crs="EPSG:4326",
    )
    result = op_geo_count_points_in_polygons(
        [polys, empty_points], "geometry", "geometry", count_column="n"
    )
    assert "n" in result.columns
    assert result["n"].iloc[0] == 0


def test_count_points_polygon_with_no_points_gets_zero_filled():
    polys = gpd.GeoDataFrame(
        {"name": ["P0", "empty"]},
        geometry=[_square(0, 0), _square(100, 100)],
        crs="EPSG:4326",
    )
    points = gpd.GeoDataFrame(
        {"v": [1]},
        geometry=[Point(0.5, 0.5)],
        crs="EPSG:4326",
    )
    result = op_geo_count_points_in_polygons(
        [polys, points], "geometry", "geometry", count_column="n"
    )
    # the far polygon must be zero-filled, not NaN
    assert sorted(result["n"].tolist()) == [0, 1]


def test_count_points_fewer_than_two_inputs(poly_a):
    assert op_geo_count_points_in_polygons([poly_a], "geometry", "geometry") is poly_a
    assert len(op_geo_count_points_in_polygons([], "geometry", "geometry")) == 0


# ---------------------------------------------------------------------------
# op_geo_overlay
# ---------------------------------------------------------------------------

def test_overlay_intersection_keeps_both_attributes(poly_a, poly_b):
    result = op_geo_overlay(
        [poly_a, poly_b], "geometry", "geometry", how="intersection"
    )
    assert "a_id" in result.columns
    assert "b_id" in result.columns
    assert len(result) >= 1


def test_overlay_invalid_how_falls_back_to_intersection(poly_a, poly_b):
    bad = op_geo_overlay([poly_a, poly_b], "geometry", "geometry", how="garbage")
    ref = op_geo_overlay([poly_a, poly_b], "geometry", "geometry", how="intersection")
    # fallback must behave exactly like intersection
    assert len(bad) == len(ref)
    assert set(bad.columns) == set(ref.columns)


def test_overlay_union_mode(poly_a, poly_b):
    result = op_geo_overlay([poly_a, poly_b], "geometry", "geometry", how="union")
    total = sum(g.area for g in result.geometry)
    assert total >= poly_a.geometry.iloc[0].area - 1e-9


def test_overlay_fewer_than_two_inputs(poly_a):
    assert op_geo_overlay([poly_a], "geometry", "geometry") is poly_a
    assert len(op_geo_overlay([], "geometry", "geometry")) == 0


def test_overlay_reraises_on_internal_failure(poly_a, poly_b):
    with patch(
        "app.core.manipola.transforms.spatial_overlay.geodataframe_overlay",
        side_effect=RuntimeError("boom-overlay"),
    ):
        with pytest.raises(RuntimeError, match="boom-overlay"):
            op_geo_overlay([poly_a, poly_b], "geometry", "geometry")
