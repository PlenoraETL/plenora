"""
Smoke contract - STRUCT-001 Seam D-D: file source read orchestration.

The module under test is go-ready: every side-effect/collaborator is passed
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


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


def test_load_dataframe_uses_explicit_collaborators_and_cleans_temp(tmp_path):
    from app.services.file_source_read import load_dataframe

    tmp_file = tmp_path / "source.csv"
    tmp_file.write_text("city\nRome\n", encoding="utf-8")
    df = pd.DataFrame({"city": ["Rome"]})
    logger = MagicMock()
    assert_visible = MagicMock()
    get_file_source = MagicMock(return_value=_file_source("csv"))
    temp_file_for = MagicMock(return_value=tmp_file)
    load_by_format = MagicMock(return_value=(df, None))

    with patch("app.shared.resources.file_cleanup.rmtree_safe") as cleanup:
        result_df, result_gdf = load_dataframe(
            "file-1",
            expected_group_id="grp-1",
            assert_visible=assert_visible,
            get_file_source=get_file_source,
            temp_file_for=temp_file_for,
            load_by_format=load_by_format,
            logger=logger,
        )

    assert result_df is df
    assert result_gdf is None
    assert_visible.assert_called_once_with("file-1", "grp-1")
    temp_file_for.assert_called_once_with("file-1", "grp-1")
    load_by_format.assert_called_once_with(tmp_file, "csv", None, None)
    cleanup.assert_called_once_with(tmp_file, logger=logger)


def test_load_dataframe_prefers_explicit_layer_and_sheet(tmp_path):
    from app.services.file_source_read import load_dataframe

    tmp_file = tmp_path / "source.xlsx"
    tmp_file.write_text("x", encoding="utf-8")
    load_by_format = MagicMock(return_value=(pd.DataFrame({"x": [1]}), None))

    with patch("app.shared.resources.file_cleanup.rmtree_safe"):
        load_dataframe(
            "file-1",
            layer="explicit-layer",
            sheet="explicit-sheet",
            expected_group_id="grp-1",
            assert_visible=MagicMock(),
            get_file_source=MagicMock(
                return_value=_file_source("xlsx", layer="stored-layer", sheet="stored-sheet")
            ),
            temp_file_for=MagicMock(return_value=tmp_file),
            load_by_format=load_by_format,
            logger=MagicMock(),
        )

    load_by_format.assert_called_once_with(
        tmp_file, "xlsx", "explicit-layer", "explicit-sheet"
    )


def test_load_dataframe_raises_when_metadata_is_missing():
    from app.services.file_source_read import load_dataframe

    with pytest.raises(ValueError, match="non trovato"):
        load_dataframe(
            "missing",
            expected_group_id="grp-1",
            assert_visible=MagicMock(),
            get_file_source=MagicMock(return_value=None),
            temp_file_for=MagicMock(),
            load_by_format=MagicMock(),
            logger=MagicMock(),
        )


def test_get_layers_info_lists_geospatial_layers_and_cleans_temp(tmp_path):
    from app.services.file_source_read import get_layers_info

    tmp_file = tmp_path / "source.gpkg"
    tmp_file.write_text("x", encoding="utf-8")
    logger = MagicMock()
    list_layers = MagicMock(return_value=["roads", "buildings"])

    with patch("app.shared.resources.file_cleanup.rmtree_safe") as cleanup:
        result = get_layers_info(
            "file-1",
            expected_group_id="grp-1",
            assert_visible=MagicMock(),
            get_file_source=MagicMock(return_value=_file_source("gpkg")),
            temp_file_for=MagicMock(return_value=tmp_file),
            list_layers=list_layers,
            list_sheets=MagicMock(),
            logger=logger,
        )

    plain = _plain(result)
    assert plain["layers"] == ["roads", "buildings"]
    assert plain["sheets"] == []
    list_layers.assert_called_once_with(tmp_file)
    cleanup.assert_called_once_with(tmp_file, logger=logger)


def test_get_layers_info_lists_excel_sheets(tmp_path):
    from app.services.file_source_read import get_layers_info

    tmp_file = tmp_path / "source.xlsx"
    tmp_file.write_text("x", encoding="utf-8")
    list_sheets = MagicMock(return_value=["Sheet1", "Sheet2"])

    with patch("app.shared.resources.file_cleanup.rmtree_safe"):
        result = get_layers_info(
            "file-1",
            expected_group_id="grp-1",
            assert_visible=MagicMock(),
            get_file_source=MagicMock(return_value=_file_source("xlsx")),
            temp_file_for=MagicMock(return_value=tmp_file),
            list_layers=MagicMock(),
            list_sheets=list_sheets,
            logger=MagicMock(),
        )

    plain = _plain(result)
    assert plain["layers"] == []
    assert plain["sheets"] == ["Sheet1", "Sheet2"]
    list_sheets.assert_called_once_with(tmp_file)


def test_get_preview_normalizes_numpy_and_nan_values():
    from app.services.file_source_read import get_preview

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


def test_file_source_service_load_dataframe_wrapper_delegates(tmp_path):
    from app.services.file_service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._assert_visible = MagicMock()
    svc.get_file_source = MagicMock()
    svc._temp_file_for = MagicMock()
    svc._load_by_format = MagicMock()
    svc._logger = MagicMock()

    with patch("app.services.file_source_read.load_dataframe") as delegated:
        delegated.return_value = (pd.DataFrame({"x": [1]}), None)
        svc.load_dataframe("file-1", layer="l", sheet="s", expected_group_id="grp-1")

    delegated.assert_called_once_with(
        "file-1",
        layer="l",
        sheet="s",
        expected_group_id="grp-1",
        assert_visible=svc._assert_visible,
        get_file_source=svc.get_file_source,
        temp_file_for=svc._temp_file_for,
        load_by_format=svc._load_by_format,
        logger=svc._logger,
    )


def test_file_source_service_get_preview_wrapper_delegates():
    from app.services.file_service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._assert_visible = MagicMock()
    svc.get_file_source = MagicMock()

    with patch("app.services.file_source_read.get_preview") as delegated:
        delegated.return_value = {"rows": []}
        result = svc.get_preview("file-1", limit=5, expected_group_id="grp-1")

    assert result == {"rows": []}
    assert delegated.call_args.kwargs["limit"] == 5
    assert delegated.call_args.kwargs["expected_group_id"] == "grp-1"
    assert delegated.call_args.kwargs["assert_visible"] is svc._assert_visible
    assert delegated.call_args.kwargs["get_file_source"] is svc.get_file_source
    assert callable(delegated.call_args.kwargs["load_dataframe_for_preview"])
