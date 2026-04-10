"""
tests/scheduler/unit/test_dataframe_coverage.py
================================================
Unit tests for app.utils.dataframe — sanitize_for_json, infer_column_types,
filter_visible_columns, get_visible_column_names, get_column_from_node,
dataframe_to_json_records, find_latest_output_file.

Uses real pandas DataFrames. Mocks used only for load/save dispatch logic.
"""

import pytest
import json
from pathlib import Path
from decimal import Decimal
from datetime import datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

pytestmark = pytest.mark.unit

from app.utils.dataframe import (
    sanitize_for_json,
    infer_column_types,
    filter_visible_columns,
    get_visible_column_names,
    get_column_from_node,
    dataframe_to_json_records,
    find_latest_output_file,
    _sanitize_layer_name,
)


# =============================================================================
# sanitize_for_json
# =============================================================================

class TestSanitizeForJson:

    def test_nan_to_none(self):
        rows = [{"a": float("nan"), "b": 1}]
        result = sanitize_for_json(rows)
        assert result[0]["a"] is None
        assert result[0]["b"] == 1

    def test_none_stays_none(self):
        rows = [{"a": None}]
        result = sanitize_for_json(rows)
        assert result[0]["a"] is None

    def test_numpy_int(self):
        rows = [{"val": np.int64(42)}]
        result = sanitize_for_json(rows)
        assert result[0]["val"] == 42
        assert isinstance(result[0]["val"], int)

    def test_numpy_float(self):
        rows = [{"val": np.float64(3.14)}]
        result = sanitize_for_json(rows)
        assert result[0]["val"] == pytest.approx(3.14)
        assert isinstance(result[0]["val"], float)

    def test_numpy_bool(self):
        rows = [{"val": np.bool_(True)}]
        result = sanitize_for_json(rows)
        assert result[0]["val"] is True
        assert isinstance(result[0]["val"], bool)

    def test_numpy_array(self):
        rows = [{"val": np.array([1, 2, 3])}]
        result = sanitize_for_json(rows)
        assert result[0]["val"] == [1, 2, 3]

    def test_pandas_timestamp(self):
        ts = pd.Timestamp("2024-01-15 10:30:00")
        rows = [{"ts": ts}]
        result = sanitize_for_json(rows)
        assert isinstance(result[0]["ts"], str)
        assert "2024" in result[0]["ts"]

    def test_numpy_datetime64(self):
        dt = np.datetime64("2024-06-15")
        rows = [{"dt": dt}]
        result = sanitize_for_json(rows)
        assert isinstance(result[0]["dt"], str)

    def test_bytes_utf8(self):
        rows = [{"data": b"hello"}]
        result = sanitize_for_json(rows)
        assert result[0]["data"] == "hello"

    def test_bytes_non_utf8_to_hex(self):
        rows = [{"data": b"\xff\xfe"}]
        result = sanitize_for_json(rows)
        assert result[0]["data"] == "fffe"

    def test_nested_dict(self):
        rows = [{"meta": {"count": np.int64(5), "ratio": np.float64(0.5)}}]
        result = sanitize_for_json(rows)
        assert result[0]["meta"]["count"] == 5
        assert isinstance(result[0]["meta"]["count"], int)

    def test_nested_list(self):
        rows = [{"items": [np.int64(1), np.float64(2.0), None]}]
        result = sanitize_for_json(rows)
        assert result[0]["items"] == [1, 2.0, None]

    def test_regular_values_unchanged(self):
        rows = [{"s": "hello", "i": 42, "f": 3.14, "b": True}]
        result = sanitize_for_json(rows)
        assert result[0] == {"s": "hello", "i": 42, "f": 3.14, "b": True}

    def test_empty_rows(self):
        result = sanitize_for_json([])
        assert result == []

    def test_json_serializable_after_sanitize(self):
        """Ensure sanitized output can be serialized by json.dumps."""
        rows = [
            {
                "a": np.int64(1),
                "b": np.float64(2.5),
                "c": float("nan"),
                "d": pd.Timestamp("2024-01-01"),
                "e": b"bytes",
                "f": np.bool_(False),
            }
        ]
        result = sanitize_for_json(rows)
        serialized = json.dumps(result)  # Should not raise
        assert isinstance(serialized, str)

    def test_shapely_geometry_wkt(self):
        """If shapely is available, geometry objects are converted to WKT."""
        try:
            from shapely.geometry import Point
            rows = [{"geom": Point(1.0, 2.0)}]
            result = sanitize_for_json(rows)
            assert "POINT" in result[0]["geom"]
        except ImportError:
            pytest.skip("shapely not installed")

    def test_shapely_empty_geometry(self):
        try:
            from shapely.geometry import Point
            empty = Point()
            rows = [{"geom": empty}]
            result = sanitize_for_json(rows)
            assert result[0]["geom"] is None
        except ImportError:
            pytest.skip("shapely not installed")


# =============================================================================
# infer_column_types
# =============================================================================

class TestInferColumnTypes:

    def test_int_column(self):
        df = pd.DataFrame({"id": [1, 2, 3]})
        result = infer_column_types(df)
        assert result[0]["name"] == "id"
        assert result[0]["type"] == "int"

    def test_float_column(self):
        df = pd.DataFrame({"val": [1.1, 2.2]})
        result = infer_column_types(df)
        assert result[0]["type"] == "real"

    def test_bool_column(self):
        df = pd.DataFrame({"flag": [True, False]})
        result = infer_column_types(df)
        assert result[0]["type"] == "bool"

    def test_datetime_column(self):
        df = pd.DataFrame({"ts": pd.to_datetime(["2024-01-01", "2024-06-15"])})
        result = infer_column_types(df)
        assert result[0]["type"] == "date"

    def test_string_column(self):
        df = pd.DataFrame({"name": ["Alice", "Bob"]})
        result = infer_column_types(df)
        assert result[0]["type"] == "str"

    def test_multiple_columns(self):
        df = pd.DataFrame({
            "id": [1],
            "name": ["test"],
            "value": [3.14],
            "flag": [True],
        })
        result = infer_column_types(df)
        types = {r["name"]: r["type"] for r in result}
        assert types["id"] == "int"
        assert types["name"] == "str"
        assert types["value"] == "real"
        assert types["flag"] == "bool"

    def test_empty_dataframe(self):
        df = pd.DataFrame({"a": pd.Series([], dtype="int64")})
        result = infer_column_types(df)
        assert len(result) == 1
        assert result[0]["type"] == "int"


# =============================================================================
# filter_visible_columns
# =============================================================================

class TestFilterVisibleColumns:

    def test_basic_filter(self):
        df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
        node_cols = [
            {"name": "a", "visible": True},
            {"name": "b", "visible": False},
            {"name": "c", "visible": True},
        ]
        result = filter_visible_columns(df, node_cols)
        assert list(result.columns) == ["a", "c"]

    def test_no_node_columns_returns_original(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        result = filter_visible_columns(df, [])
        assert list(result.columns) == ["a", "b"]

    def test_none_node_columns(self):
        df = pd.DataFrame({"a": [1]})
        result = filter_visible_columns(df, None)
        assert list(result.columns) == ["a"]

    def test_default_visible_true(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        node_cols = [{"name": "a"}, {"name": "b"}]  # No "visible" key
        result = filter_visible_columns(df, node_cols)
        assert list(result.columns) == ["a", "b"]

    def test_with_alias(self):
        df = pd.DataFrame({"original": [1]})
        node_cols = [{"name": "original", "visible": True, "alias": "renamed"}]
        result = filter_visible_columns(df, node_cols)
        assert list(result.columns) == ["renamed"]

    def test_missing_columns_in_df(self):
        df = pd.DataFrame({"a": [1]})
        node_cols = [
            {"name": "a", "visible": True},
            {"name": "nonexistent", "visible": True},
        ]
        result = filter_visible_columns(df, node_cols)
        assert list(result.columns) == ["a"]

    def test_all_invisible_returns_original(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        node_cols = [
            {"name": "a", "visible": False},
            {"name": "b", "visible": False},
        ]
        result = filter_visible_columns(df, node_cols)
        assert list(result.columns) == ["a", "b"]

    def test_no_matching_columns_returns_original(self):
        df = pd.DataFrame({"a": [1]})
        node_cols = [{"name": "x", "visible": True}]
        result = filter_visible_columns(df, node_cols)
        assert list(result.columns) == ["a"]


# =============================================================================
# get_visible_column_names
# =============================================================================

class TestGetVisibleColumnNames:

    def test_basic(self):
        cols = [
            {"name": "a", "visible": True},
            {"name": "b", "visible": False},
            {"name": "c", "visible": True},
        ]
        result = get_visible_column_names(cols)
        assert result == ["a", "c"]

    def test_empty(self):
        assert get_visible_column_names([]) == []

    def test_none(self):
        assert get_visible_column_names(None) == []

    def test_default_visible(self):
        cols = [{"name": "a"}, {"name": "b"}]
        result = get_visible_column_names(cols)
        assert result == ["a", "b"]


# =============================================================================
# get_column_from_node
# =============================================================================

class TestGetColumnFromNode:

    def test_from_object_attr(self):
        node = MagicMock()
        node.my_attr = "value"
        result = get_column_from_node(node, "my_attr")
        assert result == "value"

    def test_from_dict(self):
        node = {"my_attr": "value"}
        result = get_column_from_node(node, "my_attr")
        assert result == "value"

    def test_from_data_attr(self):
        node = MagicMock(spec=[])
        node.data = {"nested_attr": "nested_value"}
        # Node doesn't have the attr directly
        result = get_column_from_node(node, "nested_attr", default="fallback")
        assert result == "nested_value"

    def test_default_value(self):
        node = {}
        result = get_column_from_node(node, "missing", default="default_val")
        assert result == "default_val"


# =============================================================================
# dataframe_to_json_records
# =============================================================================

class TestDataframeToJsonRecords:

    def test_basic_conversion(self):
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        result = dataframe_to_json_records(df)
        assert len(result) == 2
        assert result[0]["a"] == 1
        assert result[1]["b"] == "y"

    def test_handles_nan(self):
        df = pd.DataFrame({"a": [1.0, float("nan")]})
        result = dataframe_to_json_records(df)
        assert result[1]["a"] is None


# =============================================================================
# find_latest_output_file
# =============================================================================

class TestFindLatestOutputFile:

    def test_finds_latest(self, tmp_path):
        import time
        f1 = tmp_path / "old.csv"
        f1.write_text("old")
        time.sleep(0.05)
        f2 = tmp_path / "new.csv"
        f2.write_text("new")

        result = find_latest_output_file(tmp_path)
        assert result == f2

    def test_empty_dir(self, tmp_path):
        result = find_latest_output_file(tmp_path)
        assert result is None

    def test_nonexistent_dir(self):
        result = find_latest_output_file(Path("/nonexistent/dir"))
        assert result is None


# =============================================================================
# _sanitize_layer_name
# =============================================================================

class TestSanitizeLayerName:

    def test_clean_name(self):
        assert _sanitize_layer_name("my_layer") == "my_layer"

    def test_special_chars(self):
        result = _sanitize_layer_name("my-layer.v2")
        assert result == "my_layer_v2"

    def test_starts_with_digit(self):
        result = _sanitize_layer_name("123layer")
        assert result.startswith("_")

    def test_empty_string(self):
        result = _sanitize_layer_name("")
        assert result == "layer"


# =============================================================================
# save_dataframe — format dispatch (mocked)
# =============================================================================

class TestSaveDataframe:

    def test_csv_format(self, tmp_path):
        from app.utils.dataframe import save_dataframe
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        path = save_dataframe(df, tmp_path / "out.csv", format="csv")
        assert path.exists()
        assert path.suffix == ".csv"

    def test_json_format(self, tmp_path):
        from app.utils.dataframe import save_dataframe
        df = pd.DataFrame({"a": [1], "b": ["test"]})
        path = save_dataframe(df, tmp_path / "out.json", format="json")
        assert path.exists()
        assert path.suffix == ".json"
        content = json.loads(path.read_text())
        assert isinstance(content, list)

    def test_parquet_format(self, tmp_path):
        from app.utils.dataframe import save_dataframe
        df = pd.DataFrame({"a": [1, 2]})
        path = save_dataframe(df, tmp_path / "out.parquet", format="parquet")
        assert path.exists()
        assert path.suffix == ".parquet"

    def test_xlsx_format(self, tmp_path):
        from app.utils.dataframe import save_dataframe
        df = pd.DataFrame({"a": [1]})
        path = save_dataframe(df, tmp_path / "out.xlsx", format="xlsx")
        assert path.exists()
        assert path.suffix == ".xlsx"

    def test_unsupported_format_raises(self, tmp_path):
        from app.utils.dataframe import save_dataframe
        df = pd.DataFrame({"a": [1]})
        with pytest.raises(ValueError, match="non supportato"):
            save_dataframe(df, tmp_path / "out", format="avro")


# =============================================================================
# load_dataframe_from_file — basic formats
# =============================================================================

class TestLoadDataframe:

    def test_csv(self, tmp_path):
        from app.utils.dataframe import load_dataframe_from_file
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("col1,col2\n1,2\n3,4")
        df = load_dataframe_from_file(csv_file)
        assert len(df) == 2
        assert list(df.columns) == ["col1", "col2"]

    def test_json(self, tmp_path):
        from app.utils.dataframe import load_dataframe_from_file
        json_file = tmp_path / "test.json"
        json_file.write_text(json.dumps([{"a": 1}, {"a": 2}]))
        df = load_dataframe_from_file(json_file)
        assert len(df) == 2

    def test_file_not_found(self):
        from app.utils.dataframe import load_dataframe_from_file
        with pytest.raises(FileNotFoundError):
            load_dataframe_from_file(Path("/nonexistent/file.csv"))

    def test_parquet(self, tmp_path):
        from app.utils.dataframe import load_dataframe_from_file
        parquet_file = tmp_path / "test.parquet"
        pd.DataFrame({"x": [1, 2]}).to_parquet(parquet_file)
        df = load_dataframe_from_file(parquet_file)
        assert len(df) == 2
