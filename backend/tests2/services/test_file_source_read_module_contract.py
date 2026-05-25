"""Smoke contract: file source read orchestration (RAM-only, Phase4).

The module under test is go-ready: every side-effect/collaborator is passed
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


def test_removed_app_services_file_source_read_tombstone():
    assert not (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "file_source_read.py"
    ).exists()


def _plain(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _file_source(fmt="csv", *, layer=None, sheet=None, columns=None):
    return SimpleNamespace(
        id="file-1",
        format=fmt,
        layer=layer,
        sheet=sheet,
        columns=columns or [SimpleNamespace(name="city", type="str")],
    )


def test_load_dataframe_uses_explicit_collaborators_bytes_first():
    from app.core.files.source_read import load_dataframe

    df = pd.DataFrame({"city": ["Rome"]})
    logger = MagicMock()
    assert_visible = MagicMock()
    get_file_source = MagicMock(return_value=_file_source("csv"))
    get_blob = MagicMock(return_value=b"city\nRome\n")

    with patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
        return_value=(df, None),
    ) as load_by_format_bytes:
        result_df, result_gdf = load_dataframe(
            "file-1",
            expected_group_id="grp-1",
            assert_visible=assert_visible,
            get_file_source=get_file_source,
            get_blob=get_blob,
            logger=logger,
        )

    assert result_df is df
    assert result_gdf is None
    assert_visible.assert_called_once_with("file-1", "grp-1")
    get_blob.assert_called_once_with("file-1", "grp-1")
    load_by_format_bytes.assert_called_once_with(
        b"city\nRome\n", "csv", layer=None, sheet=None,
    )


def test_load_dataframe_prefers_explicit_layer_and_sheet():
    from app.core.files.source_read import load_dataframe

    with patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
        return_value=(pd.DataFrame({"x": [1]}), None),
    ) as load_by_format_bytes:
        load_dataframe(
            "file-1",
            layer="explicit-layer",
            sheet="explicit-sheet",
            expected_group_id="grp-1",
            assert_visible=MagicMock(),
            get_file_source=MagicMock(
                return_value=_file_source(
                    "xlsx", layer="stored-layer", sheet="stored-sheet",
                ),
            ),
            get_blob=MagicMock(return_value=b"xlsx-bytes"),
            logger=MagicMock(),
        )

    load_by_format_bytes.assert_called_once_with(
        b"xlsx-bytes", "xlsx",
        layer="explicit-layer", sheet="explicit-sheet",
    )


def test_load_dataframe_raises_when_metadata_is_missing():
    from app.core.files.source_read import load_dataframe

    with pytest.raises(ValueError, match="non trovato"):
        load_dataframe(
            "missing",
            expected_group_id="grp-1",
            assert_visible=MagicMock(),
            get_file_source=MagicMock(return_value=None),
            get_blob=MagicMock(),
            logger=MagicMock(),
        )


def test_load_dataframe_raises_when_blob_missing():
    from app.core.files.source_read import load_dataframe

    with pytest.raises(ValueError, match="Nessun dato trovato"):
        load_dataframe(
            "file-1",
            expected_group_id="grp-1",
            assert_visible=MagicMock(),
            get_file_source=MagicMock(return_value=_file_source("csv")),
            get_blob=MagicMock(return_value=None),
            logger=MagicMock(),
        )


def test_get_layers_info_lists_geospatial_layers_bytes_first():
    from app.core.files.source_read import get_layers_info

    logger = MagicMock()
    get_blob = MagicMock(return_value=b"gpkg-bytes")

    with patch(
        "app.core.io.geospatial.list_vector_layers",
        return_value=["roads", "buildings"],
    ) as list_vector_layers:
        result = get_layers_info(
            "file-1",
            expected_group_id="grp-1",
            assert_visible=MagicMock(),
            get_file_source=MagicMock(return_value=_file_source("gpkg")),
            get_blob=get_blob,
            logger=logger,
        )

    plain = _plain(result)
    assert plain["layers"] == ["roads", "buildings"]
    assert plain["sheets"] == []
    list_vector_layers.assert_called_once_with(b"gpkg-bytes")


def test_get_layers_info_lists_excel_sheets_bytes_first():
    from app.core.files.source_read import get_layers_info

    with patch(
        "app.core.dataframes.load_dispatch.list_excel_sheets_from_bytes",
        return_value=["Sheet1", "Sheet2"],
    ) as list_sheets:
        result = get_layers_info(
            "file-1",
            expected_group_id="grp-1",
            assert_visible=MagicMock(),
            get_file_source=MagicMock(return_value=_file_source("xlsx")),
            get_blob=MagicMock(return_value=b"xlsx-bytes"),
            logger=MagicMock(),
        )

    plain = _plain(result)
    assert plain["layers"] == []
    assert plain["sheets"] == ["Sheet1", "Sheet2"]
    list_sheets.assert_called_once_with(b"xlsx-bytes")


def test_get_preview_normalizes_numpy_and_nan_values():
    from app.core.files.source_read import get_preview

    df = pd.DataFrame(
        {
            "city": ["Rome", "Milan"],
            "score": [np.int64(7), np.nan],
            "ok": [np.bool_(True), np.bool_(False)],
        }
    )

    result = get_preview(
        "file-1",
        limit=1,
        expected_group_id="grp-1",
        assert_visible=MagicMock(),
        get_file_source=MagicMock(
            return_value=_file_source(
                "csv",
                columns=[
                    {"name": "city", "type": "str"},
                    {"name": "score", "type": "int"},
                    {"name": "ok", "type": "bool"},
                ],
            )
        ),
        load_dataframe_for_preview=MagicMock(return_value=(df, None)),
    )

    plain = _plain(result)
    assert plain["total_count"] == 2
    assert plain["preview_count"] == 1
    assert plain["rows"] == [{"city": "Rome", "score": 7, "ok": True}]


def test_file_source_service_load_dataframe_facade_delegates():
    from app.core.files.service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._assert_visible = MagicMock()
    svc.get_file_source = MagicMock()
    svc._get_blob_from_db = MagicMock()
    svc._logger = MagicMock()

    with patch("app.core.files.source_read.load_dataframe") as delegated:
        delegated.return_value = (pd.DataFrame({"x": [1]}), None)
        svc.load_dataframe(
            "file-1", layer="l", sheet="s", expected_group_id="grp-1",
        )

    delegated.assert_called_once_with(
        "file-1",
        layer="l",
        sheet="s",
        expected_group_id="grp-1",
        assert_visible=svc._assert_visible,
        get_file_source=svc.get_file_source,
        get_blob=svc._get_blob_from_db,
        logger=svc._logger,
    )


def test_file_source_service_get_preview_facade_delegates():
    from app.core.files.service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._assert_visible = MagicMock()
    svc.get_file_source = MagicMock()

    with patch("app.core.files.source_read.get_preview") as delegated:
        delegated.return_value = {"rows": []}
        result = svc.get_preview(
            "file-1", limit=5, expected_group_id="grp-1",
        )

    assert result == {"rows": []}
    assert delegated.call_args.kwargs["limit"] == 5
    assert delegated.call_args.kwargs["expected_group_id"] == "grp-1"
    assert delegated.call_args.kwargs["assert_visible"] is svc._assert_visible
    assert delegated.call_args.kwargs["get_file_source"] is svc.get_file_source
    assert callable(delegated.call_args.kwargs["load_dataframe_for_preview"])
