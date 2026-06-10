"""
Behavioral tests — app/core/manipola/transforms/utility.py

Covers the three public per-row utility transforms:
  - op_add_row_number
  - op_date_extract
  - op_uuid_generator

These are pure DataFrame -> DataFrame operations (no DB/HTTP), so they are
exercised directly with small inline pandas DataFrames.
"""

from __future__ import annotations

import re

import pandas as pd
import pytest

from app.core.manipola.transforms.utility import (
    op_add_row_number,
    op_date_extract,
    op_uuid_generator,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.manipola,
]


# ---------------------------------------------------------------------------
# op_add_row_number
# ---------------------------------------------------------------------------


def test_add_row_number_global_default_start():
    df = pd.DataFrame({"a": [10, 20, 30]})
    out = op_add_row_number(df)

    assert "row_number" in out.columns
    assert list(out["row_number"]) == [1, 2, 3]
    # original frame untouched (copy-on-update)
    assert "row_number" not in df.columns


def test_add_row_number_custom_output_and_start():
    df = pd.DataFrame({"a": [1, 2, 3, 4]})
    out = op_add_row_number(df, output_column="rn", start=100)

    assert list(out["rn"]) == [100, 101, 102, 103]


def test_add_row_number_with_order_column_sorts_first():
    # Unsorted values; ordering by 'a' ascending should number in sorted order.
    df = pd.DataFrame({"a": [30, 10, 20], "label": ["c", "a", "b"]})
    out = op_add_row_number(df, order_column="a", ascending=True)

    # After sorting by 'a' the labels are a,b,c and numbering follows the sort.
    pairs = list(zip(out["label"], out["row_number"]))
    assert pairs == [("a", 1), ("b", 2), ("c", 3)]


def test_add_row_number_with_order_column_descending():
    df = pd.DataFrame({"a": [30, 10, 20], "label": ["c", "a", "b"]})
    out = op_add_row_number(df, order_column="a", ascending=False)

    pairs = list(zip(out["label"], out["row_number"]))
    assert pairs == [("c", 1), ("b", 2), ("a", 3)]


def test_add_row_number_ignores_missing_order_column():
    # order_column not present -> branch skipped, global numbering on original order.
    df = pd.DataFrame({"a": [30, 10, 20]})
    out = op_add_row_number(df, order_column="does_not_exist")

    assert list(out["row_number"]) == [1, 2, 3]
    assert list(out["a"]) == [30, 10, 20]


def test_add_row_number_partitioned_cumcount():
    df = pd.DataFrame({"grp": ["x", "x", "y", "x", "y"]})
    out = op_add_row_number(df, partition_column="grp", start=1)

    # cumcount within group + start(=1): x->1,2,3 ; y->1,2 by row order
    assert list(out["row_number"]) == [1, 2, 1, 3, 2]


def test_add_row_number_partitioned_custom_start():
    df = pd.DataFrame({"grp": ["a", "a", "b"]})
    out = op_add_row_number(df, partition_column="grp", start=5)

    assert list(out["row_number"]) == [5, 6, 5]


def test_add_row_number_missing_partition_column_falls_back_to_global():
    df = pd.DataFrame({"a": [1, 2, 3]})
    out = op_add_row_number(df, partition_column="nope")

    assert list(out["row_number"]) == [1, 2, 3]


def test_add_row_number_empty_dataframe():
    df = pd.DataFrame({"a": []})
    out = op_add_row_number(df)

    assert "row_number" in out.columns
    assert len(out) == 0


# ---------------------------------------------------------------------------
# op_date_extract
# ---------------------------------------------------------------------------


def test_date_extract_default_part_is_year():
    df = pd.DataFrame({"ts": ["2021-03-15", "2022-11-01"]})
    out = op_date_extract(df, column="ts")

    # Default prefix "{column}_" and default parts ["year"]
    assert "ts_year" in out.columns
    assert list(out["ts_year"]) == [2021, 2022]


def test_date_extract_multiple_parts():
    df = pd.DataFrame({"ts": ["2021-03-15 08:09:10"]})
    out = op_date_extract(
        df,
        column="ts",
        parts=["year", "month", "day", "hour", "minute", "second"],
    )

    assert out["ts_year"].iloc[0] == 2021
    assert out["ts_month"].iloc[0] == 3
    assert out["ts_day"].iloc[0] == 15
    assert out["ts_hour"].iloc[0] == 8
    assert out["ts_minute"].iloc[0] == 9
    assert out["ts_second"].iloc[0] == 10


def test_date_extract_quarter_weekday_week():
    # 2021-03-15 is a Monday (weekday 0), Q1, ISO week 11.
    df = pd.DataFrame({"ts": ["2021-03-15"]})
    out = op_date_extract(df, column="ts", parts=["quarter", "weekday", "week"])

    assert out["ts_quarter"].iloc[0] == 1
    assert out["ts_weekday"].iloc[0] == 0
    assert out["ts_week"].iloc[0] == 11


def test_date_extract_custom_prefix():
    df = pd.DataFrame({"ts": ["2021-03-15"]})
    out = op_date_extract(df, column="ts", parts=["year"], prefix="d_")

    assert "d_year" in out.columns
    assert "ts_year" not in out.columns


def test_date_extract_parts_case_insensitive_and_stripped():
    df = pd.DataFrame({"ts": ["2021-03-15"]})
    out = op_date_extract(df, column="ts", parts=["  YEAR  ", "Month"])

    assert "ts_year" in out.columns
    assert "ts_month" in out.columns
    assert out["ts_year"].iloc[0] == 2021
    assert out["ts_month"].iloc[0] == 3


def test_date_extract_unsupported_part_is_skipped():
    df = pd.DataFrame({"ts": ["2021-03-15"]})
    out = op_date_extract(df, column="ts", parts=["year", "millennium"])

    assert "ts_year" in out.columns
    # unsupported part produces no column
    assert not any(c.startswith("ts_millennium") for c in out.columns)


def test_date_extract_missing_column_returns_df_unchanged():
    df = pd.DataFrame({"ts": ["2021-03-15"]})
    out = op_date_extract(df, column="absent")

    # Returns the same object (warning branch), no new columns added.
    assert out is df
    assert list(out.columns) == ["ts"]


def test_date_extract_invalid_values_coerced_to_nat():
    df = pd.DataFrame({"ts": ["2021-03-15", "not-a-date"]})
    out = op_date_extract(df, column="ts", parts=["year"])

    # First row valid, second coerced to NaT -> NaN year.
    years = out["ts_year"]
    assert years.iloc[0] == 2021
    assert pd.isna(years.iloc[1])


def test_date_extract_does_not_mutate_input():
    df = pd.DataFrame({"ts": ["2021-03-15"]})
    op_date_extract(df, column="ts")

    assert list(df.columns) == ["ts"]


# ---------------------------------------------------------------------------
# op_uuid_generator
# ---------------------------------------------------------------------------


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def test_uuid_generator_adds_column_with_valid_uuids():
    df = pd.DataFrame({"a": [1, 2, 3]})
    out = op_uuid_generator(df)

    assert "uuid" in out.columns
    assert len(out) == 3
    for val in out["uuid"]:
        assert _UUID_RE.match(val), f"not a v4 uuid: {val}"


def test_uuid_generator_values_are_unique():
    df = pd.DataFrame({"a": list(range(50))})
    out = op_uuid_generator(df)

    assert out["uuid"].nunique() == 50


def test_uuid_generator_custom_output_column():
    df = pd.DataFrame({"a": [1]})
    out = op_uuid_generator(df, output_column="pk")

    assert "pk" in out.columns
    assert _UUID_RE.match(out["pk"].iloc[0])


def test_uuid_generator_empty_dataframe_returns_unchanged():
    df = pd.DataFrame({"a": []})
    out = op_uuid_generator(df)

    # Early-return branch for empty frame: same object, no uuid column.
    assert out is df
    assert "uuid" not in out.columns


def test_uuid_generator_does_not_mutate_input():
    df = pd.DataFrame({"a": [1, 2]})
    op_uuid_generator(df)

    assert "uuid" not in df.columns
