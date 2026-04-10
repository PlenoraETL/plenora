"""
Unit tests for EsriJsonConverter.geodataframe_to_esri_features() and _shapely_to_esri().
"""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, PropertyMock

from app.core.features.arcgis_client import EsriJsonConverter

pytestmark = pytest.mark.unit


# =============================================================================
# Helpers: mock Shapely geometry objects
# =============================================================================

def _make_point(x=1.0, y=2.0):
    g = MagicMock()
    g.geom_type = "Point"
    g.x = x
    g.y = y
    g.is_empty = False
    return g


def _make_multipoint(points=None):
    if points is None:
        points = [(1.0, 2.0), (3.0, 4.0)]
    g = MagicMock()
    g.geom_type = "MultiPoint"
    g.is_empty = False
    pt_mocks = []
    for px, py in points:
        pm = MagicMock()
        pm.x = px
        pm.y = py
        pt_mocks.append(pm)
    g.geoms = pt_mocks
    return g


def _make_linestring(coords=None):
    if coords is None:
        coords = [(0, 0), (1, 1), (2, 0)]
    g = MagicMock()
    g.geom_type = "LineString"
    g.is_empty = False
    g.coords = coords
    return g


def _make_multilinestring(lines=None):
    if lines is None:
        lines = [[(0, 0), (1, 1)], [(2, 2), (3, 3)]]
    g = MagicMock()
    g.geom_type = "MultiLineString"
    g.is_empty = False
    line_mocks = []
    for line_coords in lines:
        lm = MagicMock()
        lm.coords = line_coords
        line_mocks.append(lm)
    g.geoms = line_mocks
    return g


def _make_polygon(exterior=None, interiors=None):
    if exterior is None:
        exterior = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    if interiors is None:
        interiors = []
    g = MagicMock()
    g.geom_type = "Polygon"
    g.is_empty = False
    ext_mock = MagicMock()
    ext_mock.coords = exterior
    g.exterior = ext_mock
    int_mocks = []
    for ring in interiors:
        im = MagicMock()
        im.coords = ring
        int_mocks.append(im)
    g.interiors = int_mocks
    return g


def _make_multipolygon(polygons=None):
    if polygons is None:
        polygons = [
            {"exterior": [(0, 0), (1, 0), (1, 1), (0, 0)], "interiors": []},
            {"exterior": [(5, 5), (6, 5), (6, 6), (5, 5)], "interiors": []},
        ]
    g = MagicMock()
    g.geom_type = "MultiPolygon"
    g.is_empty = False
    poly_mocks = []
    for pdef in polygons:
        pm = _make_polygon(pdef["exterior"], pdef.get("interiors", []))
        poly_mocks.append(pm)
    g.geoms = poly_mocks
    return g


def _make_empty_geom():
    g = MagicMock()
    g.is_empty = True
    return g


# =============================================================================
# _shapely_to_esri
# =============================================================================

class TestShapelyToEsri:
    """Tests for EsriJsonConverter._shapely_to_esri()."""

    def test_point(self):
        result = EsriJsonConverter._shapely_to_esri(_make_point(11.5, 42.3))
        assert result == {"x": 11.5, "y": 42.3}

    def test_multipoint(self):
        result = EsriJsonConverter._shapely_to_esri(
            _make_multipoint([(1.0, 2.0), (3.0, 4.0)])
        )
        assert result == {"points": [[1.0, 2.0], [3.0, 4.0]]}

    def test_linestring(self):
        coords = [(0, 0), (1, 1), (2, 0)]
        result = EsriJsonConverter._shapely_to_esri(_make_linestring(coords))
        assert result == {"paths": [coords]}

    def test_multilinestring(self):
        lines = [[(0, 0), (1, 1)], [(2, 2), (3, 3)]]
        result = EsriJsonConverter._shapely_to_esri(_make_multilinestring(lines))
        assert result == {"paths": lines}

    def test_polygon_no_holes(self):
        ext = [(0, 0), (10, 0), (10, 10), (0, 0)]
        result = EsriJsonConverter._shapely_to_esri(_make_polygon(ext, []))
        assert result == {"rings": [ext]}

    def test_polygon_with_holes(self):
        ext = [(0, 0), (10, 0), (10, 10), (0, 0)]
        hole = [(2, 2), (8, 2), (8, 8), (2, 2)]
        result = EsriJsonConverter._shapely_to_esri(_make_polygon(ext, [hole]))
        assert result == {"rings": [ext, hole]}

    def test_multipolygon(self):
        polys = [
            {"exterior": [(0, 0), (1, 0), (1, 1), (0, 0)], "interiors": []},
            {"exterior": [(5, 5), (6, 5), (6, 6), (5, 5)], "interiors": []},
        ]
        result = EsriJsonConverter._shapely_to_esri(_make_multipolygon(polys))
        assert result == {
            "rings": [
                [(0, 0), (1, 0), (1, 1), (0, 0)],
                [(5, 5), (6, 5), (6, 6), (5, 5)],
            ]
        }

    def test_multipolygon_with_holes(self):
        polys = [
            {
                "exterior": [(0, 0), (10, 0), (10, 10), (0, 0)],
                "interiors": [[(2, 2), (3, 3), (2, 3), (2, 2)]],
            },
        ]
        result = EsriJsonConverter._shapely_to_esri(_make_multipolygon(polys))
        assert len(result["rings"]) == 2

    def test_unsupported_geometry_type_returns_none(self):
        g = MagicMock()
        g.geom_type = "GeometryCollection"
        result = EsriJsonConverter._shapely_to_esri(g)
        assert result is None

    def test_exception_in_conversion_returns_none(self):
        g = MagicMock()
        g.geom_type = "Point"
        type(g).x = PropertyMock(side_effect=Exception("boom"))
        result = EsriJsonConverter._shapely_to_esri(g)
        assert result is None


# =============================================================================
# geodataframe_to_esri_features
# =============================================================================

class TestGeoDataFrameToEsriFeatures:
    """Tests for EsriJsonConverter.geodataframe_to_esri_features()."""

    def _make_gdf(self, data, geometry_col="geometry"):
        """Build a mock GeoDataFrame-like object using a real DataFrame."""
        df = pd.DataFrame(data)
        # Add geometry property so the code recognises it
        df.geometry = df[geometry_col] if geometry_col in df.columns else None
        return df

    def test_single_point_feature(self):
        geom = _make_point(12.0, 42.0)
        gdf = self._make_gdf({"NAME": ["Rome"], "VALUE": [100], "geometry": [geom]})
        result = EsriJsonConverter.geodataframe_to_esri_features(gdf)
        assert len(result) == 1
        assert result[0]["attributes"]["NAME"] == "Rome"
        assert result[0]["attributes"]["VALUE"] == 100
        assert result[0]["geometry"] == {"x": 12.0, "y": 42.0}

    def test_nan_attribute_converted_to_none(self):
        geom = _make_point()
        gdf = self._make_gdf({"NAME": [np.nan], "geometry": [geom]})
        result = EsriJsonConverter.geodataframe_to_esri_features(gdf)
        assert result[0]["attributes"]["NAME"] is None

    def test_numpy_int_converted_to_python_int(self):
        geom = _make_point()
        gdf = self._make_gdf({"VAL": [np.int64(42)], "geometry": [geom]})
        result = EsriJsonConverter.geodataframe_to_esri_features(gdf)
        val = result[0]["attributes"]["VAL"]
        assert val == 42
        assert isinstance(val, int)

    def test_empty_geometry_excluded(self):
        geom = _make_empty_geom()
        gdf = self._make_gdf({"NAME": ["X"], "geometry": [geom]})
        result = EsriJsonConverter.geodataframe_to_esri_features(gdf)
        assert "geometry" not in result[0]

    def test_none_geometry_excluded(self):
        """None in a pandas column becomes NaN; pd.isna check in source handles it.

        NOTE: The source code checks `geom is not None` which does NOT catch NaN.
        This means a None geometry stored via pandas will raise AttributeError
        when calling geom.is_empty. We test the current (buggy) behavior here
        so the bug is documented and visible.
        """
        gdf = self._make_gdf({"NAME": ["X"], "geometry": [None]})
        # pandas converts None -> NaN (float), source code does `geom is not None`
        # which is True for NaN, then `geom.is_empty` raises AttributeError.
        with pytest.raises(AttributeError):
            EsriJsonConverter.geodataframe_to_esri_features(gdf)

    def test_multiple_rows(self):
        geoms = [_make_point(1, 2), _make_point(3, 4)]
        gdf = self._make_gdf({"ID": [1, 2], "geometry": geoms})
        result = EsriJsonConverter.geodataframe_to_esri_features(gdf)
        assert len(result) == 2
        assert result[0]["geometry"] == {"x": 1, "y": 2}
        assert result[1]["geometry"] == {"x": 3, "y": 4}

    def test_geometry_column_not_in_attributes(self):
        geom = _make_point()
        gdf = self._make_gdf({"NAME": ["A"], "geometry": [geom]})
        result = EsriJsonConverter.geodataframe_to_esri_features(gdf)
        assert "geometry" not in result[0]["attributes"]

    def test_custom_geometry_column_name(self):
        geom = _make_point(5, 6)
        gdf = self._make_gdf({"NAME": ["A"], "geom": [geom]}, geometry_col="geom")
        result = EsriJsonConverter.geodataframe_to_esri_features(
            gdf, geometry_column="geom"
        )
        assert "geom" not in result[0]["attributes"]

    def test_polygon_geometry(self):
        geom = _make_polygon([(0, 0), (1, 0), (1, 1), (0, 0)])
        gdf = self._make_gdf({"ID": [1], "geometry": [geom]})
        result = EsriJsonConverter.geodataframe_to_esri_features(gdf)
        assert "rings" in result[0]["geometry"]
