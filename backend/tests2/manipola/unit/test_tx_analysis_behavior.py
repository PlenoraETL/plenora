"""
Behavioral coverage for app.core.manipola.transforms.analysis

Exercises the happy-path + significant branches of every public op_*:
op_sample, op_lookup, op_bin, op_flatten_json, op_statistics.

No production code touched — pure unit tests with small inline DataFrames.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.manipola.transforms.analysis import (
    op_bin,
    op_flatten_json,
    op_lookup,
    op_sample,
    op_statistics,
)


pytestmark = [pytest.mark.unit, pytest.mark.manipola]


# op_sample


def test_sample_empty_df_keeps_schema():
    # A3: niente scorciatoia sul vuoto, l'esecuzione a vuoto esercita i parametri.
    df = pd.DataFrame({"a": []})
    result = op_sample(df, n=10)
    assert len(result) == 0
    assert list(result.columns) == ["a"]


def test_sample_by_n_caps_to_len():
    df = pd.DataFrame({"a": range(5)})
    result = op_sample(df, n=100, random_state=1)
    # n is capped at len(df) -> all 5 rows returned
    assert len(result) == 5


def test_sample_by_n_subset_deterministic_with_seed():
    df = pd.DataFrame({"a": range(20)})
    r1 = op_sample(df, n=5, random_state=42)
    r2 = op_sample(df, n=5, random_state=42)
    assert len(r1) == 5
    # Same seed -> same rows
    assert list(r1["a"]) == list(r2["a"])


def test_sample_by_fraction_takes_priority_over_n():
    df = pd.DataFrame({"a": range(100)})
    result = op_sample(df, n=99, fraction=0.1, random_state=7)
    # 10% of 100 rows
    assert len(result) == 10


def test_sample_fraction_clamped_to_one():
    df = pd.DataFrame({"a": range(10)})
    result = op_sample(df, fraction=5.0, random_state=0)
    # fraction clamped to 1.0 -> all rows
    assert len(result) == 10


def test_sample_does_not_mutate_input():
    df = pd.DataFrame({"a": range(10)})
    before = df.copy()
    op_sample(df, n=3, random_state=1)
    pd.testing.assert_frame_equal(df, before)


def test_sample_stratified_keeps_at_least_one_per_group():
    # 6 rows in group X, 2 rows in group Y
    df = pd.DataFrame(
        {
            "g": ["X"] * 6 + ["Y"] * 2,
            "v": range(8),
        }
    )
    result = op_sample(df, n=2, random_state=3, stratify_column="g")
    groups = set(result["g"])
    # Proportional sampling guarantees >=1 from each group present
    assert "X" in groups
    assert "Y" in groups


def test_sample_stratified_with_fraction():
    df = pd.DataFrame(
        {
            "g": ["A"] * 10 + ["B"] * 10,
            "v": range(20),
        }
    )
    result = op_sample(df, fraction=0.5, random_state=5, stratify_column="g")
    # ~50% of each group of 10 -> ~5 each -> 10 total
    counts = result["g"].value_counts()
    assert counts.get("A", 0) == 5
    assert counts.get("B", 0) == 5


def test_sample_stratify_column_missing_falls_back_to_plain_sample():
    df = pd.DataFrame({"a": range(10)})
    result = op_sample(df, n=4, random_state=1, stratify_column="nope")
    assert len(result) == 4


# op_lookup


def test_lookup_missing_column_returns_input_unchanged():
    df = pd.DataFrame({"a": [1, 2]})
    result = op_lookup(df, column="ghost", mapping={1: "x"})
    assert result is df


def test_lookup_empty_mapping_returns_input_unchanged():
    df = pd.DataFrame({"a": [1, 2]})
    result = op_lookup(df, column="a", mapping={})
    assert result is df


def test_lookup_maps_and_keeps_original_for_unmatched():
    df = pd.DataFrame({"code": ["a", "b", "z"]})
    result = op_lookup(df, column="code", mapping={"a": "Alpha", "b": "Beta"})
    # 'z' unmatched and default=None -> keep original
    assert list(result["code"]) == ["Alpha", "Beta", "z"]


def test_lookup_default_used_for_unmatched():
    df = pd.DataFrame({"code": ["a", "z"]})
    result = op_lookup(df, column="code", mapping={"a": "Alpha"}, default="UNKNOWN")
    assert list(result["code"]) == ["Alpha", "UNKNOWN"]


def test_lookup_output_column_preserves_source():
    df = pd.DataFrame({"code": ["a", "b"]})
    result = op_lookup(
        df, column="code", mapping={"a": "A", "b": "B"}, output_column="label"
    )
    assert list(result["label"]) == ["A", "B"]
    # Source column untouched
    assert list(result["code"]) == ["a", "b"]


def test_lookup_does_not_mutate_input():
    df = pd.DataFrame({"code": ["a", "b"]})
    before = df.copy()
    op_lookup(df, column="code", mapping={"a": "A"})
    pd.testing.assert_frame_equal(df, before)


# op_bin


def test_bin_missing_column_returns_input_unchanged():
    df = pd.DataFrame({"a": [1, 2]})
    result = op_bin(df, column="ghost", bins=3)
    assert result is df


def test_bin_int_bins_creates_default_named_output():
    df = pd.DataFrame({"score": [0, 25, 50, 75, 100]})
    result = op_bin(df, column="score", bins=5)
    assert "score_bin" in result.columns
    # All values assigned to some bin (no NaN with include_lowest)
    assert result["score_bin"].notna().all()
    # Categorical with 5 categories
    assert len(result["score_bin"].cat.categories) == 5


def test_bin_with_edges_and_labels():
    df = pd.DataFrame({"v": [5, 15, 55, 95]})
    result = op_bin(
        df,
        column="v",
        bins=[0, 10, 50, 100],
        labels=["low", "mid", "high"],
        output_column="band",
    )
    assert list(result["band"]) == ["low", "mid", "high", "high"]


def test_bin_does_not_mutate_input():
    df = pd.DataFrame({"v": [1, 2, 3]})
    before = df.copy()
    op_bin(df, column="v", bins=2)
    pd.testing.assert_frame_equal(df, before)


# op_flatten_json


def test_flatten_missing_column_returns_input_unchanged():
    df = pd.DataFrame({"a": [1]})
    result = op_flatten_json(df, column="ghost")
    assert result is df


def test_flatten_dict_column_full_path():
    df = pd.DataFrame({"meta": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]})
    result = op_flatten_json(df, column="meta")
    assert "meta_x" in result.columns
    assert "meta_y" in result.columns
    assert list(result["meta_x"]) == [1, 3]
    assert list(result["meta_y"]) == [2, 4]


def test_flatten_json_string_column():
    df = pd.DataFrame({"meta": ['{"a": 10}', '{"a": 20}']})
    result = op_flatten_json(df, column="meta")
    assert "meta_a" in result.columns
    assert list(result["meta_a"]) == [10, 20]


def test_flatten_custom_prefix():
    df = pd.DataFrame({"meta": [{"k": 1}]})
    result = op_flatten_json(df, column="meta", prefix="P_")
    assert "P_k" in result.columns


def test_flatten_invalid_json_strings_yield_no_columns():
    df = pd.DataFrame({"meta": ["not json", "still not"]})
    result = op_flatten_json(df, column="meta")
    # Nothing valid -> empty flat -> input returned
    assert result is df


def test_flatten_output_columns_whitelist_fast_path():
    df = pd.DataFrame(
        {"meta": [{"a": 1, "b": 2, "c": 3}, {"a": 4, "b": 5, "c": 6}]}
    )
    result = op_flatten_json(df, column="meta", output_columns=["meta_a", "meta_c"])
    assert "meta_a" in result.columns
    assert "meta_c" in result.columns
    # Not requested -> absent
    assert "meta_b" not in result.columns
    assert list(result["meta_a"]) == [1, 4]
    assert list(result["meta_c"]) == [3, 6]


def test_flatten_output_columns_nested_path_within_max_level():
    df = pd.DataFrame({"meta": [{"outer": {"inner": 7}}]})
    result = op_flatten_json(
        df, column="meta", max_level=1, output_columns=["meta_outer.inner"]
    )
    # path depth = 2 parts, max_level=1 -> allowed (len <= max_level+1)
    assert "meta_outer.inner" in result.columns
    assert list(result["meta_outer.inner"]) == [7]


def test_flatten_output_columns_too_deep_skipped():
    df = pd.DataFrame({"meta": [{"a": {"b": {"c": 1}}}]})
    result = op_flatten_json(
        df, column="meta", max_level=1, output_columns=["meta_a.b.c"]
    )
    # depth 3 > max_level(1)+1 -> skipped -> no extractable -> input returned
    assert result is df


def test_flatten_output_columns_no_prefix_match_returns_input():
    df = pd.DataFrame({"meta": [{"a": 1}]})
    result = op_flatten_json(
        df, column="meta", output_columns=["wrongprefix_a"]
    )
    assert result is df


def test_flatten_does_not_mutate_input():
    df = pd.DataFrame({"meta": [{"x": 1}]})
    before = df.copy()
    op_flatten_json(df, column="meta")
    pd.testing.assert_frame_equal(df, before)


# op_statistics


def test_statistics_missing_column_returns_input_unchanged():
    df = pd.DataFrame({"a": [1]})
    result = op_statistics(df, column="ghost")
    assert result is df


def test_statistics_global_default_stats_as_constant_columns():
    df = pd.DataFrame({"v": [1.0, 2.0, 3.0, 4.0]})
    result = op_statistics(df, column="v")
    for stat in ["count", "min", "max", "mean", "median", "std"]:
        assert f"v_{stat}" in result.columns
    # Constant across rows
    assert result["v_min"].nunique() == 1
    assert result["v_min"].iloc[0] == 1.0
    assert result["v_max"].iloc[0] == 4.0
    assert result["v_mean"].iloc[0] == 2.5
    assert result["v_count"].iloc[0] == 4


def test_statistics_custom_stats_and_quantiles():
    df = pd.DataFrame({"v": [1.0, 2.0, 3.0, 4.0]})
    result = op_statistics(df, column="v", stats=["sum", "q25", "q75", "var"])
    assert result["v_sum"].iloc[0] == 10.0
    assert result["v_q25"].iloc[0] == pytest.approx(1.75)
    assert result["v_q75"].iloc[0] == pytest.approx(3.25)
    assert result["v_var"].iloc[0] == pytest.approx(np.var([1, 2, 3, 4], ddof=1))


def test_statistics_unknown_stat_silently_ignored():
    df = pd.DataFrame({"v": [1.0, 2.0]})
    result = op_statistics(df, column="v", stats=["mean", "bogus"])
    assert "v_mean" in result.columns
    assert "v_bogus" not in result.columns


def test_statistics_output_prefix_override():
    df = pd.DataFrame({"v": [1.0, 2.0]})
    result = op_statistics(df, column="v", stats=["mean"], output_prefix="ST_")
    assert "ST_mean" in result.columns
    assert "v_mean" not in result.columns


def test_statistics_grouped_per_group_values():
    df = pd.DataFrame(
        {
            "g": ["A", "A", "B", "B"],
            "v": [10.0, 20.0, 100.0, 200.0],
        }
    )
    result = op_statistics(df, column="v", group_by="g", stats=["mean", "max"])
    assert "v_mean" in result.columns
    assert "v_max" in result.columns
    # Same row count preserved (left merge)
    assert len(result) == len(df)
    by_group = result.set_index("g")["v_mean"].groupby(level=0).first()
    assert by_group["A"] == 15.0
    assert by_group["B"] == 150.0


def test_statistics_group_by_missing_falls_back_to_global():
    df = pd.DataFrame({"v": [2.0, 4.0]})
    result = op_statistics(df, column="v", group_by="nope", stats=["mean"])
    # Falls into global branch -> constant column
    assert result["v_mean"].iloc[0] == 3.0
    assert result["v_mean"].nunique() == 1


def test_statistics_does_not_mutate_input():
    df = pd.DataFrame({"v": [1.0, 2.0, 3.0]})
    before = df.copy()
    op_statistics(df, column="v", stats=["mean"])
    pd.testing.assert_frame_equal(df, before)
