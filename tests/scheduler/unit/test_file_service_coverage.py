"""
tests/scheduler/unit/test_file_service_coverage.py
===================================================
Unit tests for app.services.file_service — pure functions (FormatDetector,
TypeInferrer, FileIdGenerator, ColumnInfoBuilder), FileSourceService CRUD.

Uses mock filesystem/data_loader via DI (no real I/O).
"""

import pytest
import json
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock, patch
from io import BytesIO

import pandas as pd
import numpy as np

pytestmark = pytest.mark.unit

from app.services.file_service import (
    FormatDetector,
    TypeInferrer,
    FileIdGenerator,
    ColumnInfoBuilder,
    FileSourceService,
    DefaultTimeProvider,
)


class _TestLogger:
    """Logger for tests that accepts any kwargs (like exc_info)."""
    def info(self, msg, *args, **kwargs): pass
    def error(self, msg, *args, **kwargs): pass
    def debug(self, msg, *args, **kwargs): pass
    def warning(self, msg, *args, **kwargs): pass


# =============================================================================
# FormatDetector
# =============================================================================

class TestFormatDetector:

    @pytest.mark.parametrize("filename,expected", [
        ("data.csv", "csv"),
        ("data.CSV", "csv"),
        ("file.xlsx", "xlsx"),
        ("file.xls", "xlsx"),
        ("geo.geojson", "geojson"),
        ("geo.gpkg", "gpkg"),
        ("geo.shp", "shp"),
        ("geo.gdb", "gdb"),
        ("archive.zip", "zip"),
        ("data.json", "json"),
        ("data.parquet", "unknown"),
        ("noext", "unknown"),
    ])
    def test_detect(self, filename, expected):
        assert FormatDetector.detect(filename) == expected

    @pytest.mark.parametrize("fmt,expected", [
        ("geojson", True),
        ("gpkg", True),
        ("shp", True),
        ("gdb", True),
        ("csv", False),
        ("xlsx", False),
    ])
    def test_is_geospatial(self, fmt, expected):
        assert FormatDetector.is_geospatial(fmt) == expected

    @pytest.mark.parametrize("fmt,expected", [
        ("csv", True),
        ("xlsx", True),
        ("json", True),
        ("geojson", False),
        ("gpkg", False),
    ])
    def test_is_tabular(self, fmt, expected):
        assert FormatDetector.is_tabular(fmt) == expected


# =============================================================================
# TypeInferrer
# =============================================================================

class TestTypeInferrer:

    def test_int_column(self):
        s = pd.Series([1, 2, 3], dtype="int64")
        assert TypeInferrer.infer_column_type(s) == "int"

    def test_float_column(self):
        s = pd.Series([1.1, 2.2], dtype="float64")
        assert TypeInferrer.infer_column_type(s) == "real"

    def test_bool_column(self):
        s = pd.Series([True, False], dtype="bool")
        assert TypeInferrer.infer_column_type(s) == "bool"

    def test_datetime_column(self):
        s = pd.Series(pd.to_datetime(["2024-01-01", "2024-06-15"]))
        assert TypeInferrer.infer_column_type(s) == "date"

    def test_object_string(self):
        s = pd.Series(["hello", "world"])
        assert TypeInferrer.infer_column_type(s) == "str"

    def test_wkt_geometry(self):
        s = pd.Series(["POINT(0 0)", "POINT(1 1)"])
        assert TypeInferrer.infer_column_type(s) == "geometry"

    def test_empty_series(self):
        s = pd.Series([None, None], dtype="object")
        assert TypeInferrer.infer_column_type(s) == "str"

    def test_date_from_values(self):
        s = pd.Series(["2024-01-01", "2024-06-15"], dtype="object")
        result = TypeInferrer.infer_column_type(s)
        assert result == "date"

    def test_detect_geometry_type_point(self):
        result = TypeInferrer.detect_geometry_type(["Point"])
        assert result == "geometry:point"

    def test_detect_geometry_type_polygon(self):
        result = TypeInferrer.detect_geometry_type(["Polygon"])
        assert result == "geometry:polygon"

    def test_detect_geometry_type_empty(self):
        result = TypeInferrer.detect_geometry_type([])
        assert result == "geometry"

    def test_detect_geometry_type_unknown(self):
        result = TypeInferrer.detect_geometry_type(["unknown_type"])
        assert result == "geometry"


# =============================================================================
# FileIdGenerator
# =============================================================================

class TestFileIdGenerator:

    def test_generate(self):
        fid = FileIdGenerator.generate("My File", "20260101_120000", "abcd1234")
        assert fid == "file_My_File_20260101_120000_abcd1234"

    def test_special_chars(self):
        fid = FileIdGenerator.generate("t@st!#file", "20260101_120000", "abcd1234")
        assert "@" not in fid
        assert "!" not in fid

    def test_truncation(self):
        fid = FileIdGenerator.generate("A" * 100, "20260101_120000", "abcd1234")
        parts = fid.split("_")
        # file_ prefix, then name (truncated), then timestamp parts, then uuid
        assert len(parts[1]) <= 30

    def test_sanitize_name(self):
        result = FileIdGenerator.sanitize_name("My File @#$", max_length=10)
        assert len(result) <= 10
        assert "@" not in result


# =============================================================================
# ColumnInfoBuilder
# =============================================================================

class TestColumnInfoBuilder:

    def test_from_dataframe_basic(self):
        df = pd.DataFrame({
            "id": [1, 2, 3],
            "name": ["a", "b", "c"],
            "value": [1.1, 2.2, 3.3],
        })
        cols = ColumnInfoBuilder.from_dataframe(df)
        assert len(cols) == 3
        names = [c.name for c in cols]
        assert "id" in names
        types = {c.name: c.type for c in cols}
        assert types["id"] == "int"
        assert types["value"] == "real"
        assert types["name"] == "str"

    def test_from_dataframe_with_datetime(self):
        df = pd.DataFrame({
            "ts": pd.to_datetime(["2024-01-01"]),
        })
        cols = ColumnInfoBuilder.from_dataframe(df)
        assert cols[0].type == "date"

    def test_from_dataframe_with_bool(self):
        df = pd.DataFrame({"flag": [True, False]})
        cols = ColumnInfoBuilder.from_dataframe(df)
        assert cols[0].type == "bool"


# =============================================================================
# FileSourceService — initialization
# =============================================================================

class TestFileSourceServiceInit:

    def test_default_init(self):
        svc = FileSourceService()
        assert svc._files_dir == Path("/tmp/files")

    def test_custom_init(self):
        svc = FileSourceService(
            files_dir=Path("/custom"),
            logger=_TestLogger(),
        )
        assert svc.FILES_DIR == Path("/custom")


# =============================================================================
# FileSourceService — helper paths
# =============================================================================

class TestHelperPaths:

    def test_get_file_dir(self):
        svc = FileSourceService(files_dir=Path("/data/files"))
        assert svc._get_file_dir("file_1") == Path("/data/files/file_1")

    def test_get_metadata_path(self):
        svc = FileSourceService(files_dir=Path("/data/files"))
        assert svc._get_metadata_path("file_1") == Path("/data/files/file_1/metadata.json")


# =============================================================================
# FileSourceService — format detection
# =============================================================================

class TestDetectFormat:

    def test_csv(self):
        svc = FileSourceService()
        assert svc._detect_format("data.csv") == "csv"

    def test_geojson(self):
        svc = FileSourceService()
        assert svc._detect_format("layer.geojson") == "geojson"


# =============================================================================
# FileSourceService — _load_by_format
# =============================================================================

class TestLoadByFormat:

    def test_csv_format(self):
        mock_loader = MagicMock()
        mock_loader.load_csv.return_value = pd.DataFrame({"a": [1]})
        svc = FileSourceService(data_loader=mock_loader, logger=_TestLogger())

        df, gdf = svc._load_by_format(Path("/tmp/test.csv"), "csv")
        assert len(df) == 1
        assert gdf is None

    def test_xlsx_format(self):
        mock_loader = MagicMock()
        mock_loader.load_excel.return_value = pd.DataFrame({"b": [2]})
        svc = FileSourceService(data_loader=mock_loader, logger=_TestLogger())

        df, gdf = svc._load_by_format(Path("/tmp/test.xlsx"), "xlsx", sheet="Sheet1")
        assert len(df) == 1

    def test_json_format(self):
        mock_loader = MagicMock()
        mock_loader.load_json.return_value = pd.DataFrame({"c": [3]})
        svc = FileSourceService(data_loader=mock_loader, logger=_TestLogger())

        df, gdf = svc._load_by_format(Path("/tmp/test.json"), "json")
        assert len(df) == 1

    def test_unsupported_format(self):
        svc = FileSourceService(logger=_TestLogger())
        with pytest.raises(ValueError, match="non supportato"):
            svc._load_by_format(Path("/tmp/test.xyz"), "xyz")


# =============================================================================
# FileSourceService — _find_data_file
# =============================================================================

class TestFindDataFile:

    def test_find_shp(self):
        mock_fs = MagicMock()
        svc = FileSourceService(filesystem=mock_fs, logger=_TestLogger())
        files = [Path("/data/test.dbf"), Path("/data/test.shp"), Path("/data/test.prj")]
        result = svc._find_data_file(files, "shp")
        assert result == Path("/data/test.shp")

    def test_find_gpkg(self):
        mock_fs = MagicMock()
        svc = FileSourceService(filesystem=mock_fs, logger=_TestLogger())
        files = [Path("/data/layer.gpkg")]
        result = svc._find_data_file(files, "gpkg")
        assert result == Path("/data/layer.gpkg")

    def test_find_default(self):
        mock_fs = MagicMock()
        svc = FileSourceService(filesystem=mock_fs, logger=_TestLogger())
        files = [Path("/data/test.csv")]
        result = svc._find_data_file(files, "csv")
        assert result == Path("/data/test.csv")

    def test_find_empty_list(self):
        mock_fs = MagicMock()
        svc = FileSourceService(filesystem=mock_fs, logger=_TestLogger())
        result = svc._find_data_file([], "csv")
        assert result is None

    def test_find_gdb(self):
        mock_fs = MagicMock()
        mock_fs.is_dir.return_value = True
        svc = FileSourceService(filesystem=mock_fs, logger=_TestLogger())
        files = [Path("/data/layer.gdb")]
        result = svc._find_data_file(files, "gdb")
        assert result == Path("/data/layer.gdb")


# =============================================================================
# FileSourceService — CRUD (DB-backed)
# =============================================================================

class TestGetFileSource:

    def test_get_found(self):
        svc = FileSourceService(logger=_TestLogger())
        metadata = {
            "id": "file_1", "name": "test", "description": "",
            "file_path": "test.csv", "original_filename": "test.csv",
            "format": "csv", "layer": None, "sheet": None,
            "columns": [], "row_count": 0, "file_size": 100,
            "crs": None, "group_id": "default",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        with patch("app.db.db_service.get_file_source") as mock_get:
            mock_get.return_value = {
                "id": "file_1",
                "metadata": metadata,
                "group_id": "default",
            }
            result = svc.get_file_source("file_1")
            assert result is not None

    def test_get_not_found(self):
        svc = FileSourceService(logger=_TestLogger())
        with patch("app.db.db_service.get_file_source", return_value=None):
            result = svc.get_file_source("missing")
            assert result is None


class TestDeleteFileSource:

    def test_delete_success(self):
        svc = FileSourceService(logger=_TestLogger())
        with patch("app.db.db_service.get_file_source", return_value={"id": "f1"}):
            with patch("app.db.db_service.delete_file_source") as mock_del:
                result = svc.delete_file_source("f1")
                assert result is True
                mock_del.assert_called_once_with("f1")

    def test_delete_not_found(self):
        svc = FileSourceService(logger=_TestLogger())
        with patch("app.db.db_service.get_file_source", return_value=None):
            result = svc.delete_file_source("missing")
            assert result is False


# =============================================================================
# FileSourceService — list/layers/sheets
# =============================================================================

class TestListLayersSheets:

    def test_list_layers(self):
        mock_loader = MagicMock()
        mock_loader.list_layers.return_value = ["layer1", "layer2"]
        svc = FileSourceService(data_loader=mock_loader, logger=_TestLogger())
        result = svc.list_layers(Path("/tmp/test.gpkg"))
        assert result == ["layer1", "layer2"]

    def test_list_layers_error(self):
        mock_loader = MagicMock()
        mock_loader.list_layers.side_effect = RuntimeError("no fiona")
        svc = FileSourceService(data_loader=mock_loader, logger=_TestLogger())
        result = svc.list_layers(Path("/tmp/test.gpkg"))
        assert result == []

    def test_list_sheets(self):
        mock_loader = MagicMock()
        mock_loader.list_sheets.return_value = ["Sheet1", "Sheet2"]
        svc = FileSourceService(data_loader=mock_loader, logger=_TestLogger())
        result = svc.list_sheets(Path("/tmp/test.xlsx"))
        assert result == ["Sheet1", "Sheet2"]

    def test_list_sheets_error(self):
        mock_loader = MagicMock()
        mock_loader.list_sheets.side_effect = RuntimeError("no openpyxl")
        svc = FileSourceService(data_loader=mock_loader, logger=_TestLogger())
        result = svc.list_sheets(Path("/tmp/test.xlsx"))
        assert result == []


# =============================================================================
# FileSourceService — _filter_columns
# =============================================================================

class TestFilterColumns:

    def test_filter(self):
        svc = FileSourceService(logger=_TestLogger())
        df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
        result = svc._filter_columns(df, ["a", "c"])
        assert list(result.columns) == ["a", "c"]

    def test_filter_invalid_columns(self):
        svc = FileSourceService(logger=_TestLogger())
        df = pd.DataFrame({"a": [1], "b": [2]})
        result = svc._filter_columns(df, ["x", "y"])
        # No valid columns -> return original
        assert list(result.columns) == ["a", "b"]
