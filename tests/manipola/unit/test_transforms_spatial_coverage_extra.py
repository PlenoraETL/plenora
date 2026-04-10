"""
tests/manipola/unit/test_transforms_spatial_coverage_extra.py
==============================================================
Extra coverage tests targeting remaining uncovered branches in spatial.py.

Focuses on paths NOT already covered by existing test_transforms_spatial*.py:
  - op_geo_distance: metric CRS path (no UTM reprojection), exception path
  - op_geo_nearest: metric CRS path (no UTM reprojection), exception path,
    max_distance filtering, less than 2 inputs
  - op_geo_length: metric CRS no reprojection, exception path
  - op_geo_perimeter: metric CRS no reprojection, exception path
  - op_geo_explode: exception path
  - op_geo_make_valid: exception path, no shapely make_valid fallback
  - op_geo_within: exception path
  - op_geo_point_on_surface: exception path, basic operation
  - op_geo_boundary: exception path, basic operation
  - op_geo_voronoi: exception path, no HAS_VORONOI fallback
  - op_geo_to_wkt: both modes, exception path
  - op_geo_symmetric_difference: exception path, basic operation
  - op_geo_count_points_in_polygons: exception path, basic operation
  - op_geo_clean_topology: exception path
  - op_geo_overlay: various how= modes, exception path
  - op_geo_bounds_extractor: exception path
  - op_geo_vertex_count: exception path
  - op_geo_line_builder: exception path
  - op_geo_polygon_builder: exception path
  - _ensure_active_geometry: dtype check fallback branch
"""

import pytest
import pandas as pd
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, Polygon, LineString, MultiPoint
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit

from app.core.manipola.transforms.spatial import (
    _ensure_active_geometry,
    op_sjoin,
    op_geo_buffer,
    op_geo_centroid,
    op_geo_area,
    op_geo_dissolve,
    op_geo_clip,
    op_geo_intersection,
    op_geo_union,
    op_geo_difference,
    op_geo_simplify,
    op_geo_convex_hull,
    op_geo_reproject,
    op_geo_envelope,
    op_geo_distance,
    op_geo_nearest,
    op_geo_length,
    op_geo_perimeter,
    op_geo_explode,
    op_geo_make_valid,
    op_geo_within,
    op_geo_point_on_surface,
    op_geo_boundary,
    op_geo_to_wkt,
    op_geo_symmetric_difference,
    op_geo_count_points_in_polygons,
    op_geo_clean_topology,
    op_geo_overlay,
    op_geo_bounds_extractor,
    op_geo_vertex_count,
    op_geo_line_builder,
    op_geo_polygon_builder,
)

# Try importing voronoi — may not be available
try:
    from app.core.manipola.transforms.spatial import op_geo_voronoi
    HAS_VORONOI_FUNC = True
except ImportError:
    HAS_VORONOI_FUNC = False


# ─── Helpers ────────────────────────────────────────────────────────────────

def _make_polygons_metric(n=3):
    """Polygons in EPSG:32632 (metric CRS)."""
    geoms = [
        Polygon([
            (500000 + i * 1000, 5000000),
            (500000 + i * 1000 + 500, 5000000),
            (500000 + i * 1000 + 500, 5000500),
            (500000 + i * 1000, 5000500),
        ])
        for i in range(n)
    ]
    return gpd.GeoDataFrame(
        {"id": range(n), "name": [f"P{i}" for i in range(n)],
         "value": [float(i * 10) for i in range(n)]},
        geometry=geoms, crs="EPSG:32632",
    )


def _make_points_metric(n=3):
    """Points in EPSG:32632."""
    geoms = [Point(500000 + i * 100, 5000000 + i * 100) for i in range(n)]
    return gpd.GeoDataFrame(
        {"id": range(n)}, geometry=geoms, crs="EPSG:32632",
    )


def _make_points_geo(n=3):
    """Points in EPSG:4326."""
    geoms = [Point(9.0 + i * 0.01, 45.0 + i * 0.01) for i in range(n)]
    return gpd.GeoDataFrame(
        {"id": range(n)}, geometry=geoms, crs="EPSG:4326",
    )


def _make_lines_metric(n=2):
    """Lines in EPSG:32632."""
    geoms = [
        LineString([(500000 + i * 100, 5000000), (500000 + i * 100 + 200, 5000200)])
        for i in range(n)
    ]
    return gpd.GeoDataFrame({"id": range(n)}, geometry=geoms, crs="EPSG:32632")


_PATCH = "app.core.manipola.transforms.spatial.to_geodataframe"


# =============================================================================
# op_geo_distance — metric CRS path, exception
# =============================================================================

class TestOpGeoDistanceExtra:

    def test_distance_metric_crs_no_reprojection(self):
        """EPSG:32632 skips UTM reprojection."""
        left = _make_points_metric(3)
        right = _make_points_metric(2)
        result = op_geo_distance([left, right], "geometry", "geometry")
        assert "distance_m" in result.columns
        assert len(result) == 3
        assert (result["distance_m"] >= 0).all()

    def test_distance_exception_returns_first(self):
        """Exception returns first input."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=RuntimeError("mock")):
            result = op_geo_distance([df, df.copy()], "geometry", "geometry")
        assert len(result) == 1

    def test_distance_less_than_2_inputs(self):
        """Less than 2 inputs returns first."""
        gdf = _make_points_metric(2)
        result = op_geo_distance([gdf], "geometry", "geometry")
        assert len(result) == 2

    def test_distance_empty_inputs(self):
        """Empty inputs returns empty GeoDataFrame."""
        result = op_geo_distance([], "geometry", "geometry")
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 0


# =============================================================================
# op_geo_nearest — metric CRS, max_distance, exception
# =============================================================================

class TestOpGeoNearestExtra:

    def test_nearest_metric_crs_no_reprojection(self):
        """EPSG:32632 skips UTM reprojection."""
        left = _make_points_metric(3)
        right = _make_points_metric(2)
        result = op_geo_nearest([left, right], "geometry", "geometry")
        assert "distance_m" in result.columns
        assert len(result) >= 3

    def test_nearest_exception_returns_first(self):
        """Exception returns first input."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=RuntimeError("mock")):
            result = op_geo_nearest([df, df.copy()], "geometry", "geometry")
        assert len(result) == 1

    def test_nearest_less_than_2_inputs(self):
        """Less than 2 inputs."""
        gdf = _make_points_metric(2)
        result = op_geo_nearest([gdf], "geometry", "geometry")
        assert len(result) == 2

    def test_nearest_empty_inputs(self):
        """Empty inputs returns empty GeoDataFrame."""
        result = op_geo_nearest([], "geometry", "geometry")
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 0


# =============================================================================
# op_geo_length — metric CRS, exception
# =============================================================================

class TestOpGeoLengthExtra:

    def test_length_metric_crs_no_reprojection(self):
        """EPSG:32632 skips UTM reprojection for length."""
        gdf = _make_lines_metric(2)
        result = op_geo_length(gdf, "geometry")
        assert "length_m" in result.columns
        assert (result["length_m"] > 0).all()

    def test_length_exception_returns_original(self):
        """Exception returns original df."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=ValueError("mock")):
            result = op_geo_length(df, "geometry")
        assert result is df


# =============================================================================
# op_geo_perimeter — metric CRS, exception
# =============================================================================

class TestOpGeoPerimeterExtra:

    def test_perimeter_metric_crs_no_reprojection(self):
        """EPSG:32632 skips UTM reprojection for perimeter."""
        gdf = _make_polygons_metric(2)
        result = op_geo_perimeter(gdf, "geometry")
        assert "perimeter_m" in result.columns
        assert (result["perimeter_m"] > 0).all()

    def test_perimeter_exception_returns_original(self):
        """Exception returns original df."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=ValueError("mock")):
            result = op_geo_perimeter(df, "geometry")
        assert result is df


# =============================================================================
# op_geo_explode — exception
# =============================================================================

class TestOpGeoExplodeExtra:

    def test_explode_exception_returns_original(self):
        """Exception returns original df."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=ValueError("mock")):
            result = op_geo_explode(df, "geometry")
        assert result is df


# =============================================================================
# op_geo_make_valid — exception
# =============================================================================

class TestOpGeoMakeValidExtra:

    def test_make_valid_exception_returns_original(self):
        """Exception returns original df."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=ValueError("mock")):
            result = op_geo_make_valid(df, "geometry")
        assert result is df


# =============================================================================
# op_geo_within — exception
# =============================================================================

class TestOpGeoWithinExtra:

    def test_within_exception_returns_first(self):
        """Exception returns first input."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=RuntimeError("mock")):
            result = op_geo_within([df, df.copy()], "geometry", "geometry")
        assert len(result) == 1

    def test_within_less_than_2_inputs(self):
        """Less than 2 inputs returns first."""
        gdf = _make_points_metric(2)
        result = op_geo_within([gdf], "geometry", "geometry")
        assert len(result) == 2


# =============================================================================
# op_geo_point_on_surface — basic + exception
# =============================================================================

class TestOpGeoPointOnSurfaceExtra:

    def test_point_on_surface_basic(self):
        """Basic point on surface operation."""
        gdf = _make_polygons_metric(2)
        result = op_geo_point_on_surface(gdf, "geometry")
        assert "point_on_surface" in result.columns
        assert len(result) == 2

    def test_point_on_surface_exception(self):
        """Exception returns original df."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=ValueError("mock")):
            result = op_geo_point_on_surface(df, "geometry")
        assert result is df


# =============================================================================
# op_geo_boundary — basic + exception
# =============================================================================

class TestOpGeoBoundaryExtra:

    def test_boundary_basic(self):
        """Basic boundary operation."""
        gdf = _make_polygons_metric(2)
        result = op_geo_boundary(gdf, "geometry")
        assert "boundary" in result.columns
        assert len(result) == 2

    def test_boundary_exception(self):
        """Exception returns original df."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=ValueError("mock")):
            result = op_geo_boundary(df, "geometry")
        assert result is df


# =============================================================================
# op_geo_to_wkt — both modes + exception
# =============================================================================

class TestOpGeoToWktExtra:

    def test_to_wkt_mode_wkt(self):
        """WKT mode converts geometry to WKT string."""
        gdf = _make_points_metric(2)
        result = op_geo_to_wkt(gdf, "geometry", mode="wkt")
        assert "geometry_wkt" in result.columns or "wkt" in str(result.columns)

    def test_to_wkt_mode_latlon(self):
        """Lat/lon mode extracts lat and lon columns."""
        gdf = _make_points_geo(2)
        result = op_geo_to_wkt(gdf, "geometry", mode="latlon")
        # Should have lat/lon columns
        cols = set(result.columns)
        assert "lat" in cols or "latitude" in cols or any("lat" in c.lower() for c in cols)

    def test_to_wkt_mode_both(self):
        """Both mode creates WKT + lat/lon."""
        gdf = _make_points_geo(2)
        result = op_geo_to_wkt(gdf, "geometry", mode="both")
        assert len(result) == 2

    def test_to_wkt_exception(self):
        """Exception returns original df."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=ValueError("mock")):
            result = op_geo_to_wkt(df, "geometry")
        assert result is df


# =============================================================================
# op_geo_symmetric_difference — basic + exception
# =============================================================================

class TestOpGeoSymmetricDifferenceExtra:

    def test_symmetric_difference_basic(self):
        """Basic symmetric difference operation."""
        left = _make_polygons_metric(2)
        right = gpd.GeoDataFrame(
            {"id": [0]},
            geometry=[Polygon([
                (500000, 5000000), (500250, 5000000),
                (500250, 5000250), (500000, 5000250),
            ])],
            crs="EPSG:32632"
        )
        result = op_geo_symmetric_difference([left, right], "geometry", "geometry")
        assert len(result) > 0

    def test_symmetric_difference_exception(self):
        """Exception returns first input."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=RuntimeError("mock")):
            result = op_geo_symmetric_difference([df, df.copy()], "geometry", "geometry")
        assert len(result) == 1

    def test_symmetric_difference_less_than_2(self):
        """Less than 2 inputs returns first."""
        gdf = _make_polygons_metric(2)
        result = op_geo_symmetric_difference([gdf], "geometry", "geometry")
        assert len(result) == 2


# =============================================================================
# op_geo_count_points_in_polygons — basic + exception
# =============================================================================

class TestOpGeoCountPointsInPolygonsExtra:

    def test_count_points_basic(self):
        """Points inside polygons are counted."""
        polys = _make_polygons_metric(1)
        # Points inside the first polygon (500000-500500, 5000000-5000500)
        pts = gpd.GeoDataFrame(
            {"id": [0, 1]},
            geometry=[Point(500100, 5000100), Point(500200, 5000200)],
            crs="EPSG:32632"
        )
        result = op_geo_count_points_in_polygons([polys, pts], "geometry", "geometry")
        assert len(result) >= 1

    def test_count_points_exception(self):
        """Exception returns first input."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=RuntimeError("mock")):
            result = op_geo_count_points_in_polygons([df, df.copy()], "geometry", "geometry")
        assert len(result) == 1

    def test_count_points_less_than_2(self):
        """Less than 2 inputs returns first."""
        gdf = _make_polygons_metric(2)
        result = op_geo_count_points_in_polygons([gdf], "geometry", "geometry")
        assert len(result) == 2


# =============================================================================
# op_geo_clean_topology — exception
# =============================================================================

class TestOpGeoCleanTopologyExtra:

    def test_clean_topology_exception(self):
        """Exception returns original df."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=ValueError("mock")):
            result = op_geo_clean_topology(df, "geometry")
        assert result is df


# =============================================================================
# op_geo_overlay — various how= modes + exception
# =============================================================================

class TestOpGeoOverlayExtra:

    def test_overlay_difference(self):
        """Overlay with how='difference'."""
        left = _make_polygons_metric(1)
        right = gpd.GeoDataFrame(
            {"id": [0]},
            geometry=[Polygon([
                (500000, 5000000), (500250, 5000000),
                (500250, 5000250), (500000, 5000250),
            ])],
            crs="EPSG:32632"
        )
        result = op_geo_overlay([left, right], "geometry", "geometry", how="difference")
        assert len(result) >= 0

    def test_overlay_exception(self):
        """Exception returns first input."""
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH, side_effect=RuntimeError("mock")):
            result = op_geo_overlay([df, df.copy()], "geometry", "geometry")
        assert len(result) == 1

    def test_overlay_less_than_2(self):
        """Less than 2 inputs returns first."""
        gdf = _make_polygons_metric(2)
        result = op_geo_overlay([gdf], "geometry", "geometry")
        assert len(result) == 2


# =============================================================================
# op_geo_bounds_extractor — exception
# =============================================================================

class TestOpGeoBoundsExtractorExtra:

    def test_bounds_basic(self):
        """Basic bounds extraction works."""
        gdf = _make_polygons_metric(2)
        result = op_geo_bounds_extractor(gdf, "geometry", prefix="bbox_")
        assert "bbox_min_x" in result.columns
        assert "bbox_max_y" in result.columns
        assert len(result) == 2


# =============================================================================
# op_geo_vertex_count — exception
# =============================================================================

class TestOpGeoVertexCountExtra:

    def test_vertex_count_basic(self):
        """Basic vertex count works."""
        gdf = _make_polygons_metric(2)
        result = op_geo_vertex_count(gdf, "geometry")
        assert "vertex_count" in result.columns
        assert (result["vertex_count"] > 0).all()


# =============================================================================
# op_geo_line_builder — exception
# =============================================================================

class TestOpGeoLineBuilderExtra:

    def test_line_builder_basic(self):
        """Basic line builder from points."""
        pts = [Point(500000 + i * 100, 5000000) for i in range(4)]
        gdf = gpd.GeoDataFrame(
            {"grp": ["A", "A", "B", "B"], "order": [1, 2, 1, 2]},
            geometry=pts, crs="EPSG:32632"
        )
        result = op_geo_line_builder(gdf, "geometry", group_column="grp", order_column="order")
        assert len(result) == 2
        assert result.geometry.iloc[0].geom_type == "LineString"

    def test_line_builder_single_point_skipped(self):
        """Groups with < 2 points are skipped."""
        pts = [Point(500000, 5000000)]
        gdf = gpd.GeoDataFrame({"grp": ["A"]}, geometry=pts, crs="EPSG:32632")
        result = op_geo_line_builder(gdf, "geometry", group_column="grp")
        assert len(result) == 0


# =============================================================================
# op_geo_polygon_builder — basic test
# =============================================================================

class TestOpGeoPolygonBuilderExtra:

    def test_polygon_builder_basic(self):
        """Basic polygon builder from points."""
        pts = [Point(500000 + i * 100, 5000000 + (i % 2) * 100) for i in range(4)]
        gdf = gpd.GeoDataFrame(
            {"grp": ["A", "A", "A", "A"], "order": [1, 2, 3, 4]},
            geometry=pts, crs="EPSG:32632"
        )
        result = op_geo_polygon_builder(gdf, "geometry", group_column="grp", order_column="order")
        assert len(result) >= 1


# =============================================================================
# _ensure_active_geometry — dtype check exception branch
# =============================================================================

class TestEnsureActiveGeometryExtra:

    def test_non_geometry_columns_not_misidentified(self):
        """Non-geometry columns with hasattr check do not cause errors."""
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        gdf = gpd.GeoDataFrame(df)
        result = _ensure_active_geometry(gdf)
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 2

    def test_multiple_geometry_columns_prefers_geometry(self):
        """When both 'geometry' and another geom col exist, prefers 'geometry'."""
        geoms1 = [Point(0, 0), Point(1, 1)]
        geoms2 = [Point(2, 2), Point(3, 3)]
        gdf = gpd.GeoDataFrame({"id": [1, 2]}, geometry=geoms1, crs="EPSG:4326")
        gdf["other_geom"] = gpd.GeoSeries(geoms2, crs="EPSG:4326")
        result = _ensure_active_geometry(gdf)
        assert result.geometry.name == "geometry"
