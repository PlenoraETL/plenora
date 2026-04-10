"""
Unit tests for EsriJsonConverter.features_to_rows() and _geometry_to_wkt().
"""

import pytest

from app.core.features.arcgis_client import EsriJsonConverter

pytestmark = pytest.mark.unit


# =============================================================================
# Helper: standard field definitions
# =============================================================================

FIELDS_SIMPLE = [
    {"name": "OBJECTID", "type": "esriFieldTypeOID"},
    {"name": "NAME", "type": "esriFieldTypeString"},
    {"name": "VALUE", "type": "esriFieldTypeDouble"},
]

FIELDS_WITH_GEOM = [
    {"name": "OBJECTID", "type": "esriFieldTypeOID"},
    {"name": "NAME", "type": "esriFieldTypeString"},
    {"name": "SHAPE", "type": "esriFieldTypeGeometry"},
]


# =============================================================================
# features_to_rows — basic behavior
# =============================================================================

class TestFeaturesToRowsBasic:
    """Test basic features_to_rows() behavior."""

    def test_empty_features_returns_empty(self):
        rows, cols = EsriJsonConverter.features_to_rows([], FIELDS_SIMPLE)
        assert rows == []
        assert cols == []

    def test_attributes_only_no_geometry(self):
        features = [
            {"attributes": {"OBJECTID": 1, "NAME": "Rome", "VALUE": 2.5}},
        ]
        rows, cols = EsriJsonConverter.features_to_rows(
            features, FIELDS_SIMPLE, include_geometry=False
        )
        assert cols == ["OBJECTID", "NAME", "VALUE"]
        assert rows == [(1, "Rome", 2.5)]

    def test_attributes_with_geometry_column_appended(self):
        features = [
            {
                "attributes": {"OBJECTID": 1, "NAME": "Rome", "VALUE": 2.5},
                "geometry": {"x": 12.5, "y": 41.9},
            },
        ]
        rows, cols = EsriJsonConverter.features_to_rows(
            features, FIELDS_SIMPLE, include_geometry=True
        )
        assert "geometry" in cols
        assert cols[-1] == "geometry"
        assert rows[0][-1] == "POINT(12.5 41.9)"

    def test_geometry_field_excluded_from_attr_columns(self):
        """Fields with type esriFieldTypeGeometry should not appear as attribute columns."""
        features = [
            {
                "attributes": {"OBJECTID": 1, "NAME": "Rome"},
                "geometry": {"x": 12.5, "y": 41.9},
            },
        ]
        rows, cols = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        assert "SHAPE" not in cols
        assert "geometry" in cols

    def test_missing_attribute_yields_none(self):
        features = [
            {"attributes": {"OBJECTID": 1}},
        ]
        rows, cols = EsriJsonConverter.features_to_rows(
            features, FIELDS_SIMPLE, include_geometry=False
        )
        assert rows[0] == (1, None, None)

    def test_multiple_features(self):
        features = [
            {"attributes": {"OBJECTID": 1, "NAME": "A", "VALUE": 1.0}},
            {"attributes": {"OBJECTID": 2, "NAME": "B", "VALUE": 2.0}},
            {"attributes": {"OBJECTID": 3, "NAME": "C", "VALUE": 3.0}},
        ]
        rows, cols = EsriJsonConverter.features_to_rows(
            features, FIELDS_SIMPLE, include_geometry=False
        )
        assert len(rows) == 3
        assert rows[2] == (3, "C", 3.0)

    def test_feature_with_none_geometry(self):
        """Feature with geometry key set to None should produce None WKT."""
        features = [
            {"attributes": {"OBJECTID": 1, "NAME": "X", "VALUE": 0}, "geometry": None},
        ]
        rows, cols = EsriJsonConverter.features_to_rows(
            features, FIELDS_SIMPLE, include_geometry=True
        )
        assert rows[0][-1] is None

    def test_feature_without_geometry_key(self):
        """Feature missing the 'geometry' key entirely."""
        features = [
            {"attributes": {"OBJECTID": 1, "NAME": "X", "VALUE": 0}},
        ]
        rows, cols = EsriJsonConverter.features_to_rows(
            features, FIELDS_SIMPLE, include_geometry=True
        )
        assert rows[0][-1] is None

    def test_feature_without_attributes_key(self):
        """Feature missing the 'attributes' key — all attr values should be None."""
        features = [
            {"geometry": {"x": 1.0, "y": 2.0}},
        ]
        rows, cols = EsriJsonConverter.features_to_rows(
            features, FIELDS_SIMPLE, include_geometry=True
        )
        assert rows[0][:-1] == (None, None, None)
        assert rows[0][-1] == "POINT(1.0 2.0)"


# =============================================================================
# features_to_rows — Point geometry
# =============================================================================

class TestFeaturesToRowsPoint:
    """Test Point geometry conversion in features_to_rows."""

    def test_point_wkt(self):
        features = [
            {"attributes": {"OBJECTID": 1, "NAME": "P"}, "geometry": {"x": 11.0, "y": 44.0}},
        ]
        rows, _ = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        assert rows[0][-1] == "POINT(11.0 44.0)"

    def test_point_with_null_x_returns_none(self):
        features = [
            {"attributes": {"OBJECTID": 1, "NAME": "P"}, "geometry": {"x": None, "y": 44.0}},
        ]
        rows, _ = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        assert rows[0][-1] is None

    def test_point_with_null_y_returns_none(self):
        features = [
            {"attributes": {"OBJECTID": 1, "NAME": "P"}, "geometry": {"x": 11.0, "y": None}},
        ]
        rows, _ = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        assert rows[0][-1] is None

    def test_point_negative_coords(self):
        features = [
            {"attributes": {"OBJECTID": 1, "NAME": "P"}, "geometry": {"x": -73.9, "y": 40.7}},
        ]
        rows, _ = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        assert rows[0][-1] == "POINT(-73.9 40.7)"


# =============================================================================
# features_to_rows — MultiPoint geometry
# =============================================================================

class TestFeaturesToRowsMultiPoint:
    """Test MultiPoint geometry conversion."""

    def test_multipoint_wkt(self):
        features = [
            {
                "attributes": {"OBJECTID": 1, "NAME": "MP"},
                "geometry": {"points": [[1.0, 2.0], [3.0, 4.0]]},
            },
        ]
        rows, _ = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        assert rows[0][-1] == "MULTIPOINT(1.0 2.0, 3.0 4.0)"

    def test_multipoint_empty_points_returns_none(self):
        features = [
            {"attributes": {"OBJECTID": 1, "NAME": "MP"}, "geometry": {"points": []}},
        ]
        rows, _ = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        assert rows[0][-1] is None

    def test_multipoint_single_point(self):
        features = [
            {"attributes": {"OBJECTID": 1, "NAME": "MP"}, "geometry": {"points": [[5.5, 6.6]]}},
        ]
        rows, _ = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        assert rows[0][-1] == "MULTIPOINT(5.5 6.6)"


# =============================================================================
# features_to_rows — Polyline geometry
# =============================================================================

class TestFeaturesToRowsPolyline:
    """Test Polyline geometry conversion."""

    def test_single_path_linestring(self):
        features = [
            {
                "attributes": {"OBJECTID": 1, "NAME": "L"},
                "geometry": {"paths": [[[0, 0], [1, 1], [2, 0]]]},
            },
        ]
        rows, _ = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        assert rows[0][-1] == "LINESTRING(0 0, 1 1, 2 0)"

    def test_multi_path_multilinestring(self):
        features = [
            {
                "attributes": {"OBJECTID": 1, "NAME": "ML"},
                "geometry": {
                    "paths": [
                        [[0, 0], [1, 1]],
                        [[2, 2], [3, 3]],
                    ]
                },
            },
        ]
        rows, _ = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        assert rows[0][-1] == "MULTILINESTRING((0 0, 1 1), (2 2, 3 3))"

    def test_empty_paths_returns_none(self):
        features = [
            {"attributes": {"OBJECTID": 1, "NAME": "L"}, "geometry": {"paths": []}},
        ]
        rows, _ = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        assert rows[0][-1] is None


# =============================================================================
# features_to_rows — Polygon geometry
# =============================================================================

class TestFeaturesToRowsPolygon:
    """Test Polygon geometry conversion."""

    def test_simple_polygon(self):
        features = [
            {
                "attributes": {"OBJECTID": 1, "NAME": "PG"},
                "geometry": {
                    "rings": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]
                },
            },
        ]
        rows, _ = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        assert rows[0][-1] == "POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))"

    def test_polygon_with_hole(self):
        features = [
            {
                "attributes": {"OBJECTID": 1, "NAME": "PGH"},
                "geometry": {
                    "rings": [
                        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                        [[2, 2], [8, 2], [8, 8], [2, 8], [2, 2]],
                    ]
                },
            },
        ]
        rows, _ = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        wkt = rows[0][-1]
        assert wkt.startswith("POLYGON(")
        assert "(0 0, 10 0, 10 10, 0 10, 0 0)" in wkt
        assert "(2 2, 8 2, 8 8, 2 8, 2 2)" in wkt

    def test_empty_rings_returns_none(self):
        features = [
            {"attributes": {"OBJECTID": 1, "NAME": "PG"}, "geometry": {"rings": []}},
        ]
        rows, _ = EsriJsonConverter.features_to_rows(
            features, FIELDS_WITH_GEOM, include_geometry=True
        )
        assert rows[0][-1] is None


# =============================================================================
# _geometry_to_wkt — direct tests
# =============================================================================

class TestGeometryToWkt:
    """Direct tests for the static _geometry_to_wkt method."""

    def test_none_geometry_returns_none(self):
        assert EsriJsonConverter._geometry_to_wkt(None) is None

    def test_empty_dict_returns_none(self):
        assert EsriJsonConverter._geometry_to_wkt({}) is None

    def test_unrecognized_geometry_returns_none(self):
        assert EsriJsonConverter._geometry_to_wkt({"foo": "bar"}) is None

    def test_malformed_point_missing_y_key(self):
        """If only 'x' is present without 'y', should still try and possibly error."""
        # The code checks 'x' in geom and 'y' in geom, so missing 'y' falls through
        result = EsriJsonConverter._geometry_to_wkt({"x": 1.0})
        assert result is None

    def test_integer_coords_work(self):
        result = EsriJsonConverter._geometry_to_wkt({"x": 10, "y": 20})
        assert result == "POINT(10 20)"
