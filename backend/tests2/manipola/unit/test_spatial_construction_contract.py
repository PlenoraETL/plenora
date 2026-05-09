from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from app.core.manipola.transforms import spatial
from app.core.manipola.transforms.spatial_construction import (
    op_geo_line_builder,
    op_geo_polygon_builder,
)


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]


def test_spatial_keeps_public_construction_imports():
    assert spatial.op_geo_line_builder is op_geo_line_builder
    assert spatial.op_geo_polygon_builder is op_geo_polygon_builder


def test_line_builder_orders_points_and_preserves_crs():
    gdf = gpd.GeoDataFrame(
        {
            "route": ["A", "A", "A"],
            "seq": [2, 1, 3],
        },
        geometry=[Point(1, 0), Point(0, 0), Point(2, 0)],
        crs="EPSG:4326",
    )

    result = op_geo_line_builder(
        gdf,
        "geometry",
        group_column="route",
        order_column="seq",
    )

    assert list(result.columns) == ["route", "geometry"]
    assert result.crs == gdf.crs
    assert list(result.geometry.iloc[0].coords) == [(0, 0), (1, 0), (2, 0)]


def test_polygon_builder_closes_ring_and_skips_short_groups():
    gdf = gpd.GeoDataFrame(
        {
            "area": ["A", "A", "A", "B", "B"],
            "seq": [1, 2, 3, 1, 2],
        },
        geometry=[
            Point(0, 0),
            Point(1, 0),
            Point(0, 1),
            Point(10, 10),
            Point(11, 10),
        ],
        crs="EPSG:3857",
    )

    result = op_geo_polygon_builder(
        gdf,
        "geometry",
        group_column="area",
        order_column="seq",
    )

    assert result["area"].tolist() == ["A"]
    assert result.crs == gdf.crs
    exterior = list(result.geometry.iloc[0].exterior.coords)
    assert exterior[0] == exterior[-1]
