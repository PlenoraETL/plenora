"""Behavioral coverage for ``app.core.dataframes.tabular``.

Esercita l'happy-path e i rami significativi delle helper pubbliche del
modulo tabular: serializzazione JSON/REST, normalizzazione decimali/float,
operazioni group/window, diff tabellare, costruttori di frame/series e
predicati di dtype. Tutti i test usano piccoli DataFrame/Series inline.
"""

from __future__ import annotations

from decimal import Decimal

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from app.core.dataframes import tabular as T


pytestmark = [
    pytest.mark.unit,
]


# --------------------------------------------------------------------------
# CSV reading helpers
# --------------------------------------------------------------------------


class TestReadCsvSafeBytes:
    def test_reads_plain_utf8(self):
        data = b"a,b\n1,2\n3,4\n"
        df = T.read_csv_safe_bytes(data)
        assert list(df.columns) == ["a", "b"]
        assert df.shape == (2, 2)
        assert df["a"].tolist() == [1, 3]

    def test_falls_back_to_cp1252_for_italian_chars(self):
        # "città" encoded in cp1252 is not valid utf-8 -> fallback path.
        data = "name\ncittà\n".encode("cp1252")
        df = T.read_csv_safe_bytes(data)
        assert df["name"].tolist() == ["città"]


# --------------------------------------------------------------------------
# Decimal / float normalization
# --------------------------------------------------------------------------


class TestCompactDecimal:
    def test_strips_trailing_zeros(self):
        assert T._compact_decimal(Decimal("1.2300")) == "1.23"

    def test_whole_number_has_no_dot(self):
        assert T._compact_decimal(Decimal("5.000")) == "5"

    def test_zero(self):
        assert T._compact_decimal(Decimal("0.0")) == "0"

    def test_non_finite_passthrough(self):
        assert T._compact_decimal(Decimal("NaN")) == "NaN"


class TestNormalizeObjectDecimals:
    def test_per_value_mapping_to_compact_strings(self):
        df = pd.DataFrame({"x": [Decimal("1.50"), Decimal("2.000")]})
        out = T.normalize_object_decimals(df)
        assert out["x"].tolist() == ["1.5", "2"]

    def test_does_not_mutate_input(self):
        df = pd.DataFrame({"x": [Decimal("1.50")]})
        T.normalize_object_decimals(df)
        assert isinstance(df["x"].iloc[0], Decimal)

    def test_empty_returns_same(self):
        df = pd.DataFrame()
        assert T.normalize_object_decimals(df) is df

    def test_non_decimal_object_column_untouched(self):
        df = pd.DataFrame({"x": ["a", "b"]})
        out = T.normalize_object_decimals(df)
        assert out["x"].tolist() == ["a", "b"]

    def test_preserves_nan(self):
        df = pd.DataFrame({"x": [Decimal("1.0"), np.nan]})
        out = T.normalize_object_decimals(df)
        assert out["x"].iloc[0] == "1"
        assert pd.isna(out["x"].iloc[1])


class TestCompactWholeNumberFloats:
    def test_whole_floats_become_int64(self):
        df = pd.DataFrame({"x": [1.0, 2.0, 3.0]})
        out = T._compact_whole_number_floats(df)
        assert str(out["x"].dtype) == "Int64"

    def test_non_whole_floats_untouched(self):
        df = pd.DataFrame({"x": [1.5, 2.0]})
        out = T._compact_whole_number_floats(df)
        assert pd.api.types.is_float_dtype(out["x"])

    def test_whole_decimals_become_int64(self):
        df = pd.DataFrame({"x": [Decimal("1"), Decimal("2")]})
        out = T._compact_whole_number_floats(df)
        assert str(out["x"].dtype) == "Int64"
        assert out["x"].tolist() == [1, 2]

    def test_fractional_decimals_become_compact_strings(self):
        df = pd.DataFrame({"x": [Decimal("1.5"), Decimal("2.25")]})
        out = T._compact_whole_number_floats(df)
        assert out["x"].tolist() == ["1.5", "2.25"]

    def test_empty_returns_same(self):
        df = pd.DataFrame()
        assert T._compact_whole_number_floats(df) is df


# --------------------------------------------------------------------------
# Column naming
# --------------------------------------------------------------------------


class TestMakeUniqueColumnNames:
    def test_no_duplicates_returns_empty_renames(self):
        unique, renames = T.make_unique_column_names(["a", "b"])
        assert unique == ["a", "b"]
        assert renames == []

    def test_duplicates_get_suffixed(self):
        unique, renames = T.make_unique_column_names(["id", "id"])
        assert unique == ["id", "id_2"]
        assert renames == [("id", "id_2")]

    def test_avoids_existing_reserved_name(self):
        # docstring example: ["id","id","id_2"] -> ["id","id_3","id_2"]
        unique, renames = T.make_unique_column_names(["id", "id", "id_2"])
        assert unique == ["id", "id_3", "id_2"]
        assert renames == [("id", "id_3")]

    def test_non_string_columns_coerced(self):
        unique, _ = T.make_unique_column_names([1, 2])
        assert unique == ["1", "2"]


class TestRenameDuplicateColumns:
    def test_no_duplicates_returns_same_object(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        out, renames = T.rename_duplicate_columns(df)
        assert out is df
        assert renames == []

    def test_duplicates_renamed_without_data_loss(self):
        df = pd.DataFrame([[1, 2]], columns=["x", "x"])
        out, renames = T.rename_duplicate_columns(df)
        assert list(out.columns) == ["x", "x_2"]
        assert renames == [("x", "x_2")]
        assert out.iloc[0].tolist() == [1, 2]


# --------------------------------------------------------------------------
# Visible column filtering
# --------------------------------------------------------------------------


class TestFilterVisibleColumns:
    def test_empty_node_columns_returns_df(self):
        df = pd.DataFrame({"a": [1]})
        assert T.filter_visible_columns(df, []) is df

    def test_selects_visible_and_applies_alias(self):
        df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
        node_cols = [
            {"name": "a", "visible": True},
            {"name": "b", "visible": False},
            {"name": "c", "alias": "C", "visible": True},
        ]
        out = T.filter_visible_columns(df, node_cols)
        assert list(out.columns) == ["a", "C"]

    def test_no_visible_columns_returns_df(self):
        df = pd.DataFrame({"a": [1]})
        node_cols = [{"name": "a", "visible": False}]
        assert T.filter_visible_columns(df, node_cols) is df

    def test_missing_column_falls_back_to_df(self):
        df = pd.DataFrame({"a": [1]})
        node_cols = [{"name": "zzz", "visible": True}]
        assert T.filter_visible_columns(df, node_cols) is df


# --------------------------------------------------------------------------
# JSON sanitization
# --------------------------------------------------------------------------


class TestSanitizeForJson:
    def test_numpy_scalars_to_python(self):
        rows = [{"i": np.int64(3), "f": np.float64(1.5), "b": np.bool_(True)}]
        out = T.sanitize_for_json(rows)
        assert out == [{"i": 3, "f": 1.5, "b": True}]
        assert type(out[0]["i"]) is int

    def test_nan_and_none_to_none(self):
        out = T.sanitize_for_json([{"a": np.nan, "b": None}])
        assert out == [{"a": None, "b": None}]

    def test_nested_list_and_dict(self):
        out = T.sanitize_for_json([{"a": [np.int64(1), np.int64(2)], "d": {"k": np.int64(5)}}])
        assert out == [{"a": [1, 2], "d": {"k": 5}}]

    def test_ndarray_to_list(self):
        out = T.sanitize_for_json([{"a": np.array([1, 2])}])
        assert out == [{"a": [1, 2]}]

    def test_timestamp_to_string(self):
        out = T.sanitize_for_json([{"t": pd.Timestamp("2020-01-01")}])
        assert out[0]["t"].startswith("2020-01-01")

    def test_bytes_utf8_decoded(self):
        out = T.sanitize_for_json([{"b": b"hi"}])
        assert out == [{"b": "hi"}]

    def test_bytes_non_utf8_to_hex(self):
        out = T.sanitize_for_json([{"b": b"\xff\xfe"}])
        assert out == [{"b": "fffe"}]

    def test_shapely_geometry_to_wkt(self):
        out = T.sanitize_for_json([{"g": Point(1, 2)}])
        assert out[0]["g"] == "POINT (1 2)"

    def test_does_not_mutate_input(self):
        rows = [{"i": np.int64(3)}]
        T.sanitize_for_json(rows)
        assert isinstance(rows[0]["i"], np.integer)


# --------------------------------------------------------------------------
# REST record iteration
# --------------------------------------------------------------------------


class TestRestJsonSafeValue:
    def test_none(self):
        assert T._rest_json_safe_value(None) is None

    def test_pd_na(self):
        assert T._rest_json_safe_value(pd.NA) is None

    def test_nan_float(self):
        assert T._rest_json_safe_value(float("nan")) is None

    def test_bool(self):
        assert T._rest_json_safe_value(True) is True

    def test_numpy_int(self):
        assert T._rest_json_safe_value(np.int64(7)) == 7

    def test_numpy_float_nan(self):
        assert T._rest_json_safe_value(np.float64("nan")) is None

    def test_timestamp(self):
        assert T._rest_json_safe_value(pd.Timestamp("2020-01-01")).startswith("2020")

    def test_nat(self):
        assert T._rest_json_safe_value(pd.NaT) is None

    def test_fallback_str(self):
        assert T._rest_json_safe_value(Decimal("1.5")) == "1.5"


class TestIterDataframeRestRecords:
    def test_yields_normalized_records(self):
        df = pd.DataFrame({"a": [1, 2], "b": [np.nan, 3.0]})
        recs = list(T.iter_dataframe_rest_records(df))
        assert recs[0] == {"a": 1, "b": None}
        assert recs[1] == {"a": 2, "b": 3.0}


# --------------------------------------------------------------------------
# Preview / record accessors
# --------------------------------------------------------------------------


class TestRecordAccessors:
    def _df(self):
        return pd.DataFrame({"a": [10, 20, 30], "b": ["x", "y", "z"]})

    def test_preview_records_limit(self):
        out = T.dataframe_preview_records(self._df(), 2)
        assert out == [{"a": 10, "b": "x"}, {"a": 20, "b": "y"}]

    def test_records_all(self):
        out = T.dataframe_records(self._df())
        assert len(out) == 3

    def test_row_dict_by_index(self):
        out = T.dataframe_row_dict(self._df(), 1)
        assert out == {"a": 20, "b": "y"}

    def test_row_by_index(self):
        row = T.dataframe_row_by_index(self._df(), 0)
        assert row["a"] == 10

    def test_first_row_dict(self):
        assert T.dataframe_first_row_dict(self._df()) == {"a": 10, "b": "x"}

    def test_first_column(self):
        col = T.dataframe_first_column(self._df())
        assert col.tolist() == [10, 20, 30]

    def test_column_by_position(self):
        col = T.dataframe_column_by_position(self._df(), 1)
        assert col.tolist() == ["x", "y", "z"]

    def test_head_records_raw(self):
        out = T.dataframe_head_records(self._df(), 1)
        assert out == [{"a": 10, "b": "x"}]

    def test_iter_indexed_rows(self):
        pairs = list(T.iter_dataframe_indexed_rows(self._df()))
        assert [idx for idx, _ in pairs] == [0, 1, 2]

    def test_iter_rows(self):
        rows = list(T.iter_dataframe_rows(self._df()))
        assert rows[0]["b"] == "x"


# --------------------------------------------------------------------------
# Mutation helpers
# --------------------------------------------------------------------------


class TestMutationHelpers:
    def test_set_cell_value(self):
        df = pd.DataFrame({"a": [1, 2]})
        T.set_cell_value(df, 0, "a", 99)
        assert df.at[0, "a"] == 99

    def test_set_values_where_mask(self):
        df = pd.DataFrame({"a": [1, 2, 3]})
        T.set_values_where_mask(df, df["a"] > 1, "a", 0)
        assert df["a"].tolist() == [1, 0, 0]


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------


class TestSampling:
    def test_sample_dataframe_deterministic(self):
        df = pd.DataFrame({"a": range(10)})
        out = T.sample_dataframe(df, n=3, random_state=42)
        assert len(out) == 3

    def test_sample_rows_reset_index_n(self):
        df = pd.DataFrame({"a": range(10)})
        out = T.sample_rows_reset_index(df, n=4, random_state=1)
        assert list(out.index) == [0, 1, 2, 3]

    def test_sample_rows_reset_index_frac(self):
        df = pd.DataFrame({"a": range(10)})
        out = T.sample_rows_reset_index(df, frac=0.5, random_state=1)
        assert len(out) == 5


# --------------------------------------------------------------------------
# Counting / uniqueness
# --------------------------------------------------------------------------


class TestCounting:
    def test_null_count_for_columns(self):
        df = pd.DataFrame({"a": [1, np.nan, 3], "b": [np.nan, np.nan, 1]})
        assert T.null_count_for_columns(df, ["a", "b", "missing"]) == 3

    def test_duplicate_row_count(self):
        df = pd.DataFrame({"a": [1, 1, 2], "b": [9, 9, 8]})
        assert T.duplicate_row_count(df, ["a", "b"]) == 1

    def test_unique_non_null_column_values(self):
        df = pd.DataFrame({"a": [1, 1, np.nan, 2]})
        out = T.unique_non_null_column_values(df, "a")
        assert sorted(out) == [1.0, 2.0]

    def test_duplicate_non_null_value_count(self):
        # duplicated(keep=False) flags both 1s (2) minus the null count (1) -> 1.
        df = pd.DataFrame({"a": [1, 1, 2, np.nan]})
        assert T.duplicate_non_null_value_count(df, "a") == 1

    def test_duplicate_non_null_value_count_no_nulls(self):
        df = pd.DataFrame({"a": [1, 1, 1, 2]})
        # all three 1s flagged by keep=False -> 3, no nulls
        assert T.duplicate_non_null_value_count(df, "a") == 3


# --------------------------------------------------------------------------
# Filtering / indexing
# --------------------------------------------------------------------------


class TestFiltering:
    def test_filter_rows_by_predicate(self):
        df = pd.DataFrame({"a": [1, 2, 3]})
        out = T.filter_rows_by_column_predicate(df, "a", lambda v: v > 1)
        assert out["a"].tolist() == [2, 3]

    def test_row_indices_where_column_in(self):
        df = pd.DataFrame({"a": [1, 2, 3]})
        assert T.row_indices_where_column_in(df, "a", [2, 3]) == [1, 2]

    def test_unique_rows_for_columns(self):
        df = pd.DataFrame({"a": [1, 1, 2], "b": [9, 9, 8]})
        out = T.unique_rows_for_columns(df, ["a", "b"])
        assert len(out) == 2

    def test_matching_row_indices(self):
        df = pd.DataFrame({"a": [1, 2, 2], "b": ["x", "y", "y"]})
        assert T.matching_row_indices(df, {"a": 2, "b": "y"}) == [1, 2]

    def test_matching_row_indices_with_null(self):
        df = pd.DataFrame({"a": [1, np.nan, np.nan]})
        assert T.matching_row_indices(df, {"a": np.nan}) == [1, 2]

    def test_matching_row_indices_limit(self):
        df = pd.DataFrame({"a": [2, 2, 2]})
        assert T.matching_row_indices(df, {"a": 2}, limit=2) == [0, 1]


# --------------------------------------------------------------------------
# Dedup / sort
# --------------------------------------------------------------------------


class TestDedupSort:
    def test_drop_duplicate_rows_reset_index(self):
        df = pd.DataFrame({"a": [1, 1, 2]})
        out = T.drop_duplicate_rows_reset_index(df, subset=["a"])
        assert out["a"].tolist() == [1, 2]
        assert list(out.index) == [0, 1]

    def test_drop_duplicate_columns(self):
        df = pd.DataFrame([[1, 2, 3]], columns=["a", "a", "b"])
        out = T.drop_duplicate_columns(df)
        assert list(out.columns) == ["a", "b"]

    def test_sort_rows_reset_index(self):
        df = pd.DataFrame({"a": [3, 1, 2]})
        out = T.sort_rows_reset_index(df, by="a")
        assert out["a"].tolist() == [1, 2, 3]

    def test_drop_rows_reset_index(self):
        df = pd.DataFrame({"a": [10, 20, 30]})
        out = T.drop_rows_reset_index(df, [1])
        assert out["a"].tolist() == [10, 30]
        assert list(out.index) == [0, 1]


# --------------------------------------------------------------------------
# Group operations
# --------------------------------------------------------------------------


class TestGroupOps:
    def _df(self):
        return pd.DataFrame({"g": ["a", "a", "b"], "v": [1, 2, 3]})

    def test_group_size_reset(self):
        out = T.group_size_reset(self._df(), ["g"])
        assert dict(zip(out["g"], out["count"])) == {"a": 2, "b": 1}

    def test_group_aggregate_reset(self):
        out = T.group_aggregate_reset(self._df(), ["g"], {"v": "sum"})
        assert dict(zip(out["g"], out["v"])) == {"a": 3, "b": 3}

    def test_group_aggregate_as_columns(self):
        out = T.group_aggregate_as_columns(self._df(), ["g"], {"v": "max"})
        assert dict(zip(out["g"], out["v"])) == {"a": 2, "b": 3}

    def test_group_keys_dataframe(self):
        out = T.group_keys_dataframe(self._df(), ["g"])
        assert sorted(out["g"].tolist()) == ["a", "b"]
        assert "size" not in out.columns

    def test_group_size_values(self):
        vals = T.group_size_values(self._df(), ["g"])
        assert sorted(vals.tolist()) == [1, 2]

    def test_iter_grouped_dataframes(self):
        groups = dict(T.iter_grouped_dataframes(self._df(), "g"))
        assert set(groups.keys()) == {"a", "b"}
        assert len(groups["a"]) == 2

    def test_group_named_aggregate_merge(self):
        out = T.group_named_aggregate_merge(
            self._df(), "g", {"v_sum": ("v", "sum")}
        )
        assert "v_sum" in out.columns
        assert len(out) == 3

    def test_group_cumcount_series(self):
        out = T.group_cumcount_series(self._df(), "g")
        assert out.tolist() == [1, 2, 1]


# --------------------------------------------------------------------------
# Window functions
# --------------------------------------------------------------------------


class TestWindowFunctions:
    def _df(self):
        return pd.DataFrame({"g": ["a", "a", "b"], "v": [10, 20, 30]})

    def test_rank_no_group(self):
        out = T.window_function_values(self._df(), "v", "rank")
        assert out.tolist() == [1.0, 2.0, 3.0]

    def test_dense_rank_no_group(self):
        out = T.window_function_values(pd.DataFrame({"v": [5, 5, 9]}), "v", "dense_rank")
        assert out.tolist() == [1.0, 1.0, 2.0]

    def test_cumsum_no_group(self):
        out = T.window_function_values(self._df(), "v", "cumsum")
        assert out.tolist() == [10, 30, 60]

    def test_cumcount_no_group(self):
        out = T.window_function_values(self._df(), "v", "cumcount")
        assert list(out) == [0, 1, 2]

    def test_lag_no_group(self):
        out = T.window_function_values(self._df(), "v", "lag")
        assert out.iloc[1] == 10

    def test_lead_no_group(self):
        out = T.window_function_values(self._df(), "v", "lead")
        assert out.iloc[0] == 20

    def test_pct_change_no_group(self):
        out = T.window_function_values(self._df(), "v", "pct_change")
        assert out.iloc[1] == pytest.approx(1.0)

    def test_running_mean_no_group(self):
        out = T.window_function_values(self._df(), "v", "running_mean")
        assert out.iloc[1] == pytest.approx(15.0)

    def test_grouped_cumsum(self):
        out = T.window_function_values(self._df(), "v", "cumsum", group_by="g")
        assert out.tolist() == [10, 30, 30]

    def test_grouped_rank(self):
        out = T.window_function_values(self._df(), "v", "rank", group_by="g")
        assert out.tolist() == [1.0, 2.0, 1.0]

    def test_grouped_cumcount(self):
        out = T.window_function_values(self._df(), "v", "cumcount", group_by="g")
        assert out.tolist() == [0, 1, 0]

    def test_grouped_lag(self):
        out = T.window_function_values(self._df(), "v", "lag", group_by="g")
        assert pd.isna(out.iloc[0]) and out.iloc[1] == 10

    def test_grouped_running_mean(self):
        out = T.window_function_values(self._df(), "v", "running_mean", group_by="g")
        assert out.iloc[1] == pytest.approx(15.0)

    def test_unsupported_raises(self):
        with pytest.raises(ValueError, match="Unsupported window function"):
            T.window_function_values(self._df(), "v", "bogus")


# --------------------------------------------------------------------------
# Series stats / merge estimation
# --------------------------------------------------------------------------


class TestSeriesStats:
    def test_series_null_ratio(self):
        s = pd.Series([1, np.nan, np.nan, 4])
        assert T.series_null_ratio(s) == 0.5

    def test_series_null_ratio_empty(self):
        assert T.series_null_ratio(pd.Series([], dtype=float)) == 0.0

    def test_non_null_head_values(self):
        s = pd.Series([np.nan, 1, 2, 3])
        assert T.non_null_head_values(s, 2) == [1.0, 2.0]


class TestEstimateMergeOutputRows:
    def test_inner(self):
        left = pd.DataFrame({"k": [1, 1, 2]})
        right = pd.DataFrame({"k": [1, 2, 2]})
        # k=1: 2*1=2 ; k=2: 1*2=2 -> 4
        assert T.estimate_merge_output_rows(left, right, ["k"], "inner") == 4

    def test_left(self):
        left = pd.DataFrame({"k": [1, 3]})
        right = pd.DataFrame({"k": [1]})
        # matched(1)=1 + unmatched left(3)=1 -> 2
        assert T.estimate_merge_output_rows(left, right, ["k"], "left") == 2

    def test_right(self):
        left = pd.DataFrame({"k": [1]})
        right = pd.DataFrame({"k": [1, 5]})
        assert T.estimate_merge_output_rows(left, right, ["k"], "right") == 2

    def test_outer(self):
        left = pd.DataFrame({"k": [1, 3]})
        right = pd.DataFrame({"k": [1, 5]})
        # matched=1 + left unmatched(3)=1 + right unmatched(5)=1 -> 3
        assert T.estimate_merge_output_rows(left, right, ["k"], "outer") == 3

    def test_no_matches(self):
        left = pd.DataFrame({"k": [1]})
        right = pd.DataFrame({"k": [2]})
        assert T.estimate_merge_output_rows(left, right, ["k"], "inner") == 0

    def test_unsupported_how_returns_none(self):
        left = pd.DataFrame({"k": [1]})
        right = pd.DataFrame({"k": [1]})
        assert T.estimate_merge_output_rows(left, right, ["k"], "cross") is None

    def test_empty_keys_returns_none(self):
        left = pd.DataFrame({"k": [1]})
        right = pd.DataFrame({"k": [1]})
        assert T.estimate_merge_output_rows(left, right, [], "inner") is None


# --------------------------------------------------------------------------
# Mixed columns / stringify / geometry
# --------------------------------------------------------------------------


class TestMixedAndStringify:
    def test_mixed_numeric_string_object_columns(self):
        df = pd.DataFrame({"mixed": [1, "two", 3], "clean": ["a", "b", "c"]})
        out = T.mixed_numeric_string_object_columns(df)
        assert out == ["mixed"]

    def test_mixed_skips_non_object(self):
        df = pd.DataFrame({"n": [1, 2, 3]})
        assert T.mixed_numeric_string_object_columns(df) == []

    def test_stringify_preserves_nulls(self):
        df = pd.DataFrame({"a": [1, np.nan, 3]})
        out = T.stringify_columns_preserving_nulls(df, ["a"])
        assert out["a"].iloc[0] == "1.0"
        assert out["a"].iloc[1] is None

    def test_geometry_series_columns_to_wkt(self):
        # is_geometry_series only recognizes a real GeoSeries, not a plain
        # object Series of shapely objects, so build a GeoDataFrame.
        gdf = gpd.GeoDataFrame(
            {"x": [1, 2]},
            geometry=gpd.GeoSeries([Point(0, 0), Point(1, 1)]),
        )
        out = T.geometry_series_columns_to_wkt(gdf)
        assert out["geometry"].iloc[0] == "POINT (0 0)"
        assert out["x"].tolist() == [1, 2]

    def test_geometry_no_geom_column_untouched(self):
        df = pd.DataFrame({"x": [1, 2]})
        out = T.geometry_series_columns_to_wkt(df)
        assert out["x"].tolist() == [1, 2]


# --------------------------------------------------------------------------
# Select / append / concat
# --------------------------------------------------------------------------


class TestSelectAppendConcat:
    def test_select_and_rename_columns(self):
        df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
        out = T.select_and_rename_columns(df, ["a", "b", "missing"], rename_map={"a": "A"})
        assert list(out.columns) == ["A", "b"]

    def test_select_without_rename(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        out = T.select_and_rename_columns(df, ["b"])
        assert list(out.columns) == ["b"]

    def test_append_record_rows(self):
        df = pd.DataFrame({"a": [1]})
        out = T.append_record_rows(df, [{"a": 2}])
        assert out["a"].tolist() == [1, 2]

    def test_append_empty_rows_returns_same(self):
        df = pd.DataFrame({"a": [1]})
        assert T.append_record_rows(df, []) is df

    def test_concat_dataframes(self):
        out = T.concat_dataframes(
            [pd.DataFrame({"a": [1]}), pd.DataFrame({"a": [2]})],
            ignore_index=True,
        )
        assert out["a"].tolist() == [1, 2]


# --------------------------------------------------------------------------
# Null row indices
# --------------------------------------------------------------------------


class TestNullRowIndices:
    def test_empty_columns_returns_empty(self):
        df = pd.DataFrame({"a": [1]})
        assert T.null_row_indices_for_columns(df, []) == []

    def test_detects_nan_and_empty_string(self):
        df = pd.DataFrame({"a": [1, np.nan, 3], "b": ["x", "y", ""]})
        out = T.null_row_indices_for_columns(df, ["a", "b"])
        assert out == [1, 2]


# --------------------------------------------------------------------------
# Frame / series builders
# --------------------------------------------------------------------------


class TestBuilders:
    def test_dataframe_from_records(self):
        out = T.dataframe_from_records([{"a": 1}, {"a": 2}])
        assert out["a"].tolist() == [1, 2]

    def test_dataframe_from_records_empty(self):
        assert T.dataframe_from_records([]).empty

    def test_empty_dataframe(self):
        assert T.empty_dataframe().empty

    def test_dataframe_from_rows(self):
        out = T.dataframe_from_rows([[1, 2]], ["a", "b"])
        assert out.iloc[0].tolist() == [1, 2]

    def test_dataframe_from_mapping(self):
        out = T.dataframe_from_mapping({"a": [1, 2]})
        assert out["a"].tolist() == [1, 2]

    def test_empty_object_series(self):
        s = T.empty_object_series()
        assert s.dtype == object and len(s) == 0

    def test_false_boolean_series(self):
        s = T.false_boolean_series([0, 1, 2])
        assert (~s).all() and len(s) == 3

    def test_object_series(self):
        s = T.object_series([1, "a"])
        assert s.dtype == object

    def test_constant_object_series(self):
        s = T.constant_object_series("z", [0, 1])
        assert s.tolist() == ["z", "z"]

    def test_missing_scalar(self):
        assert T.missing_scalar() is pd.NA

    def test_dataframe_eval_namespace(self):
        ns = T.dataframe_eval_namespace()
        assert ns["pd"] is pd


# --------------------------------------------------------------------------
# String ops
# --------------------------------------------------------------------------


class TestStringOps:
    def test_trim(self):
        s = pd.Series(["  a  ", "b"])
        out = T.apply_string_ops_to_string_subset(s, "trim")
        assert out.tolist() == ["a", "b"]

    def test_lower(self):
        s = pd.Series(["AB", "Cd"])
        out = T.apply_string_ops_to_string_subset(s, "lower")
        assert out.tolist() == ["ab", "cd"]

    def test_upper(self):
        out = T.apply_string_ops_to_string_subset(pd.Series(["ab"]), "upper")
        assert out.tolist() == ["AB"]

    def test_title(self):
        out = T.apply_string_ops_to_string_subset(pd.Series(["hello world"]), "title")
        assert out.tolist() == ["Hello World"]

    def test_strip_double_spaces(self):
        out = T.apply_string_ops_to_string_subset(pd.Series(["a   b"]), "strip_double_spaces")
        assert out.tolist() == ["a b"]

    def test_full_combines(self):
        out = T.apply_string_ops_to_string_subset(pd.Series(["  HeLLo   World  "]), "full")
        assert out.tolist() == ["hello world"]

    def test_non_string_values_preserved(self):
        s = pd.Series([1, "AB"], dtype=object)
        out = T.apply_string_ops_to_string_subset(s, "lower")
        assert out.iloc[0] == 1 and out.iloc[1] == "ab"

    def test_no_strings_returns_copy(self):
        s = pd.Series([1, 2])
        out = T.apply_string_ops_to_string_subset(s, "upper")
        assert out.tolist() == [1, 2]
        assert out is not s


# --------------------------------------------------------------------------
# dtype predicates
# --------------------------------------------------------------------------


class TestDtypePredicates:
    def test_is_dataframe(self):
        assert T.is_dataframe(pd.DataFrame())
        assert not T.is_dataframe([1, 2])

    def test_is_numeric_series(self):
        assert T.is_numeric_series(pd.Series([1, 2]))
        assert not T.is_numeric_series(pd.Series(["a"]))

    def test_is_bool_series(self):
        assert T.is_bool_series(pd.Series([True, False]))
        assert not T.is_bool_series(pd.Series([1, 2]))

    def test_is_float_series(self):
        assert T.is_float_series(pd.Series([1.0, 2.0]))
        assert not T.is_float_series(pd.Series([1, 2]))

    def test_float_series_non_null_whole_numbers(self):
        assert T.float_series_non_null_whole_numbers(pd.Series([1.0, 2.0, np.nan]))
        assert not T.float_series_non_null_whole_numbers(pd.Series([1.5]))

    def test_is_multiindex(self):
        mi = pd.MultiIndex.from_tuples([(1, 2)])
        assert T.is_multiindex(mi)
        assert not T.is_multiindex(pd.Index([1, 2]))


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------


class TestHashing:
    def test_hash_stable_for_equal_frames(self):
        df1 = pd.DataFrame({"a": [1, 2]})
        df2 = pd.DataFrame({"a": [1, 2]})
        assert T.dataframe_hash_bytes(df1) == T.dataframe_hash_bytes(df2)

    def test_hash_differs_for_different_frames(self):
        df1 = pd.DataFrame({"a": [1, 2]})
        df2 = pd.DataFrame({"a": [1, 3]})
        assert T.dataframe_hash_bytes(df1) != T.dataframe_hash_bytes(df2)


# --------------------------------------------------------------------------
# Coercion / binning / normalize / merge / pivot / melt
# --------------------------------------------------------------------------


class TestCoercionAndReshape:
    def test_coerce_datetime_series(self):
        out = T.coerce_datetime_series(pd.Series(["2020-01-01", "bad"]))
        assert out.iloc[0] == pd.Timestamp("2020-01-01")
        assert pd.isna(out.iloc[1])

    def test_coerce_datetime_dayfirst(self):
        out = T.coerce_datetime_series(pd.Series(["01-02-2020"]), dayfirst=True)
        assert out.iloc[0] == pd.Timestamp("2020-02-01")

    def test_coerce_numeric_series(self):
        out = T.coerce_numeric_series(pd.Series(["1", "x", "3"]))
        assert out.iloc[0] == 1 and pd.isna(out.iloc[1]) and out.iloc[2] == 3

    def test_cut_numeric_series(self):
        out = T.cut_numeric_series([1, 5, 9], bins=[0, 4, 10], labels=["lo", "hi"])
        assert list(out) == ["lo", "hi", "hi"]

    def test_normalize_json_records(self):
        out = T.normalize_json_records([{"a": {"b": 1}}])
        assert "a.b" in out.columns

    def test_merge_dataframes(self):
        left = pd.DataFrame({"k": [1, 2], "l": ["a", "b"]})
        right = pd.DataFrame({"k": [1], "r": ["x"]})
        out = T.merge_dataframes(left, right, on="k", how="inner")
        assert out.shape == (1, 3)

    def test_pivot_table_dataframe(self):
        df = pd.DataFrame({"g": ["a", "a", "b"], "v": [1, 3, 5]})
        out = T.pivot_table_dataframe(df, index="g", values="v", aggfunc="sum")
        assert out.loc["a", "v"] == 4

    def test_melt_dataframe(self):
        df = pd.DataFrame({"id": [1], "x": [10], "y": [20]})
        out = T.melt_dataframe(df, id_vars=["id"], value_vars=["x", "y"])
        assert set(out["variable"]) == {"x", "y"}


# --------------------------------------------------------------------------
# Join columns / concat strings
# --------------------------------------------------------------------------


class TestJoinColumns:
    def test_join_columns_as_strings(self):
        df = pd.DataFrame({"a": [1, 2], "b": ["x", None]})
        out = T.join_columns_as_strings(df, "-")
        assert out.tolist() == ["1-x", "2-"]

    def test_join_columns_empty(self):
        df = pd.DataFrame({"a": []})
        out = T.join_columns_as_strings(df, "-")
        assert len(out) == 0 and out.dtype == object

    def test_concat_non_null_as_strings(self):
        s = pd.Series([1, np.nan, 3])
        assert T.concat_non_null_as_strings(s) == "1.0, 3.0"

    def test_concat_custom_separator(self):
        s = pd.Series(["a", "b"])
        assert T.concat_non_null_as_strings(s, "|") == "a|b"


# --------------------------------------------------------------------------
# Table diff
# --------------------------------------------------------------------------


class TestTableDiff:
    def test_added_deleted_modified(self):
        old_df = pd.DataFrame({"id": [1, 2, 3], "val": [10, 20, 30]})
        new_df = pd.DataFrame({"id": [1, 2, 4], "val": [10, 25, 40]})
        result, counts = T.table_diff_dataframe(
            old_df,
            new_df,
            key_cols=["id"],
            common_value_cols=["val"],
            only_old_cols=[],
            only_new_cols=[],
        )
        status = dict(zip(result["id"], result["_diff_status"]))
        assert status[2] == "MODIFIED"
        assert status[3] == "DELETED"
        assert status[4] == "ADDED"
        # id=1 unchanged -> excluded by default
        assert 1 not in status
        assert counts.get("MODIFIED") == 1

    def test_modified_records_changed_columns_and_old_values(self):
        old_df = pd.DataFrame({"id": [1], "val": [10]})
        new_df = pd.DataFrame({"id": [1], "val": [99]})
        result, _ = T.table_diff_dataframe(
            old_df,
            new_df,
            key_cols=["id"],
            common_value_cols=["val"],
            only_old_cols=[],
            only_new_cols=[],
        )
        row = result.iloc[0]
        assert row["_diff_status"] == "MODIFIED"
        assert row["_diff_columns"] == "val"
        assert row["_diff_old_values"] == "10"
        assert row["val"] == 99

    def test_include_unchanged_yes(self):
        old_df = pd.DataFrame({"id": [1], "val": [10]})
        new_df = pd.DataFrame({"id": [1], "val": [10]})
        result, counts = T.table_diff_dataframe(
            old_df,
            new_df,
            key_cols=["id"],
            common_value_cols=["val"],
            only_old_cols=[],
            only_new_cols=[],
            include_unchanged="yes",
        )
        assert result.iloc[0]["_diff_status"] == "UNCHANGED"
        assert counts.get("UNCHANGED") == 1

    def test_empty_result_when_no_changes_excluded(self):
        old_df = pd.DataFrame({"id": [1], "val": [10]})
        new_df = pd.DataFrame({"id": [1], "val": [10]})
        result, counts = T.table_diff_dataframe(
            old_df,
            new_df,
            key_cols=["id"],
            common_value_cols=["val"],
            only_old_cols=[],
            only_new_cols=[],
        )
        assert len(result) == 0
        assert counts == {}
        assert "_diff_status" in result.columns

    def test_deleted_keeps_old_values(self):
        old_df = pd.DataFrame({"id": [1], "val": [10]})
        new_df = pd.DataFrame({"id": [99], "val": [20]})
        result, _ = T.table_diff_dataframe(
            old_df,
            new_df,
            key_cols=["id"],
            common_value_cols=["val"],
            only_old_cols=[],
            only_new_cols=[],
        )
        deleted_row = result[result["_diff_status"] == "DELETED"].iloc[0]
        assert deleted_row["val"] == 10
