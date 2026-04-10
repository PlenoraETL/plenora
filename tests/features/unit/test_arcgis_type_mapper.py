"""
Unit tests for EsriTypeMapper and _mask_secret helper.
"""

import pytest

from app.core.features.arcgis_client import EsriTypeMapper, _mask_secret

pytestmark = pytest.mark.unit


# =============================================================================
# _mask_secret
# =============================================================================

class TestMaskSecret:
    """Tests for the _mask_secret() helper function."""

    def test_empty_string_returns_vuoto(self):
        assert _mask_secret("") == "(vuoto)"

    def test_none_returns_vuoto(self):
        assert _mask_secret(None) == "(vuoto)"

    def test_short_string_returns_stars(self):
        """Strings <= visible chars should return '***'."""
        assert _mask_secret("abc", visible=4) == "***"
        assert _mask_secret("abcd", visible=4) == "***"

    def test_long_string_shows_prefix(self):
        assert _mask_secret("mySecretToken", visible=4) == "mySe***"

    def test_custom_visible_length(self):
        assert _mask_secret("abcdefghij", visible=6) == "abcdef***"

    def test_default_visible_is_four(self):
        result = _mask_secret("HelloWorld")
        assert result == "Hell***"

    def test_exactly_one_over_visible(self):
        assert _mask_secret("abcde", visible=4) == "abcd***"


# =============================================================================
# EsriTypeMapper.map_field_type
# =============================================================================

class TestMapFieldType:
    """Tests for EsriTypeMapper.map_field_type()."""

    def test_oid_maps_to_int(self):
        assert EsriTypeMapper.map_field_type("esriFieldTypeOID") == "int"

    def test_integer_maps_to_int(self):
        assert EsriTypeMapper.map_field_type("esriFieldTypeInteger") == "int"

    def test_small_integer_maps_to_int(self):
        assert EsriTypeMapper.map_field_type("esriFieldTypeSmallInteger") == "int"

    def test_double_maps_to_real(self):
        assert EsriTypeMapper.map_field_type("esriFieldTypeDouble") == "real"

    def test_single_maps_to_real(self):
        assert EsriTypeMapper.map_field_type("esriFieldTypeSingle") == "real"

    def test_string_maps_to_str(self):
        assert EsriTypeMapper.map_field_type("esriFieldTypeString") == "str"

    def test_date_maps_to_date(self):
        assert EsriTypeMapper.map_field_type("esriFieldTypeDate") == "date"

    def test_guid_maps_to_str(self):
        assert EsriTypeMapper.map_field_type("esriFieldTypeGUID") == "str"

    def test_global_id_maps_to_str(self):
        assert EsriTypeMapper.map_field_type("esriFieldTypeGlobalID") == "str"

    def test_geometry_maps_to_geometry(self):
        assert EsriTypeMapper.map_field_type("esriFieldTypeGeometry") == "geometry"

    def test_blob_maps_to_str(self):
        assert EsriTypeMapper.map_field_type("esriFieldTypeBlob") == "str"

    def test_xml_maps_to_str(self):
        assert EsriTypeMapper.map_field_type("esriFieldTypeXML") == "str"

    def test_unknown_type_defaults_to_str(self):
        assert EsriTypeMapper.map_field_type("esriFieldTypeFooBar") == "str"

    def test_empty_string_defaults_to_str(self):
        assert EsriTypeMapper.map_field_type("") == "str"

    def test_all_known_types_covered(self):
        """Ensure every key in ESRI_TO_URBY returns the expected value."""
        for esri_type, expected in EsriTypeMapper.ESRI_TO_URBY.items():
            assert EsriTypeMapper.map_field_type(esri_type) == expected


# =============================================================================
# EsriTypeMapper.map_geometry_type
# =============================================================================

class TestMapGeometryType:
    """Tests for EsriTypeMapper.map_geometry_type()."""

    def test_point(self):
        assert EsriTypeMapper.map_geometry_type("esriGeometryPoint") == "geometry:point"

    def test_multipoint(self):
        assert EsriTypeMapper.map_geometry_type("esriGeometryMultipoint") == "geometry:point"

    def test_polyline(self):
        assert EsriTypeMapper.map_geometry_type("esriGeometryPolyline") == "geometry:line"

    def test_polygon(self):
        assert EsriTypeMapper.map_geometry_type("esriGeometryPolygon") == "geometry:polygon"

    def test_envelope(self):
        assert EsriTypeMapper.map_geometry_type("esriGeometryEnvelope") == "geometry:polygon"

    def test_unknown_geometry_defaults_to_geometry(self):
        assert EsriTypeMapper.map_geometry_type("esriGeometryUnknown") == "geometry"

    def test_empty_string_defaults_to_geometry(self):
        assert EsriTypeMapper.map_geometry_type("") == "geometry"

    def test_all_known_geom_types_covered(self):
        """Ensure every key in ESRI_GEOM_TO_URBY returns the expected value."""
        for esri_geom, expected in EsriTypeMapper.ESRI_GEOM_TO_URBY.items():
            assert EsriTypeMapper.map_geometry_type(esri_geom) == expected
