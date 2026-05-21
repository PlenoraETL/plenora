"""
Focused test: ResourceMetadata travels with load_file_sources.

BE-GO-READY-ARCH-057-W4-LOADEDRESOURCE-METADATA-SOURCE-PILOT.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.manipola]


# Helpers

class _Node:
    type = "file_source"
    id = "node-1"
    data = {"file_source_id": "fs-1"}

    def __init__(self, columns=None):
        self.columns = columns or []


class _Canvas:
    def __init__(self, *nodes):
        self.nodes = list(nodes)


class _StubLoader:
    def __init__(self, df):
        self._df = df

    def load(self, ref, *, expected_group_id, is_superadmin=False):
        return self._df

    def load_with_metadata(self, ref, *, expected_group_id, resource_columns=None, is_superadmin=False):
        from app.shared.resources.metadata import LoadedResource, ResourceMetadata
        metadata = None
        if resource_columns is not None:
            metadata = ResourceMetadata.from_columns_list(resource_columns, source_kind="file")
        return LoadedResource(data=self._df, metadata=metadata)


# 1. metadata_map populated for a file node with a geometry column


def test_metadata_map_receives_resource_metadata():
    """After a successful load, metadata_map[node.id] is a ResourceMetadata
    with the correct geometry_column and crs_epsg extracted from node
    columns."""
    from app.core.manipola import source_loading as sl
    from app.shared.resources.metadata import ResourceMetadata

    df = pd.DataFrame({"id": [1], "geometry": ["POINT (0 0)"]})
    node = _Node(columns=[
        {"name": "id", "type": "int"},
        {"name": "geometry", "type": "geometry:polygon:4326"},
    ])
    canvas = _Canvas(node)
    dataframes: dict = {}
    metadata_map: dict = {}

    with patch("app.core.artifacts.SourceLoader", lambda: _StubLoader(df)):
        result = sl.load_file_sources(
            canvas, dataframes,
            group_id="grp-1",
            metadata_map=metadata_map,
        )

    assert result is None  # success
    assert "node-1" in metadata_map
    meta = metadata_map["node-1"]
    assert isinstance(meta, ResourceMetadata)
    assert meta.geometry_column == "geometry"
    assert meta.crs_epsg == 4326
    assert meta.source_kind == "file"


# 2. metadata_map unchanged when load fails


def test_metadata_map_not_populated_on_load_error():
    """If the node load raises SourceLoadingError, metadata_map stays empty
    and the function returns the fail-closed dict."""
    from app.core.manipola import source_loading as sl
    from app.core.artifacts import SourceLoadingError

    class _FailLoader:
        def load_with_metadata(self, ref, *, expected_group_id, resource_columns=None, is_superadmin=False):
            raise SourceLoadingError("Accesso negato: il file sorgente 'fs-1'")

    node = _Node(columns=[{"name": "geometry", "type": "geometry:polygon:4326"}])
    canvas = _Canvas(node)
    dataframes: dict = {}
    metadata_map: dict = {}

    with patch("app.core.artifacts.SourceLoader", lambda: _FailLoader()):
        result = sl.load_file_sources(
            canvas, dataframes,
            group_id="grp-1",
            metadata_map=metadata_map,
        )

    assert result is not None
    assert result.get("success") is False
    assert metadata_map == {}


# 3. metadata_map=None (default) - no error, existing behaviour unchanged


def test_default_metadata_map_none_does_not_raise():
    """When metadata_map is not passed (default None), load_file_sources
    works exactly as before - no error and dataframes is populated."""
    from app.core.manipola import source_loading as sl

    df = pd.DataFrame({"a": [1]})
    node = _Node(columns=[{"name": "a", "type": "str"}])
    canvas = _Canvas(node)
    dataframes: dict = {}

    with patch("app.core.artifacts.SourceLoader", lambda: _StubLoader(df)):
        result = sl.load_file_sources(
            canvas, dataframes, group_id="grp-1",
        )

    assert result is None
    assert "node-1" in dataframes


# 4. pre-built meta passed to _applica_crs_da_colonne - no double construction


def test_applica_crs_accepts_pre_built_meta():
    """_applica_crs_da_colonne with a pre-built ResourceMetadata avoids the
    internal construction from node_columns - the meta parameter takes precedence."""
    from app.core.manipola.executor import _applica_crs_da_colonne
    from app.shared.resources.metadata import ResourceMetadata

    df = pd.DataFrame({"id": [1], "geometry": ["POINT (11 44)"]})
    meta = ResourceMetadata.from_columns_list(
        [
            {"name": "id", "type": "int"},
            {"name": "geometry", "type": "geometry:polygon:4326"},
        ],
        source_kind="file",
    )

    import geopandas as gpd
    result = _applica_crs_da_colonne(df, node_columns=[], meta=meta)
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.crs.to_epsg() == 4326


# 5. legacy path (no meta) still works


def test_applica_crs_fallback_builds_meta_from_node_columns():
    """Without a pre-built meta, _applica_crs_da_colonne falls back to
    building ResourceMetadata from node_columns - backward compat preserved."""
    from app.core.manipola.executor import _applica_crs_da_colonne
    import geopandas as gpd

    df = pd.DataFrame({"id": [1], "geometry": ["POINT (11 44)"]})
    node_columns = [
        {"name": "id", "type": "int"},
        {"name": "geometry", "type": "geometry:polygon:4326"},
    ]

    result = _applica_crs_da_colonne(df, node_columns)
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.crs.to_epsg() == 4326


# 6. alias normalisation preserved in metadata_map


def test_metadata_map_uses_alias_as_column_name():
    """When canvas columns have aliases, metadata_map stores ResourceMetadata
    with geometry_column set to the alias (the actual DataFrame column name
    after filter_visible_columns)."""
    from app.core.manipola import source_loading as sl

    df = pd.DataFrame({"id": [1], "geom_alias": ["POINT (0 0)"]})
    node = _Node(columns=[
        {"name": "geom_original", "alias": "geom_alias", "type": "geometry:point:32632"},
    ])
    canvas = _Canvas(node)
    dataframes: dict = {}
    metadata_map: dict = {}

    with patch("app.core.artifacts.SourceLoader", lambda: _StubLoader(df)):
        sl.load_file_sources(
            canvas, dataframes,
            group_id="grp-1",
            metadata_map=metadata_map,
        )

    meta = metadata_map.get("node-1")
    assert meta is not None
    assert meta.geometry_column == "geom_alias"
    assert meta.crs_epsg == 32632


def test_metadata_map_accepts_object_columns():
    """Pydantic-style column objects are accepted, not only dicts."""
    from app.core.manipola import source_loading as sl

    class _Column:
        name = "geom_original"
        alias = "geom_alias"
        type = "geometry:point:4326"

    df = pd.DataFrame({"geom_alias": ["POINT (0 0)"]})
    node = _Node(columns=[_Column()])
    canvas = _Canvas(node)
    dataframes: dict = {}
    metadata_map: dict = {}

    with patch("app.core.artifacts.SourceLoader", lambda: _StubLoader(df)):
        result = sl.load_file_sources(
            canvas,
            dataframes,
            group_id="grp-1",
            metadata_map=metadata_map,
        )

    assert result is None
    assert metadata_map["node-1"].geometry_column == "geom_alias"
    assert metadata_map["node-1"].crs_epsg == 4326


def test_project_source_artifact_loader_returns_resource_metadata():
    """Project-source artifacts carry ResourceMetadata at the loader boundary."""
    from pathlib import Path

    from app.core.manipola import source_loading as sl
    from app.shared.resources.metadata import LoadedResource, ResourceMetadata

    df = pd.DataFrame({"geom_raw": ["POINT (0 0)"], "id": [1]})
    node_columns = [
        {
            "name": "geom_raw",
            "alias": "geom_alias",
            "type": "geometry:point:32632",
            "visible": True,
        },
        {"name": "id", "type": "int", "visible": True},
    ]
    captured: dict = {}

    def _spy_apply(data, columns, meta=None):
        captured["data_columns"] = list(data.columns)
        captured["columns"] = columns
        captured["meta"] = meta
        return data

    with patch(
        "app.core.manipola.source_loading.load_dataframe_from_artifact",
        return_value=df,
    ), patch(
        "app.core.manipola.executor._applica_crs_da_colonne",
        side_effect=_spy_apply,
    ):
        loaded = sl.load_project_source_artifact(
            Path("ignored.csv"),
            node_columns,
            source_project_id="proj-up",
            group_id="grp-1",
        )

    assert isinstance(loaded, LoadedResource)
    assert isinstance(loaded.metadata, ResourceMetadata)
    assert loaded.metadata.source_kind == "project"
    assert loaded.metadata.project_id == "proj-up"
    assert loaded.metadata.group_id == "grp-1"
    assert loaded.metadata.geometry_column == "geom_alias"
    assert loaded.metadata.crs_epsg == 32632
    assert loaded.metadata.row_count == 1
    assert captured["meta"] is loaded.metadata
    assert captured["data_columns"] == ["geom_alias", "id"]


def test_load_project_source_artifact_uses_materialized_format_hint(tmp_path):
    """Project-source materialization must not infer format from temp suffix."""

    from app.core.manipola import source_loading as sl

    extensionless = tmp_path / "plenora-result-blob-no-suffix"
    extensionless.write_bytes(b"not inspected by mocked loader")
    node_columns = [{"name": "id", "type": "int", "visible": True}]

    with patch(
        "app.core.manipola.source_loading.load_dataframe_from_artifact",
        return_value=pd.DataFrame({"id": [1]}),
    ) as load_mock, patch(
        "app.core.manipola.executor._applica_crs_da_colonne",
        side_effect=lambda data, columns, meta=None: data,
    ):
        loaded = sl.load_project_source_artifact(
            extensionless,
            node_columns,
            source_project_id="proj-up",
            group_id="grp-1",
            format_hint="xlsx",
        )

    load_mock.assert_called_once_with(path=extensionless, format_hint="xlsx")
    assert loaded.data.to_dict("records") == [{"id": 1}]
