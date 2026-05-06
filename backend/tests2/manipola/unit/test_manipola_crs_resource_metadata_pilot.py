"""Focused contract test - _applica_crs_da_colonne uses ResourceMetadata.

BE-GO-READY-ARCH-056-W6-RESOURCE-METADATA-CRS-PILOT.

Verifies that the CRS-from-node-columns lookup now routes through
`ResourceMetadata.from_columns_list()` rather than inline string parsing.
Tests cover the six behavioural cases that matter for correctness.
"""

from __future__ import annotations

import pandas as pd
import geopandas as gpd
import pytest
from shapely.geometry import Point


pytestmark = [pytest.mark.unit, pytest.mark.manipola]


# ---------------------------------------------------------------------------
# Helper - import the function under test
# ---------------------------------------------------------------------------

def _func():
    from app.core.manipola.executor import _applica_crs_da_colonne
    return _applica_crs_da_colonne


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def plain_df():
    """Plain pandas DataFrame with a WKT geometry column."""
    return pd.DataFrame({
        "id": [1, 2],
        "geometry": ["POINT (11.34 44.49)", "POINT (12.49 41.90)"],
    })


@pytest.fixture
def geo_df():
    """GeoDataFrame already carrying EPSG:4326."""
    return gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[Point(11.34, 44.49), Point(12.49, 41.90)],
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# 1. Normal case: geometry + EPSG in type string -> GeoDataFrame with CRS
# ---------------------------------------------------------------------------

def test_applies_crs_when_geometry_column_and_epsg_present(plain_df):
    """The primary migration target: ResourceMetadata reads geometry_column
    and crs_epsg from the type string; the DataFrame is converted to a
    GeoDataFrame with the correct CRS."""
    fn = _func()
    node_columns = [
        {"name": "id", "type": "int"},
        {"name": "geometry", "type": "geometry:polygon:4326"},
    ]
    result = fn(plain_df, node_columns)
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.crs is not None
    assert result.crs.to_epsg() == 4326


# ---------------------------------------------------------------------------
# 2. GeoDataFrame passthrough - not re-processed
# ---------------------------------------------------------------------------

def test_geodataframe_returned_unchanged(geo_df):
    """If the input is already a GeoDataFrame the function must return it
    immediately without re-wrapping it."""
    fn = _func()
    node_columns = [{"name": "geometry", "type": "geometry:point:4326"}]
    result = fn(geo_df, node_columns)
    assert result is geo_df


# ---------------------------------------------------------------------------
# 3. No geometry column in node_columns -> plain DataFrame returned
# ---------------------------------------------------------------------------

def test_plain_df_returned_when_no_geometry_in_columns(plain_df):
    """node_columns contains only non-geometry types; the DataFrame must
    be returned as-is."""
    fn = _func()
    node_columns = [{"name": "id", "type": "int"}]
    result = fn(plain_df, node_columns)
    assert type(result) is pd.DataFrame


# ---------------------------------------------------------------------------
# 4. Geometry column not in df.columns -> plain DataFrame returned
# ---------------------------------------------------------------------------

def test_plain_df_returned_when_geometry_column_absent_from_dataframe(plain_df):
    """node_columns declares a geometry column but that column does not exist
    in the DataFrame (e.g. it was filtered out). The function must skip CRS
    application rather than raising."""
    fn = _func()
    node_columns = [{"name": "nonexistent_geom", "type": "geometry:polygon:4326"}]
    result = fn(plain_df, node_columns)
    assert type(result) is pd.DataFrame


# ---------------------------------------------------------------------------
# 5. Alias column name - alias takes priority over name
# ---------------------------------------------------------------------------

def test_alias_used_as_effective_column_name(plain_df):
    """When a canvas column carries an alias the alias is the actual column
    name in the DataFrame after filter_visible_columns. The ResourceMetadata
    normalisation (alias -> name) must resolve the correct column."""
    fn = _func()
    # The DataFrame column is "geometry"; the canvas entry has alias="geometry"
    node_columns = [
        {"name": "geom_original", "alias": "geometry", "type": "geometry:point:4326"},
    ]
    result = fn(plain_df, node_columns)
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.crs is not None
    assert result.crs.to_epsg() == 4326


# ---------------------------------------------------------------------------
# 6. Geometry type without EPSG -> plain DataFrame returned
# ---------------------------------------------------------------------------

def test_plain_df_returned_when_geometry_has_no_epsg(plain_df):
    """A geometry column with type 'geometry:polygon' (no EPSG component)
    cannot have CRS applied. The function must return the plain DataFrame."""
    fn = _func()
    node_columns = [{"name": "geometry", "type": "geometry:polygon"}]
    result = fn(plain_df, node_columns)
    assert type(result) is pd.DataFrame


# ---------------------------------------------------------------------------
# 7. Empty node_columns -> plain DataFrame returned
# ---------------------------------------------------------------------------

def test_empty_node_columns_returns_plain_df(plain_df):
    fn = _func()
    assert type(fn(plain_df, [])) is pd.DataFrame
    assert type(fn(plain_df, None)) is pd.DataFrame
