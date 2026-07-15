"""Contract tests for file-source metadata CRUD extraction."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _metadata(**overrides):
    base = {
        "id": "file-1",
        "name": "source",
        "description": "",
        "original_filename": "source.csv",
        "format": "csv",
        "group_id": "stale-group",
        "columns": [],
        "row_count": 0,
        "file_size": 12,
        "crs": None,
        "created_at": "2026-04-18T00:00:00+00:00",
        "updated_at": "2026-04-18T00:00:00+00:00",
        "created_by": "stale-user",
    }
    base.update(overrides)
    return base


def test_file_source_from_row_overlays_db_owner_columns():
    from app.core.files.models import FileSource
    from app.core.files.source_metadata import file_source_from_row

    row = {
        "id": "file-1",
        "group_id": "db-group",
        "created_by": "db-user",
        "metadata": json.dumps(_metadata()),
    }

    out = file_source_from_row(
        row,
        file_source_model=FileSource,
        use_app_models=True,
    )

    assert out.id == "file-1"
    assert out.group_id == "db-group"
    assert out.created_by == "db-user"


def test_list_file_sources_preserves_none_filter_and_skips_missing_rows():
    from app.core.files.source_metadata import list_file_sources

    class FakeFileSourceList:
        def __init__(self, files):
            self.files = files

    db_list = MagicMock(return_value=[{"id": "a"}, {"id": "gone"}, {"id": "b"}])
    get_by_id = MagicMock(side_effect=lambda fid: None if fid == "gone" else fid)

    out = list_file_sources(
        None,
        db_list_file_sources=db_list,
        get_file_source_by_id=get_by_id,
        file_source_list_model=FakeFileSourceList,
        use_app_models=True,
        logger=MagicMock(),
    )

    db_list.assert_called_once_with(
        None, resource_ids=None, limit=None, offset=0
    )
    assert out.files == ["a", "b"]


def test_update_file_source_refreshes_columns_when_layer_changes():
    from app.core.files.source_metadata import update_file_source

    now = datetime(2026, 5, 9, tzinfo=timezone.utc)

    class FakeFileSource:
        def __init__(self):
            self.id = "file-1"
            self.name = "source"
            self.original_filename = "source.csv"
            self.format = "csv"
            self.group_id = "group-1"
            self.layer = None
            self.sheet = None
            self.columns = []
            self.row_count = 0
            self.updated_at = None

        def model_dump(self, mode=None):
            return {
                "id": self.id,
                "name": self.name,
                "original_filename": self.original_filename,
                "format": self.format,
                "group_id": self.group_id,
                "layer": self.layer,
                "sheet": self.sheet,
                "columns": self.columns,
                "row_count": self.row_count,
                "updated_at": self.updated_at,
            }

    file_source = FakeFileSource()
    load_dataframe = MagicMock(return_value=([{"city": "Rome"}, {"city": "Milan"}], None))
    build_columns = MagicMock(
        return_value=[SimpleNamespace(name="city", type="str")]
    )
    db_update = MagicMock()

    out = update_file_source(
        "file-1",
        {"layer": "roads"},
        get_file_source_by_id=MagicMock(return_value=file_source),
        load_dataframe=load_dataframe,
        column_builder_from_dataframe=build_columns,
        time_now=MagicMock(return_value=now),
        db_update_file_source_metadata=db_update,
        use_app_models=True,
        logger=MagicMock(),
    )

    assert out is file_source
    assert out.layer == "roads"
    assert out.columns == [{"name": "city", "type": "str"}]
    assert out.row_count == 2
    assert out.updated_at is now
    load_dataframe.assert_called_once_with(
        "file-1", layer="roads", sheet=None, expected_group_id="group-1"
    )
    db_update.assert_called_once()
    assert db_update.call_args.args[1]["row_count"] == 2


def test_update_file_source_invalid_sheet_fails_without_mutating_metadata():
    from app.core.files.source_metadata import update_file_source

    file_source = SimpleNamespace(
        id="file-1",
        name="source",
        group_id="group-1",
        layer=None,
        sheet="Original",
        columns=[{"name": "old", "type": "str"}],
        row_count=1,
    )
    db_update = MagicMock()

    with pytest.raises(ValueError, match="Foglio inesistente"):
        update_file_source(
            "file-1",
            {"layer": None, "sheet": "Missing"},
            get_file_source_by_id=MagicMock(return_value=file_source),
            load_dataframe=MagicMock(
                side_effect=ValueError("Foglio inesistente")
            ),
            column_builder_from_dataframe=MagicMock(),
            time_now=MagicMock(),
            db_update_file_source_metadata=db_update,
            use_app_models=True,
            logger=MagicMock(),
        )

    assert file_source.sheet == "Original"
    assert file_source.columns == [{"name": "old", "type": "str"}]
    db_update.assert_not_called()


def test_update_file_source_db_error_does_not_rehydrate_blob():
    from app.core.files.source_metadata import update_file_source

    file_source = SimpleNamespace(
        id="file-1",
        name="old",
        original_filename="source.csv",
        format="csv",
        group_id="owner-group",
        updated_at=None,
        model_dump=lambda mode=None: {
            "id": "file-1",
            "name": "new",
            "original_filename": "source.csv",
            "format": "csv",
            "group_id": "owner-group",
        },
    )
    with pytest.raises(RuntimeError, match="boom"):
        update_file_source(
            "file-1",
            {"name": "new"},
            get_file_source_by_id=MagicMock(return_value=file_source),
            load_dataframe=MagicMock(),
            column_builder_from_dataframe=MagicMock(),
            time_now=MagicMock(
                return_value=datetime(2026, 5, 9, tzinfo=timezone.utc)
            ),
            db_update_file_source_metadata=MagicMock(
                side_effect=RuntimeError("boom")
            ),
            use_app_models=True,
            logger=MagicMock(),
        )


def test_file_source_service_get_file_source_facade_delegates():
    from app.core.files import service as service_module
    from app.core.files.service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._logger = MagicMock()

    with patch("app.core.files.source_metadata.get_file_source") as delegated:
        delegated.return_value = "mapped"
        out = svc.get_file_source("file-1")

    assert out == "mapped"
    assert delegated.call_args.args == ("file-1",)
    assert delegated.call_args.kwargs["file_source_model"] is service_module.FileSource
    assert delegated.call_args.kwargs["use_app_models"] is True
    assert delegated.call_args.kwargs["logger"] is svc._logger


def test_file_source_service_update_file_source_facade_delegates():
    from app.core.files.service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._logger = MagicMock()
    svc._time = MagicMock()
    svc._time.now = MagicMock(return_value="now")
    svc._column_builder = MagicMock()
    svc._column_builder.from_dataframe = MagicMock()

    with patch("app.core.files.source_metadata.update_file_source") as delegated:
        delegated.return_value = "updated"
        out = svc.update_file_source("file-1", {"name": "new"})

    assert out == "updated"
    assert delegated.call_args.args == ("file-1", {"name": "new"})
    assert delegated.call_args.kwargs["get_file_source_by_id"] == svc.get_file_source
    assert delegated.call_args.kwargs["load_dataframe"] == svc.load_dataframe
    assert (
        delegated.call_args.kwargs["column_builder_from_dataframe"]
        is svc._column_builder.from_dataframe
    )
    assert delegated.call_args.kwargs["time_now"] is svc._time.now
    assert delegated.call_args.kwargs["use_app_models"] is True
    assert delegated.call_args.kwargs["logger"] is svc._logger
