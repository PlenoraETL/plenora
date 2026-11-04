"""
Behavioral coverage for app/core/manipola/transforms/aggregation.py.

Exercises the public transform ops (op_aggregate, op_sort, op_distinct,
op_dedup_advanced, op_window_function) on small inline pandas DataFrames,
covering happy paths and the significant branches (empty/missing columns,
concat aggregation, alias rename, multi-agg flatten, window functions with
and without group_by, dedup ordering, invalid function fallthrough).
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.core.manipola.transforms.aggregation import (
    op_aggregate,
    op_dedup_advanced,
    op_distinct,
    op_sort,
    op_window_function,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.manipola,
]


# ---------------------------------------------------------------------------
# op_aggregate
# ---------------------------------------------------------------------------


def test_aggregate_no_group_by_returns_input_unchanged():
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    result = op_aggregate(df, group_by=[])
    assert result is df


def test_aggregate_group_by_all_invalid_returns_input():
    df = pd.DataFrame({"a": [1, 2]})
    result = op_aggregate(df, group_by=["nonexistent"])
    assert result is df


def test_aggregate_count_when_no_aggregations():
    df = pd.DataFrame({"g": ["x", "x", "y"], "v": [1, 2, 3]})
    result = op_aggregate(df, group_by=["g"])
    assert "count" in result.columns
    counts = dict(zip(result["g"], result["count"]))
    assert counts == {"x": 2, "y": 1}


def test_aggregate_count_keeps_nan_group_keys():
    # dropna=False: a NaN key must survive as its own group.
    df = pd.DataFrame({"g": ["x", None, "x"], "v": [1, 2, 3]})
    result = op_aggregate(df, group_by=["g"])
    # 2 groups: "x" and NaN
    assert len(result) == 2
    assert result["count"].sum() == 3


def test_aggregate_sum_and_mean_single_agg_per_column():
    df = pd.DataFrame(
        {"g": ["x", "x", "y"], "amount": [10.0, 20.0, 5.0]}
    )
    result = op_aggregate(
        df,
        group_by=["g"],
        aggregations=[{"column": "amount", "function": "sum"}],
    )
    sums = dict(zip(result["g"], result["amount"]))
    assert sums == {"x": 30.0, "y": 5.0}


def test_aggregate_avg_maps_to_mean():
    df = pd.DataFrame({"g": ["x", "x"], "v": [2.0, 4.0]})
    result = op_aggregate(
        df, group_by=["g"], aggregations=[{"column": "v", "function": "avg"}]
    )
    assert result["v"].iloc[0] == 3.0


def test_aggregate_unknown_function_passthrough_to_pandas():
    # func not in func_map -> passed raw to pandas (e.g. 'std')
    df = pd.DataFrame({"g": ["x", "x", "x"], "v": [1.0, 2.0, 3.0]})
    result = op_aggregate(
        df, group_by=["g"], aggregations=[{"column": "v", "function": "std"}]
    )
    assert pytest.approx(result["v"].iloc[0]) == pd.Series([1.0, 2.0, 3.0]).std()


def test_aggregate_skips_missing_column_aggregation():
    df = pd.DataFrame({"g": ["x", "y"], "v": [1, 2]})
    # 'missing' is skipped -> agg_dict empty -> fallback to count
    result = op_aggregate(
        df,
        group_by=["g"],
        aggregations=[{"column": "missing", "function": "sum"}],
    )
    assert "count" in result.columns


def test_aggregate_concat_default():
    df = pd.DataFrame({"g": ["x", "x", "y"], "name": ["a", "b", "c"]})
    result = op_aggregate(
        df,
        group_by=["g"],
        aggregations=[{"column": "name", "function": "concat"}],
    )
    joined = dict(zip(result["g"], result["name"]))
    assert joined["x"] == "a, b"
    assert joined["y"] == "c"


def test_aggregate_concat_custom_separator_and_distinct():
    df = pd.DataFrame({"g": ["x", "x", "x"], "name": ["a", "a", "b"]})
    result = op_aggregate(
        df,
        group_by=["g"],
        aggregations=[
            {
                "column": "name",
                "function": "concat",
                "separator": "|",
                "distinct": True,
            }
        ],
    )
    assert result["name"].iloc[0] == "a|b"


def test_aggregate_concat_skip_null_true_drops_none():
    df = pd.DataFrame({"g": ["x", "x", "x"], "name": ["a", None, "b"]})
    result = op_aggregate(
        df,
        group_by=["g"],
        aggregations=[{"column": "name", "function": "concat"}],
    )
    assert result["name"].iloc[0] == "a, b"


def test_aggregate_concat_skip_null_false_keeps_nulls():
    df = pd.DataFrame({"g": ["x", "x"], "name": ["a", None]})
    result = op_aggregate(
        df,
        group_by=["g"],
        aggregations=[
            {"column": "name", "function": "concat", "skip_null": False}
        ],
    )
    # None stringified somewhere in the join
    assert "a" in result["name"].iloc[0]


def test_aggregate_multi_agg_same_column_flattens_multiindex():
    df = pd.DataFrame({"g": ["x", "x"], "v": [10.0, 30.0]})
    result = op_aggregate(
        df,
        group_by=["g"],
        aggregations=[
            {"column": "v", "function": "sum"},
            {"column": "v", "function": "mean"},
        ],
    )
    assert "v_sum" in result.columns
    assert "v_mean" in result.columns
    assert result["v_sum"].iloc[0] == 40.0
    assert result["v_mean"].iloc[0] == 20.0


def test_aggregate_alias_single_agg_renames_output_column():
    df = pd.DataFrame({"g": ["x", "x"], "amount": [1.0, 2.0]})
    result = op_aggregate(
        df,
        group_by=["g"],
        aggregations=[
            {"column": "amount", "function": "sum", "alias": "total"}
        ],
    )
    assert "total" in result.columns
    assert "amount" not in result.columns
    assert result["total"].iloc[0] == 3.0


def test_aggregate_alias_multi_agg_renames_flat_name():
    df = pd.DataFrame({"g": ["x", "x"], "v": [10.0, 30.0]})
    result = op_aggregate(
        df,
        group_by=["g"],
        aggregations=[
            {"column": "v", "function": "sum", "alias": "total"},
            {"column": "v", "function": "mean"},
        ],
    )
    assert "total" in result.columns
    assert "v_mean" in result.columns
    assert result["total"].iloc[0] == 40.0


def test_aggregate_filters_nonexistent_group_by_columns():
    df = pd.DataFrame({"g": ["x", "x"], "v": [1, 2]})
    # 'ghost' filtered out, 'g' kept
    result = op_aggregate(
        df,
        group_by=["g", "ghost"],
        aggregations=[{"column": "v", "function": "sum"}],
    )
    assert "g" in result.columns
    assert result["v"].iloc[0] == 3


# ---------------------------------------------------------------------------
# op_sort
# ---------------------------------------------------------------------------


def test_sort_empty_columns_returns_input():
    df = pd.DataFrame({"a": [3, 1, 2]})
    assert op_sort(df, columns=[]) is df


def test_sort_all_invalid_columns_returns_input():
    df = pd.DataFrame({"a": [3, 1, 2]})
    assert op_sort(df, columns=["nope"]) is df


def test_sort_ascending_resets_index():
    df = pd.DataFrame({"a": [3, 1, 2]})
    result = op_sort(df, columns=["a"], ascending=True)
    assert result["a"].tolist() == [1, 2, 3]
    assert result.index.tolist() == [0, 1, 2]


def test_sort_descending():
    df = pd.DataFrame({"a": [1, 3, 2]})
    result = op_sort(df, columns=["a"], ascending=False)
    assert result["a"].tolist() == [3, 2, 1]


def test_sort_multi_column():
    df = pd.DataFrame({"a": [1, 1, 2], "b": [2, 1, 0]})
    result = op_sort(df, columns=["a", "b"])
    assert result["b"].tolist() == [1, 2, 0]


def test_sort_filters_invalid_keeps_valid():
    df = pd.DataFrame({"a": [3, 1, 2]})
    result = op_sort(df, columns=["ghost", "a"])
    assert result["a"].tolist() == [1, 2, 3]


# ---------------------------------------------------------------------------
# op_distinct
# ---------------------------------------------------------------------------


def test_distinct_all_columns_keep_first():
    df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
    result = op_distinct(df)
    assert len(result) == 2
    assert result.index.tolist() == [0, 1]


def test_distinct_subset_keep_first():
    df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "y", "z"]})
    result = op_distinct(df, subset=["a"], keep="first")
    assert result["b"].tolist() == ["x", "z"]


def test_distinct_subset_keep_last():
    df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "y", "z"]})
    result = op_distinct(df, subset=["a"], keep="last")
    assert result["b"].tolist() == ["y", "z"]


def test_distinct_keep_false_removes_all_duplicates():
    df = pd.DataFrame({"a": [1, 1, 2]})
    result = op_distinct(df, subset=["a"], keep="false")
    assert result["a"].tolist() == [2]


def test_distinct_invalid_subset_falls_back_to_all_columns():
    df = pd.DataFrame({"a": [1, 1, 2]})
    # subset filtered to empty -> None -> all columns considered
    result = op_distinct(df, subset=["ghost"])
    assert len(result) == 2


# ---------------------------------------------------------------------------
# op_dedup_advanced
# ---------------------------------------------------------------------------


def test_dedup_advanced_empty_keeps_schema():
    # A3: niente scorciatoia sul vuoto, l'esecuzione a vuoto esercita i parametri.
    df = pd.DataFrame({"a": []})
    result = op_dedup_advanced(df)
    assert result.empty and list(result.columns) == ["a"]


def test_dedup_advanced_order_column_picks_kept_row():
    # Sort by priority desc, keep first -> highest priority survives per key.
    df = pd.DataFrame(
        {"key": ["k", "k"], "priority": [1, 5], "val": ["low", "high"]}
    )
    result = op_dedup_advanced(
        df,
        subset=["key"],
        keep="first",
        order_column="priority",
        ascending=False,
    )
    assert len(result) == 1
    assert result["val"].iloc[0] == "high"


def test_dedup_advanced_order_ascending_keeps_smallest():
    df = pd.DataFrame(
        {"key": ["k", "k"], "priority": [1, 5], "val": ["low", "high"]}
    )
    result = op_dedup_advanced(
        df,
        subset=["key"],
        keep="first",
        order_column="priority",
        ascending=True,
    )
    assert result["val"].iloc[0] == "low"


def test_dedup_advanced_does_not_mutate_input():
    df = pd.DataFrame({"key": ["k", "k"], "priority": [5, 1]})
    original = df.copy(deep=True)
    op_dedup_advanced(df, subset=["key"], order_column="priority")
    pd.testing.assert_frame_equal(df, original)


def test_dedup_advanced_invalid_subset_uses_all_columns():
    df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
    result = op_dedup_advanced(df, subset=["ghost"])
    assert len(result) == 2


def test_dedup_advanced_missing_order_column_ignored():
    df = pd.DataFrame({"key": ["k", "k"], "val": ["a", "b"]})
    result = op_dedup_advanced(
        df, subset=["key"], order_column="nonexistent"
    )
    assert len(result) == 1


# ---------------------------------------------------------------------------
# op_window_function
# ---------------------------------------------------------------------------


def test_window_empty_returns_input():
    df = pd.DataFrame({"v": []})
    assert op_window_function(df, column="v") is df


def test_window_missing_column_returns_input():
    df = pd.DataFrame({"v": [1, 2]})
    assert op_window_function(df, column="ghost") is df


def test_window_invalid_function_returns_input():
    df = pd.DataFrame({"v": [1, 2]})
    assert op_window_function(df, column="v", function="bogus") is df


def test_window_rank_default_output_name():
    df = pd.DataFrame({"v": [10, 30, 20]})
    result = op_window_function(df, column="v", function="rank")
    assert "v_rank" in result.columns
    assert result["v_rank"].tolist() == [1.0, 3.0, 2.0]


def test_window_custom_output_column():
    df = pd.DataFrame({"v": [10, 20]})
    result = op_window_function(
        df, column="v", function="cumsum", output_column="running"
    )
    assert "running" in result.columns
    assert result["running"].tolist() == [10, 30]


def test_window_cumsum_global():
    df = pd.DataFrame({"v": [1, 2, 3]})
    result = op_window_function(df, column="v", function="cumsum")
    assert result["v_cumsum"].tolist() == [1, 3, 6]


def test_window_lag_shifts_down():
    df = pd.DataFrame({"v": [1, 2, 3]})
    result = op_window_function(df, column="v", function="lag", offset=1)
    assert result["v_lag"].tolist()[1:] == [1.0, 2.0]
    assert pd.isna(result["v_lag"].iloc[0])


def test_window_lead_shifts_up():
    df = pd.DataFrame({"v": [1, 2, 3]})
    result = op_window_function(df, column="v", function="lead", offset=1)
    assert result["v_lead"].tolist()[:2] == [2.0, 3.0]
    assert pd.isna(result["v_lead"].iloc[2])


def test_window_grouped_cumsum():
    df = pd.DataFrame({"g": ["a", "a", "b"], "v": [1, 2, 10]})
    result = op_window_function(
        df, column="v", function="cumsum", group_by="g"
    )
    assert result["v_cumsum"].tolist() == [1, 3, 10]


def test_window_grouped_rank():
    df = pd.DataFrame({"g": ["a", "a", "b"], "v": [5, 1, 9]})
    result = op_window_function(
        df, column="v", function="rank", group_by="g"
    )
    # within group a: 5->2, 1->1 ; group b: 9->1
    assert result["v_rank"].tolist() == [2.0, 1.0, 1.0]


def test_window_order_column_sorts_before_compute():
    df = pd.DataFrame({"order": [3, 1, 2], "v": [30, 10, 20]})
    result = op_window_function(
        df, column="v", function="cumsum", order_column="order"
    )
    # after sorting by order: v becomes [10,20,30] -> cumsum [10,30,60]
    assert result["v_cumsum"].tolist() == [10, 30, 60]


def test_window_pct_change():
    df = pd.DataFrame({"v": [100.0, 110.0, 121.0]})
    result = op_window_function(df, column="v", function="pct_change")
    assert pytest.approx(result["v_pct_change"].iloc[1]) == 0.1


def test_window_running_mean():
    df = pd.DataFrame({"v": [2.0, 4.0, 6.0]})
    result = op_window_function(df, column="v", function="running_mean")
    assert result["v_running_mean"].tolist() == [2.0, 3.0, 4.0]


def test_window_does_not_mutate_input():
    df = pd.DataFrame({"v": [1, 2, 3]})
    original = df.copy(deep=True)
    op_window_function(df, column="v", function="cumsum")
    pd.testing.assert_frame_equal(df, original)
