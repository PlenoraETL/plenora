"""Unit tests for ResourceMetadata and ColumnMeta.

BE-GO-READY-ARCH-055-W4-SCHEMA-CRS-METADATA-STRATEGY.
"""

from __future__ import annotations

import pytest

from app.shared.resources.metadata import ColumnMeta, ResourceMetadata


pytestmark = [pytest.mark.unit]


# ColumnMeta


class TestColumnMetaIsGeometry:
    def test_bare_geometry(self):
        assert ColumnMeta(name="geom", type="geometry").is_geometry()

    def test_geometry_with_subtype(self):
        assert ColumnMeta(name="geom", type="geometry:polygon").is_geometry()

    def test_geometry_with_subtype_and_epsg(self):
        assert ColumnMeta(name="geom", type="geometry:polygon:4326").is_geometry()

    def test_non_geometry(self):
        assert not ColumnMeta(name="name", type="str").is_geometry()

    def test_int_column(self):
        assert not ColumnMeta(name="id", type="int").is_geometry()


class TestColumnMetaExtractEpsg:
    def test_full_type_string(self):
        assert ColumnMeta(name="g", type="geometry:polygon:4326").extract_epsg() == 4326

    def test_missing_epsg(self):
        assert ColumnMeta(name="g", type="geometry:polygon").extract_epsg() is None

    def test_bare_geometry(self):
        assert ColumnMeta(name="g", type="geometry").extract_epsg() is None

    def test_non_integer_epsg(self):
        assert ColumnMeta(name="g", type="geometry:polygon:wgs84").extract_epsg() is None

    def test_other_epsg(self):
        assert ColumnMeta(name="g", type="geometry:point:32632").extract_epsg() == 32632


class TestColumnMetaGeometrySubtype:
    def test_polygon(self):
        assert ColumnMeta(name="g", type="geometry:polygon:4326").geometry_subtype() == "polygon"

    def test_point(self):
        assert ColumnMeta(name="g", type="geometry:point").geometry_subtype() == "point"

    def test_bare_geometry_returns_none(self):
        assert ColumnMeta(name="g", type="geometry").geometry_subtype() is None

    def test_non_geometry_returns_none(self):
        assert ColumnMeta(name="x", type="str").geometry_subtype() is None


# ResourceMetadata — from_columns_list


_MIXED_COLUMNS = [
    {"name": "id", "type": "int"},
    {"name": "label", "type": "str"},
    {"name": "geom", "type": "geometry:polygon:4326"},
]


class TestResourceMetadataFromColumnsList:
    def test_basic_round_trip(self):
        rm = ResourceMetadata.from_columns_list(_MIXED_COLUMNS, source_kind="file")
        assert rm.column_names() == ("id", "label", "geom")

    def test_geometry_column_extracted(self):
        rm = ResourceMetadata.from_columns_list(_MIXED_COLUMNS, source_kind="file")
        assert rm.geometry_column == "geom"

    def test_crs_epsg_extracted(self):
        rm = ResourceMetadata.from_columns_list(_MIXED_COLUMNS, source_kind="file")
        assert rm.crs_epsg == 4326

    def test_no_geometry(self):
        cols = [{"name": "a", "type": "str"}, {"name": "b", "type": "int"}]
        rm = ResourceMetadata.from_columns_list(cols, source_kind="db")
        assert rm.geometry_column is None
        assert rm.crs_epsg is None
        assert not rm.has_geometry()

    def test_skips_missing_name(self):
        cols = [{"type": "str"}, {"name": "x", "type": "int"}]
        rm = ResourceMetadata.from_columns_list(cols, source_kind="file")
        assert rm.column_names() == ("x",)

    def test_skips_non_dict_entries(self):
        rm = ResourceMetadata.from_columns_list(["bad", None, {"name": "x", "type": "str"}], source_kind="file")
        assert rm.column_names() == ("x",)

    def test_optional_fields_propagated(self):
        rm = ResourceMetadata.from_columns_list(
            _MIXED_COLUMNS, source_kind="project",
            row_count=100, group_id="g1", project_id="p1",
        )
        assert rm.row_count == 100
        assert rm.group_id == "g1"
        assert rm.project_id == "p1"

    def test_first_geometry_column_wins(self):
        cols = [
            {"name": "geom1", "type": "geometry:polygon:4326"},
            {"name": "geom2", "type": "geometry:point:32632"},
        ]
        rm = ResourceMetadata.from_columns_list(cols, source_kind="file")
        assert rm.geometry_column == "geom1"
        assert rm.crs_epsg == 4326


# ResourceMetadata — to_columns_list round-trip


class TestResourceMetadataToColumnsList:
    def test_round_trip(self):
        rm = ResourceMetadata.from_columns_list(_MIXED_COLUMNS, source_kind="file")
        assert rm.to_columns_list() == _MIXED_COLUMNS

    def test_empty(self):
        rm = ResourceMetadata.from_columns_list([], source_kind="file")
        assert rm.to_columns_list() == []


# ResourceMetadata — accessors


class TestResourceMetadataAccessors:
    def test_column_type_map(self):
        rm = ResourceMetadata.from_columns_list(_MIXED_COLUMNS, source_kind="file")
        assert rm.column_type_map() == {
            "id": "int",
            "label": "str",
            "geom": "geometry:polygon:4326",
        }

    def test_has_geometry_true(self):
        rm = ResourceMetadata.from_columns_list(_MIXED_COLUMNS, source_kind="file")
        assert rm.has_geometry()

    def test_has_geometry_false(self):
        rm = ResourceMetadata.from_columns_list(
            [{"name": "x", "type": "str"}], source_kind="file"
        )
        assert not rm.has_geometry()


# Immutability


class TestImmutability:
    def test_column_meta_frozen(self):
        col = ColumnMeta(name="x", type="str")
        with pytest.raises(Exception):  # FrozenInstanceError
            col.name = "y"  # type: ignore[misc]

    def test_resource_metadata_frozen(self):
        rm = ResourceMetadata.from_columns_list(_MIXED_COLUMNS, source_kind="file")
        with pytest.raises(Exception):
            rm.crs_epsg = 9999  # type: ignore[misc]
