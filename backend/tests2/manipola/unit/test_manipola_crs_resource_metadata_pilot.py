"""Focused contract test - _applica_crs_da_colonne uses ResourceMetadata.

BE-GO-READY-ARCH-056-W6-RESOURCE-METADATA-CRS-PILOT.

Verifies that the CRS-from-node-columns lookup now routes through
`ResourceMetadata.from_columns_list()` rather than inline string parsing.
Tests cover the six behavioural cases that matter for correctness.
"""

from __future__ import annotations

import itertools
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


# ---------------------------------------------------------------------------
# _estrai_srid_da_canvas — BE-GO-READY-ARCH-057-W5
# Verifies the migration from extract_srid_from_column_type loop to
# ResourceMetadata.from_columns_list.
# ---------------------------------------------------------------------------


def _executor():
    from app.core.manipola.executor import ManipolaExecutor
    return ManipolaExecutor.__new__(ManipolaExecutor)


_node_seq = itertools.count(1)


class _Node:
    """Minimal canvas node stub compatible with CanvasGraph (has model_dump)."""

    def __init__(self, columns=None, data=None):
        self._id = f"test-node-{next(_node_seq)}"
        self.columns = columns
        self.data = data or {}

    def model_dump(self):
        return {"id": self._id, "columns": self.columns, "data": self.data}


class _Canvas:
    def __init__(self, nodes):
        self.nodes = nodes


def test_estrai_srid_returns_epsg_from_dict_columns():
    """Primary path: geometry column in a node's dict column list."""
    ex = _executor()
    canvas = _Canvas([
        _Node(columns=[
            {"name": "id", "type": "int"},
            {"name": "geometry", "type": "geometry:polygon:4326"},
        ])
    ])
    assert ex._estrai_srid_da_canvas(canvas) == 4326


def test_estrai_srid_returns_none_when_no_geometry():
    """No geometry column in any node -> None."""
    ex = _executor()
    canvas = _Canvas([
        _Node(columns=[{"name": "id", "type": "int"}, {"name": "name", "type": "str"}])
    ])
    assert ex._estrai_srid_da_canvas(canvas) is None


def test_estrai_srid_returns_none_when_no_epsg():
    """Geometry column without EPSG component -> None."""
    ex = _executor()
    canvas = _Canvas([
        _Node(columns=[{"name": "geom", "type": "geometry:polygon"}])
    ])
    assert ex._estrai_srid_da_canvas(canvas) is None


def test_estrai_srid_returns_first_srid_across_nodes():
    """Returns SRID from the first node that has one, ignoring later nodes."""
    ex = _executor()
    canvas = _Canvas([
        _Node(columns=[{"name": "id", "type": "int"}]),
        _Node(columns=[{"name": "geom", "type": "geometry:point:3857"}]),
        _Node(columns=[{"name": "g2", "type": "geometry:polygon:4326"}]),
    ])
    assert ex._estrai_srid_da_canvas(canvas) == 3857


def test_estrai_srid_handles_empty_canvas():
    """No nodes -> None."""
    ex = _executor()
    assert ex._estrai_srid_da_canvas(_Canvas([])) is None


def test_estrai_srid_from_node_data_columns():
    """Columns supplied via node.data dict (CanvasGraph fallback to data key)."""
    ex = _executor()

    class _DataNode:
        columns = None
        data = {"columns": [{"name": "geom", "type": "geometry:point:32632"}]}

        def model_dump(self):
            return {"id": "data-node-1", "columns": self.columns, "data": self.data}

    canvas = _Canvas([_DataNode()])
    assert ex._estrai_srid_da_canvas(canvas) == 32632
