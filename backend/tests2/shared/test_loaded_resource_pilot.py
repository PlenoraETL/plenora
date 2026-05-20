"""
Focused contract test: SourceLoader.load_with_metadata produces LoadedResource.

BE-GO-READY-ARCH-058-W1 / BE-GO-READY-ARCH-059-W1.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared]


# Helpers

def _make_file_ref(fid="fs-1"):
    from app.core.artifacts import SourceRef
    return SourceRef.from_node_data("file_source", {"file_source_id": fid})


def _make_project_ref(pid="p-1"):
    from app.core.artifacts import SourceRef
    return SourceRef.from_node_data("project_source", {"source_project_id": pid})


def _make_static_ref():
    from app.core.artifacts import SourceRef
    return SourceRef.from_node_data(
        "staticValues",
        {"fields": [{"name": "label", "value": "test"}]},
    )


_COLUMNS = [
    {"name": "id", "type": "int"},
    {"name": "geometry", "type": "geometry:polygon:4326"},
]


# 1. LoadedResource is importable from shared (boundary guard companion)


def test_loaded_resource_importable_from_shared():
    from app.shared.resources.metadata import LoadedResource
    assert LoadedResource is not None


def test_loaded_resource_is_frozen_dataclass():
    from app.shared.resources.metadata import LoadedResource
    import dataclasses
    assert dataclasses.is_dataclass(LoadedResource)
    # frozen dataclass: assignment must raise
    lr = LoadedResource(data={"x": 1})
    with pytest.raises(Exception):
        lr.data = "something"  # type: ignore[misc]


# 2. load_with_metadata returns LoadedResource with correct data


def test_load_with_metadata_returns_loaded_resource():
    from app.core.artifacts import SourceLoader
    from app.shared.resources.metadata import LoadedResource

    df = pd.DataFrame({"id": [1], "geometry": ["POINT (0 0)"]})
    ref = _make_file_ref()

    loader = SourceLoader()
    with patch.object(loader, "load", return_value=df) as mock_load:
        result = loader.load_with_metadata(
            ref,
            expected_group_id="grp-1",
            resource_columns=_COLUMNS,
        )

    mock_load.assert_called_once_with(
        ref,
        expected_group_id="grp-1",
        is_superadmin=False,
    )
    assert isinstance(result, LoadedResource)
    assert result.data is df


# 3. metadata carries geometry_column and crs_epsg from resource_columns


def test_load_with_metadata_populates_resource_metadata():
    from app.core.artifacts import SourceLoader
    from app.shared.resources.metadata import ResourceMetadata

    df = pd.DataFrame({"id": [1]})
    ref = _make_file_ref()

    loader = SourceLoader()
    with patch.object(loader, "load", return_value=df):
        result = loader.load_with_metadata(
            ref,
            expected_group_id="grp-1",
            resource_columns=_COLUMNS,
        )

    assert isinstance(result.metadata, ResourceMetadata)
    assert result.metadata.geometry_column == "geometry"
    assert result.metadata.crs_epsg == 4326
    assert result.metadata.source_kind == "file"


# 4. resource_columns=None produces LoadedResource with metadata=None


def test_load_with_metadata_none_columns_gives_none_metadata():
    from app.core.artifacts import SourceLoader
    from app.shared.resources.metadata import LoadedResource

    df = pd.DataFrame({"a": [1]})
    ref = _make_file_ref()

    loader = SourceLoader()
    with patch.object(loader, "load", return_value=df):
        result = loader.load_with_metadata(
            ref,
            expected_group_id="grp-1",
            resource_columns=None,
        )

    assert isinstance(result, LoadedResource)
    assert result.data is df
    assert result.metadata is None


# 5. source_kind resolved per ref.kind()


def test_load_with_metadata_project_ref_sets_project_source_kind():
    from app.core.artifacts import SourceLoader
    from app.shared.resources.metadata import ResourceMetadata

    df = pd.DataFrame({"a": [1]})
    ref = _make_project_ref()

    loader = SourceLoader()
    with patch.object(loader, "load", return_value=df):
        result = loader.load_with_metadata(
            ref,
            expected_group_id="grp-1",
            resource_columns=[{"name": "a", "type": "str"}],
        )

    assert isinstance(result.metadata, ResourceMetadata)
    assert result.metadata.source_kind == "project"


# 6. legacy load() return value is unchanged (backward compat)


def test_legacy_load_still_returns_raw_data():
    """load() must not be changed: it still returns the raw DataFrame/GeoDataFrame."""
    from app.core.artifacts import SourceLoader

    df = pd.DataFrame({"a": [1]})
    ref = _make_file_ref()

    loader = SourceLoader()
    with patch.object(loader, "load", return_value=df):
        raw = loader.load(ref, expected_group_id="grp-1")

    assert raw is df


# 7. _source_kind_for_ref — unit tests for the extracted mapping function


def test_source_kind_for_file_ref():
    from app.core.artifacts.source_loader import _source_kind_for_ref
    assert _source_kind_for_ref(_make_file_ref()) == "file"


def test_source_kind_for_project_ref():
    from app.core.artifacts.source_loader import _source_kind_for_ref
    assert _source_kind_for_ref(_make_project_ref()) == "project"


def test_source_kind_for_static_ref():
    from app.core.artifacts.source_loader import _source_kind_for_ref
    assert _source_kind_for_ref(_make_static_ref()) == "static"


# 8. Static source kind coverage (059-W1 expansion)


def test_static_source_no_resource_columns_gives_none():
    """Static values carry schema in static_fields, not canvas columns.
    When no resource_columns is supplied (the typical case), metadata=None.
    """
    from app.core.artifacts import SourceLoader
    from app.shared.resources.metadata import LoadedResource

    df = pd.DataFrame({"label": ["test"]})
    ref = _make_static_ref()

    loader = SourceLoader()
    with patch.object(loader, "load", return_value=df):
        result = loader.load_with_metadata(
            ref,
            expected_group_id="grp-1",
            resource_columns=None,
        )

    assert isinstance(result, LoadedResource)
    assert result.metadata is None


def test_static_source_with_resource_columns_gives_static_source_kind():
    """When a caller explicitly provides resource_columns for a static ref,
    metadata is built with source_kind='static' — not silently coerced to 'file'.
    """
    from app.core.artifacts import SourceLoader
    from app.shared.resources.metadata import ResourceMetadata

    df = pd.DataFrame({"label": ["test"], "value": [42]})
    ref = _make_static_ref()

    loader = SourceLoader()
    with patch.object(loader, "load", return_value=df):
        result = loader.load_with_metadata(
            ref,
            expected_group_id="grp-1",
            resource_columns=[
                {"name": "label", "type": "str"},
                {"name": "value", "type": "int"},
            ],
        )

    assert isinstance(result.metadata, ResourceMetadata)
    assert result.metadata.source_kind == "static"
    assert result.metadata.geometry_column is None
