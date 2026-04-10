"""
tests/manipola/unit/test_transforms_spatial_coverage.py
=======================================================
Additional unit tests targeting uncovered lines in transforms/spatial.py.

Focus areas:
  - _ensure_active_geometry: broken geometry reference, geom column search,
    fallback to first geom col when none is named 'geometry' (lines 74-94)
  - Exception handlers for all op_geo_* / op_sjoin functions
  - Non-geographic CRS paths (buffer, centroid, area without UTM reprojection)
  - op_geo_dissolve: numeric aggfunc with mixed string/numeric columns (line 385)
  - op_geo_reproject: source_crs override, crs=None fallback (lines 637-641)
"""

import pytest
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, Polygon
from unittest.mock import patch

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
)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _make_polygons_metric(n=3):
    """Polygons in EPSG:32632 (metric CRS, no reprojection needed)."""
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


def _gdf_to_df_wkt(gdf):
    df = gdf.copy()
    df["geometry"] = gdf.geometry.to_wkt()
    return pd.DataFrame(df)


# =============================================================================
# _ensure_active_geometry  (lines 74-94)
# =============================================================================

class TestEnsureActiveGeometry:

    def test_valid_geometry_passthrough(self):
        """GDF with valid active geometry returns unchanged."""
        gdf = _make_polygons_metric(2)
        result = _ensure_active_geometry(gdf)
        assert result.geometry.name == "geometry"
        assert len(result) == 2

    def test_broken_geometry_reference_recovered(self):
        """
        When the active geometry name is missing from columns, the function
        should discover a geometry-typed column and set it as active.
        Covers lines 74-75 (except/pass) and 77-92.
        """
        geoms = [Point(9.0, 45.0), Point(9.1, 45.1)]
        gdf = gpd.GeoDataFrame({"id": [1, 2]}, geometry=geoms, crs="EPSG:4326")
        gdf = gdf.rename_geometry("geo_original")
        # Convert through DataFrame to break the geometry reference
        df = pd.DataFrame(gdf)
        broken = gpd.GeoDataFrame(df)
        result = _ensure_active_geometry(broken)
        assert isinstance(result, gpd.GeoDataFrame)
        assert result.geometry.name in result.columns

    def test_no_geom_columns_returns_as_is(self):
        """GDF with no geometry columns at all returns unchanged."""
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        gdf = gpd.GeoDataFrame(df)
        result = _ensure_active_geometry(gdf)
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 2

    def test_prefers_column_named_geometry(self):
        """When multiple geom columns exist, prefer one named 'geometry' (line 88-89)."""
        gdf = _make_polygons_metric(2)
        gdf["other_geom"] = gdf.geometry.centroid
        result = _ensure_active_geometry(gdf)
        assert result.geometry.name == "geometry"

    def test_fallback_to_first_geom_col_when_no_geometry_name(self):
        """
        When no column is named 'geometry', use the first geom column found.
        Covers line 90-91 (geom_cols[0] fallback).
        """
        geoms = [Point(9.0, 45.0), Point(9.1, 45.1)]
        gdf = gpd.GeoDataFrame({"id": [1, 2]}, geometry=geoms, crs="EPSG:4326")
        # Rename geometry to something else
        gdf = gdf.rename_geometry("my_special_geom")
        # Convert through DataFrame to break active geometry reference
        df = pd.DataFrame(gdf)
        broken = gpd.GeoDataFrame(df)
        result = _ensure_active_geometry(broken)
        # Should have recovered using the first geometry column found
        assert result.geometry.name in result.columns


# =============================================================================
# Exception handlers — force errors via mock to cover except branches
# =============================================================================

_PATCH_TARGET = "app.core.manipola.transforms.spatial.to_geodataframe"


class TestOpSjoinException:
    """Covers lines 145-147."""

    def test_error_returns_first_input(self):
        df = pd.DataFrame({"geometry": ["POINT (9 45)"], "id": [1]})
        with patch(_PATCH_TARGET, side_effect=RuntimeError("mock error")):
            result = op_sjoin([df, df.copy()], "geometry", "geometry")
        assert len(result) == len(df)

    def test_error_empty_inputs_returns_empty_gdf(self):
        """Exception with empty inputs list returns empty GeoDataFrame."""
        with patch(_PATCH_TARGET, side_effect=RuntimeError("mock error")):
            result = op_sjoin([pd.DataFrame(), pd.DataFrame()], "geometry", "geometry")
        assert len(result) == 0


class TestOpGeoBufferException:
    """Covers lines 204-206."""

    def test_error_returns_original_df(self):
        df = pd.DataFrame({"geometry": ["invalid"], "x": [1]})
        with patch(_PATCH_TARGET, side_effect=ValueError("mock")):
            result = op_geo_buffer(df, "geometry", distance=100)
        assert result is df


class TestOpGeoCentroidException:
    """Covers lines 252-254."""

    def test_error_returns_original_df(self):
        df = pd.DataFrame({"geometry": ["bad"], "x": [1]})
        with patch(_PATCH_TARGET, side_effect=ValueError("mock")):
            result = op_geo_centroid(df, "geometry")
        assert result is df


class TestOpGeoAreaException:
    """Covers lines 294-296."""

    def test_error_returns_original_df(self):
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH_TARGET, side_effect=ValueError("mock")):
            result = op_geo_area(df, "geometry")
        assert result is df


class TestOpGeoClipException:
    """Covers lines 339-341."""

    def test_error_returns_first_input(self):
        df = pd.DataFrame({"geometry": ["x"]})
        with patch(_PATCH_TARGET, side_effect=RuntimeError("mock")):
            result = op_geo_clip([df, df.copy()], "geometry", "geometry")
        assert len(result) == 1

    def test_error_empty_inputs(self):
        with patch(_PATCH_TARGET, side_effect=RuntimeError("mock")):
            result = op_geo_clip([pd.DataFrame(), pd.DataFrame()], "g", "g")
        assert len(result) == 0


class TestOpGeoDissolveException:
    """Covers lines 398-400."""

    def test_error_returns_original_df(self):
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH_TARGET, side_effect=ValueError("mock")):
            result = op_geo_dissolve(df, "geometry")
        assert result is df


class TestOpGeoIntersectionException:
    """Covers lines 441-443."""

    def test_error_returns_first_input(self):
        df = pd.DataFrame({"geometry": ["x"]})
        with patch(_PATCH_TARGET, side_effect=RuntimeError("mock")):
            result = op_geo_intersection([df, df.copy()], "geometry", "geometry")
        assert len(result) == 1

    def test_error_empty_inputs(self):
        with patch(_PATCH_TARGET, side_effect=RuntimeError("mock")):
            result = op_geo_intersection([pd.DataFrame(), pd.DataFrame()], "g", "g")
        assert len(result) == 0


class TestOpGeoUnionException:
    """Covers lines 484-486."""

    def test_error_returns_first_input(self):
        df = pd.DataFrame({"geometry": ["x"]})
        with patch(_PATCH_TARGET, side_effect=RuntimeError("mock")):
            result = op_geo_union([df, df.copy()], "geometry", "geometry")
        assert len(result) == 1

    def test_error_empty_inputs(self):
        with patch(_PATCH_TARGET, side_effect=RuntimeError("mock")):
            result = op_geo_union([pd.DataFrame(), pd.DataFrame()], "g", "g")
        assert len(result) == 0


class TestOpGeoDifferenceException:
    """Covers lines 527-529."""

    def test_error_returns_first_input(self):
        df = pd.DataFrame({"geometry": ["x"]})
        with patch(_PATCH_TARGET, side_effect=RuntimeError("mock")):
            result = op_geo_difference([df, df.copy()], "geometry", "geometry")
        assert len(result) == 1

    def test_error_empty_inputs(self):
        with patch(_PATCH_TARGET, side_effect=RuntimeError("mock")):
            result = op_geo_difference([pd.DataFrame(), pd.DataFrame()], "g", "g")
        assert len(result) == 0


class TestOpGeoSimplifyException:
    """Covers lines 563-565."""

    def test_error_returns_original_df(self):
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH_TARGET, side_effect=ValueError("mock")):
            result = op_geo_simplify(df, "geometry")
        assert result is df


class TestOpGeoConvexHullException:
    """Covers lines 600-602."""

    def test_error_returns_original_df(self):
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH_TARGET, side_effect=ValueError("mock")):
            result = op_geo_convex_hull(df, "geometry")
        assert result is df


class TestOpGeoReprojectException:
    """Covers lines 649-651."""

    def test_error_returns_original_df(self):
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH_TARGET, side_effect=ValueError("mock")):
            result = op_geo_reproject(df, "geometry", target_crs="EPSG:32632")
        assert result is df


class TestOpGeoEnvelopeException:
    """Covers lines 685-687."""

    def test_error_returns_original_df(self):
        df = pd.DataFrame({"geometry": ["bad"]})
        with patch(_PATCH_TARGET, side_effect=ValueError("mock")):
            result = op_geo_envelope(df, "geometry")
        assert result is df


# =============================================================================
# Non-geographic CRS paths (no UTM reprojection)
# Lines 187 (buffer), 244 (centroid), 286 (area)
# =============================================================================

class TestMetricCrsPaths:
    """
    Pass GeoDataFrame directly (not WKT DataFrame) with a metric CRS so that
    to_geodataframe preserves the CRS and the else-branch is taken.
    """

    def test_buffer_metric_crs_no_reprojection(self):
        """EPSG:32632 (metric) skips UTM reprojection. Covers line 187."""
        gdf = _make_polygons_metric(3)
        assert not gdf.crs.is_geographic  # precondition
        result = op_geo_buffer(gdf, "geometry", distance=50, output_column="buf")
        assert "buf" in result.columns
        assert len(result) == 3
        assert result.crs.to_epsg() == 32632

    def test_centroid_metric_crs_no_reprojection(self):
        """EPSG:32632 skips UTM reprojection for centroid. Covers line 244."""
        gdf = _make_polygons_metric(3)
        assert not gdf.crs.is_geographic
        result = op_geo_centroid(gdf, "geometry", output_column="centro")
        assert "centro" in result.columns
        assert len(result) == 3
        for val in result["centro"]:
            assert val.geom_type == "Point"

    def test_area_metric_crs_no_reprojection(self):
        """EPSG:32632 skips UTM reprojection for area. Covers line 286."""
        gdf = _make_polygons_metric(3)
        assert not gdf.crs.is_geographic
        result = op_geo_area(gdf, "geometry", output_column="area")
        assert "area" in result.columns
        assert len(result) == 3
        assert (result["area"] >= 0).all()
        # 500m x 500m polygon = 250000 m^2
        assert result["area"].iloc[0] == pytest.approx(250000.0, rel=0.01)


# =============================================================================
# op_geo_dissolve — numeric aggfunc with mixed columns (line 385)
# =============================================================================

class TestOpGeoDissolveMixedAggfunc:
    """
    Covers line 385: string columns get aggfunc='first' when the requested
    aggfunc is numeric-only (mean, sum, std, var, median).
    """

    def _gdf_mixed(self):
        geoms = [
            Polygon([
                (500000 + i * 100, 5000000),
                (500000 + i * 100 + 50, 5000000),
                (500000 + i * 100 + 50, 5000050),
                (500000 + i * 100, 5000050),
            ])
            for i in range(4)
        ]
        return gpd.GeoDataFrame({
            "cat": ["X", "X", "Y", "Y"],
            "label": ["a", "b", "c", "d"],       # string column
            "amount": [10.0, 20.0, 30.0, 40.0],  # numeric column
        }, geometry=geoms, crs="EPSG:32632")

    def test_dissolve_mean_applies_first_to_strings(self):
        """aggfunc='mean' -> mean for numeric cols, 'first' for string cols."""
        gdf = self._gdf_mixed()
        result = op_geo_dissolve(gdf, "geometry", by_column="cat", aggfunc="mean")
        assert len(result) == 2
        assert "label" in result.columns
        x_row = result[result["cat"] == "X"]
        assert x_row["amount"].iloc[0] == pytest.approx(15.0)
        # String column gets 'first' value
        assert x_row["label"].iloc[0] == "a"

    def test_dissolve_sum_applies_first_to_strings(self):
        """aggfunc='sum' -> sum for numeric cols, 'first' for string cols."""
        gdf = self._gdf_mixed()
        result = op_geo_dissolve(gdf, "geometry", by_column="cat", aggfunc="sum")
        assert len(result) == 2
        y_row = result[result["cat"] == "Y"]
        assert y_row["amount"].iloc[0] == pytest.approx(70.0)
        assert y_row["label"].iloc[0] == "c"

    def test_dissolve_mean_no_by_column(self):
        """aggfunc='mean' without by_column dissolves everything into 1 row."""
        gdf = self._gdf_mixed()
        result = op_geo_dissolve(gdf, "geometry", aggfunc="mean")
        assert len(result) == 1
        assert result["amount"].iloc[0] == pytest.approx(25.0)


# =============================================================================
# op_geo_reproject — source_crs override and crs=None paths
# Lines 637-638 (source_crs override), 641 (crs is None)
# =============================================================================

class TestOpGeoReprojectEdgeCases:

    def test_source_crs_overrides_autodetect(self):
        """
        Lines 637-638: When source_crs differs from auto-detected CRS,
        the code forces set_crs(source_crs, allow_override=True).
        """
        gdf = _make_polygons_metric(2)
        df = _gdf_to_df_wkt(gdf)
        # to_geodataframe will default to EPSG:4326 for WKT,
        # but source_crs="EPSG:32632" should override that
        result = op_geo_reproject(
            df, "geometry",
            target_crs="EPSG:4326",
            source_crs="EPSG:32632",
        )
        assert result.crs.to_epsg() == 4326
        assert len(result) == 2

    def test_crs_none_gets_default_hint(self):
        """
        Line 641: If gdf.crs is None after to_geodataframe, set_crs(crs_hint)
        is called to assign a default before reprojecting.
        """
        geoms = [Point(9.0, 45.0), Point(9.1, 45.1)]
        gdf_no_crs = gpd.GeoDataFrame({"id": [1, 2]}, geometry=geoms)
        assert gdf_no_crs.crs is None  # precondition
        result = op_geo_reproject(gdf_no_crs, "geometry", target_crs="EPSG:32632")
        assert result.crs is not None
        assert result.crs.to_epsg() == 32632
        assert len(result) == 2

    def test_source_crs_matches_detected_no_override(self):
        """
        When source_crs matches the auto-detected CRS, no override needed,
        reproject still works correctly.
        """
        geoms = [Point(9.0, 45.0), Point(9.1, 45.1)]
        gdf = gpd.GeoDataFrame({"id": [1, 2]}, geometry=geoms, crs="EPSG:4326")
        result = op_geo_reproject(
            gdf, "geometry",
            target_crs="EPSG:32632",
            source_crs="EPSG:4326",
        )
        assert result.crs.to_epsg() == 32632
        assert len(result) == 2


# =============================================================================
# Edge cases for multi-input ops with empty inputs list
# =============================================================================

class TestMultiInputEmptyList:

    def test_sjoin_empty_inputs(self):
        result = op_sjoin([], "geometry", "geometry")
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 0

    def test_clip_empty_inputs(self):
        result = op_geo_clip([], "geometry", "geometry")
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 0

    def test_intersection_empty_inputs(self):
        result = op_geo_intersection([], "geometry", "geometry")
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 0

    def test_union_empty_inputs(self):
        result = op_geo_union([], "geometry", "geometry")
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 0

    def test_difference_empty_inputs(self):
        result = op_geo_difference([], "geometry", "geometry")
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 0
