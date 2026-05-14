"""Contracts for the horizontal geodataframe helper boundary."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared]


def test_features_geo_utils_reexports_geodata_helpers() -> None:
    from app.core.dataframes import geodata
    from app.core.features import geo_utils

    assert geo_utils.detect_geometry_column is geodata.detect_geometry_column
    assert geo_utils.empty_geodataframe is geodata.empty_geodataframe
    assert geo_utils.ensure_same_crs is geodata.ensure_same_crs
    assert geo_utils.extract_srid_from_column_type is geodata.extract_srid_from_column_type
    assert geo_utils.extract_srid_from_ewkb is geodata.extract_srid_from_ewkb
    assert geo_utils.geodataframe_clip is geodata.geodataframe_clip
    assert geo_utils.geodataframe_from_geometry is geodata.geodataframe_from_geometry
    assert geo_utils.geodataframe_from_geometry_series is geodata.geodataframe_from_geometry_series
    assert geo_utils.geodataframe_from_rows is geodata.geodataframe_from_rows
    assert geo_utils.geodataframe_overlay is geodata.geodataframe_overlay
    assert geo_utils.geodataframe_sjoin is geodata.geodataframe_sjoin
    assert geo_utils.geodataframe_sjoin_nearest is geodata.geodataframe_sjoin_nearest
    assert geo_utils.geoseries_from_geometry_values is geodata.geoseries_from_geometry_values
    assert geo_utils.is_geodataframe is geodata.is_geodataframe
    assert geo_utils.parse_geometry is geodata.parse_geometry
    assert geo_utils.to_geodataframe is geodata.to_geodataframe
