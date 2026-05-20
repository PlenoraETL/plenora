"""test_305 - MANIPOLA large-frame stress coverage for core runtime paths.

Scenario covered:
- wide and long DataFrames through formula/filter/sort/aggregate/hash/sample;
- larger joins and cross joins that should stay below resource guards;
- deterministic result checks on row counts and selected values.
"""

from __future__ import annotations

import pandas as pd

from _e2e import assert_equal, assert_true
from _manipola_runtime_full import run_silent, run_transform


def test_large_frame_stress_paths() -> None:
    rows = 5000
    data = {
        "id": list(range(rows)),
        "group": [f"g{i % 10}" for i in range(rows)],
        "qty": [i % 17 for i in range(rows)],
        "price": [float(i % 23) for i in range(rows)],
    }
    for i in range(80):
        data[f"extra_{i}"] = [(j + i) % 101 for j in range(rows)]
    df = pd.DataFrame(data)

    formula = run_transform("formula", [df], {"new_column": "total", "formula": "qty * price + extra_10"})
    assert_equal(len(formula), rows, "formula stress must preserve row count")
    assert_equal(float(formula.loc[20, "total"]), float((20 % 17) * (20 % 23) + ((20 + 10) % 101)), "formula stress must compute expected values")

    filtered = run_transform("filter", [formula], {"column": "group", "operator": "==", "value": "g3"})
    assert_equal(len(filtered), 500, "filter stress must select one tenth of rows")

    sorted_df = run_transform("sort", [formula], {"columns": ["total"], "ascending": False})
    assert_true(sorted_df["total"].iloc[0] >= sorted_df["total"].iloc[-1], "sort stress must order the full frame")

    aggregated = run_transform(
        "aggregate",
        [formula],
        {"group_by": ["group"], "aggregations": [{"column": "total", "function": "sum", "alias": "total_sum"}]},
    )
    assert_equal(len(aggregated), 10, "aggregate stress must produce one row per group")

    hashed = run_transform("md5_hash", [df], {"columns": ["id", "group", "extra_79"], "output_column": "row_hash"})
    assert_equal(len(set(hashed["row_hash"].head(100).tolist())), 100, "md5_hash stress must produce varied hashes")

    sampled = run_transform("sample", [df], {"n": 250, "random_state": 123})
    assert_equal(len(sampled), 250, "sample stress must return requested row count")

    left = pd.DataFrame({"id": list(range(2000)), "left_value": [i * 2 for i in range(2000)]})
    right = pd.DataFrame({"id": list(range(0, 2000, 2)), "right_value": [i * 3 for i in range(1000)]})
    joined = run_transform("join", [left, right], {"left_on": "id", "right_on": "id", "how": "left"})
    assert_equal(len(joined), 2000, "join stress left join must preserve left row count")
    assert_equal(joined["right_value_R"].notna().sum(), 1000, "join stress must match half the keys")

    cross = run_transform("cross_join", [left[["id"]].head(100), right[["right_value"]].head(100)], {})
    assert_equal(len(cross), 10000, "cross_join stress must materialize bounded cartesian product")


if __name__ == "__main__":
    run_silent(test_large_frame_stress_paths)
