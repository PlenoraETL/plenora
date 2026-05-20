"""test_301 - MANIPOLA missing non-spatial registry transforms execute directly.

Scenario covered:
- direct runtime execution for non-spatial transforms that were not part of
  the original registry matrix;
- exact checks for joins, concat/cross join, hashing, masking, statistics and
  transpose outputs.
"""

from __future__ import annotations

from _e2e import assert_equal, assert_true
from _manipola_runtime_full import base_frame, md5_expected, run_silent, run_transform


def test_missing_non_spatial_transforms() -> None:
    df = base_frame()
    right = df[["id", "group"]].rename(columns={"group": "segment"})

    joined = run_transform("join", [df[["id", "qty"]], right], {"left_on": "id", "right_on": "id", "how": "inner"})
    assert_equal(joined[["id", "qty_L", "segment_R"]].to_dict("records"), [
        {"id": 1, "qty_L": 2, "segment_R": "A"},
        {"id": 2, "qty_L": 3, "segment_R": "A"},
        {"id": 3, "qty_L": 5, "segment_R": "B"},
    ], "join must merge two frames with suffixed non-key columns")

    concatenated = run_transform("concat", [df.iloc[:1], df.iloc[1:]], {"ignore_index": True})
    assert_equal(concatenated["id"].tolist(), [1, 2, 3], "concat must append input frames in order")

    crossed = run_transform("cross_join", [df[["id"]].iloc[:2], right[["segment"]].iloc[:2]], {})
    assert_equal(len(crossed), 4, "cross_join must produce the cartesian product")

    hashed = run_transform("md5_hash", [df], {"columns": ["name", "email"], "output_column": "row_hash", "normalize": True})
    assert_equal(hashed.loc[0, "row_hash"], md5_expected("alice@example.test", "Alice"), "md5_hash must hash normalized selected columns in deterministic sorted-column order")

    masked = run_transform(
        "mask_data",
        [df],
        {"maskings": [{"column": "email", "mask_type": "email"}], "overwrite": False},
    )
    assert_equal(masked["email_masked"].tolist(), ["a****@example.test", "b**@example.test", "c****@example.test"], "mask_data must mask email local parts")

    stats = run_transform("statistics", [df], {"column": "qty", "group_by": "group", "stats": ["count", "sum", "mean"], "output_prefix": "qty_"})
    assert_equal(stats["qty_sum"].tolist(), [5, 5, 5], "statistics must attach grouped sums to each source row")
    assert_equal(stats["qty_count"].tolist(), [2, 2, 1], "statistics must attach grouped counts to each source row")

    transposed = run_transform("transpose", [df[["id", "qty"]].iloc[:2]], {"id_column": "id", "output_columns": ["row_1", "row_2"]})
    assert_equal(list(transposed.columns), ["id", "row_1", "row_2"], "transpose must honor explicit output column names")
    assert_true("qty" in transposed["id"].tolist(), "transpose must keep original column names in the id column")


if __name__ == "__main__":
    run_silent(test_missing_non_spatial_transforms)
