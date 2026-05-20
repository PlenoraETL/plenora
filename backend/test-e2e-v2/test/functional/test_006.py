"""test_006 - MANIPOLA registry function matrix on deterministic DataFrames.

Scenario covered:
- load the real Manipola PluginExecutor registry from the host;
- execute a broad set of non-spatial transforms with deterministic input
  DataFrames;
- verify exact output values/columns where the transform is deterministic;
- verify shape/contracts for transforms with generated values such as UUIDs.

This is intentionally host-launched: it does not run inside the backend
container, but it exercises the same transform registry/function loading code.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import types
from pathlib import Path

import pandas as pd

from _e2e import assert_equal, assert_true


BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    import psycopg2  # noqa: F401
except ModuleNotFoundError:
    psycopg2_stub = types.ModuleType("psycopg2")
    psycopg2_extras_stub = types.ModuleType("psycopg2.extras")
    psycopg2_pool_stub = types.ModuleType("psycopg2.pool")
    psycopg2_stub.extras = psycopg2_extras_stub
    psycopg2_stub.pool = psycopg2_pool_stub
    sys.modules["psycopg2"] = psycopg2_stub
    sys.modules["psycopg2.extras"] = psycopg2_extras_stub
    sys.modules["psycopg2.pool"] = psycopg2_pool_stub

from app.core.manipola.plugin_executor import get_executor  # noqa: E402


def _base_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "id": 1,
                "group": "A",
                "qty": 2,
                "price": 10.0,
                "name": "  alpha  ",
                "code": "x-001",
                "date": "2024-01-10",
                "json": '{"city":"Milano","score":10}',
            },
            {
                "id": 2,
                "group": "A",
                "qty": 3,
                "price": 20.0,
                "name": "Beta",
                "code": "x-002",
                "date": "2024-02-15",
                "json": '{"city":"Roma","score":20}',
            },
            {
                "id": 3,
                "group": "B",
                "qty": 1,
                "price": 30.0,
                "name": None,
                "code": "y-003",
                "date": "2024-03-20",
                "json": '{"city":"Napoli","score":30}',
            },
            {
                "id": 4,
                "group": "B",
                "qty": 4,
                "price": 40.0,
                "name": "gamma",
                "code": "y-004",
                "date": "2024-04-25",
                "json": '{"city":"Salerno","score":40}',
            },
        ]
    )


def _run(transform_id: str, inputs, params: dict):
    return get_executor().execute_with_params(transform_id, inputs, params)


def test_core_transforms() -> None:
    df = _base_df()

    filtered = _run("filter", [df], {"column": "group", "operator": "==", "value": "A"})
    assert_equal(filtered["id"].tolist(), [1, 2], "filter must keep group A rows")

    formula = _run("formula", [df], {"new_column": "total", "formula": "qty * price"})
    assert_equal(formula["total"].tolist(), [20.0, 60.0, 30.0, 160.0], "formula must compute totals")

    concat_cols = _run(
        "concat_columns",
        [df],
        {"columns": ["group", "code"], "output_column": "group_code", "separator": ":"},
    )
    assert_equal(
        concat_cols["group_code"].tolist(),
        ["A:x-001", "A:x-002", "B:y-003", "B:y-004"],
        "concat_columns must create deterministic joined strings",
    )

    aggregate = _run(
        "aggregate",
        [df],
        {
            "group_by": ["group"],
            "aggregations": [{"column": "qty", "function": "sum", "alias": "qty_sum"}],
        },
    ).sort_values("group").reset_index(drop=True)
    assert_equal(
        aggregate[["group", "qty_sum"]].to_dict("records"),
        [{"group": "A", "qty_sum": 5}, {"group": "B", "qty_sum": 5}],
        "aggregate must group and alias sum output",
    )

    sorted_df = _run("sort", [df], {"columns": ["price"], "ascending": False})
    assert_equal(sorted_df["id"].tolist(), [4, 3, 2, 1], "sort must order descending")

    distinct = _run("distinct", [df], {"subset": ["group"], "keep": "first"})
    assert_equal(distinct["id"].tolist(), [1, 3], "distinct must keep first row per group")

    renamed = _run("rename", [df], {"renames": [{"old_name": "qty", "new_name": "quantity"}]})
    assert_true("quantity" in renamed.columns and "qty" not in renamed.columns, "rename must rename columns")

    dropped = _run("drop_columns", [df], {"columns": ["json", "date"]})
    assert_equal(list(dropped.columns), ["id", "group", "qty", "price", "name", "code"], "drop_columns must remove selected columns")

    reordered = _run("reorder_columns", [df], {"columns": ["code", "id"], "sort_alphabetical": False})
    assert_equal(list(reordered.columns)[:2], ["code", "id"], "reorder_columns must honor requested order")

    filled = _run("fill_na", [df], {"column": "name", "method": "value", "value": "missing"})
    assert_equal(filled.loc[2, "name"], "missing", "fill_na must replace null values")

    replaced = _run("replace", [df], {"column": "group", "old_value": "A", "new_value": "AA"})
    assert_equal(replaced["group"].tolist(), ["AA", "AA", "B", "B"], "replace must replace literal values")

    split = _run(
        "split_column",
        [df],
        {"column": "code", "delimiter": "-", "new_columns": ["prefix", "num"], "max_splits": 1},
    )
    assert_equal(split["prefix"].tolist(), ["x", "x", "y", "y"], "split_column must create configured columns")

    casted = _run("type_cast", [df], {"column": "qty", "target_type": "str"})
    assert_equal(casted["qty"].tolist(), ["2", "3", "1", "4"], "type_cast int->str must keep whole numbers clean")

    conditional = _run(
        "conditional",
        [df],
        {
            "column": "price",
            "conditions": [{"operator": ">=", "value": "30", "result": "high"}],
            "default_value": "low",
            "output_column": "price_band",
        },
    )
    assert_equal(conditional["price_band"].tolist(), ["low", "low", "high", "high"], "conditional must apply ordered conditions")

    normalized = _run(
        "text_normalize",
        [df],
        {"columns": ["name"], "operations": "full", "overwrite": False},
    )
    assert_equal(normalized["name_norm"].tolist()[:2], ["alpha", "beta"], "text_normalize must trim/lower into _norm column")

    row_number = _run(
        "add_row_number",
        [df],
        {"output_column": "rn", "start": 10, "order_column": "id", "ascending": True},
    )
    assert_equal(row_number["rn"].tolist(), [10, 11, 12, 13], "add_row_number must generate deterministic sequence")

    date_extract = _run(
        "date_extract",
        [df],
        {"column": "date", "parts": ["year", "month"], "prefix": "d_"},
    )
    assert_equal(date_extract["d_month"].tolist(), [1, 2, 3, 4], "date_extract must create requested date parts")

    lookup = _run(
        "lookup",
        [df],
        {"column": "group", "mapping": {"A": "Alpha", "B": "Beta"}, "output_column": "group_label"},
    )
    assert_equal(lookup["group_label"].tolist(), ["Alpha", "Alpha", "Beta", "Beta"], "lookup must map values")

    binned = _run(
        "bin",
        [df],
        {"column": "price", "bins": 2, "labels": ["low", "high"], "output_column": "price_bin"},
    )
    assert_equal([str(v) for v in binned["price_bin"].tolist()], ["low", "low", "high", "high"], "bin must create labelled buckets")

    flattened = _run(
        "flatten_json",
        [df],
        {"column": "json", "prefix": "j_", "max_level": 1, "output_columns": ["j_city"]},
    )
    assert_equal(flattened["j_city"].tolist(), ["Milano", "Roma", "Napoli", "Salerno"], "flatten_json must extract requested keys")

    string_extract = _run(
        "string_extract",
        [df],
        {"column": "code", "pattern": r"(?P<prefix>[a-z])-(?P<num>\d+)", "extract_all": False},
    )
    assert_equal(string_extract["prefix"].tolist(), ["x", "x", "y", "y"], "string_extract must create named-group columns")

    padded = _run(
        "string_pad",
        [df],
        {"column": "id", "width": 3, "side": "left", "fill_char": "0", "output_column": "id_pad"},
    )
    assert_equal(padded["id_pad"].tolist(), ["001", "002", "003", "004"], "string_pad must pad values")

    lengths = _run("string_length", [df], {"column": "code", "output_column": "code_len"})
    assert_equal(lengths["code_len"].tolist(), [5, 5, 5, 5], "string_length must count characters")

    sampled = _run("sample", [df], {"n": 2, "random_state": 7})
    assert_equal(len(sampled), 2, "sample must return requested row count")

    dedup = _run(
        "dedup_advanced",
        [pd.concat([df, df.iloc[[0]]], ignore_index=True)],
        {"subset": ["id"], "keep": "first", "order_column": "id", "ascending": True},
    )
    assert_equal(dedup["id"].tolist(), [1, 2, 3, 4], "dedup_advanced must remove duplicate ids")

    windowed = _run(
        "window_function",
        [df],
        {"column": "qty", "function": "cumsum", "group_by": "group", "output_column": "qty_cumsum"},
    )
    assert_equal(windowed["qty_cumsum"].tolist(), [2, 5, 1, 5], "window_function cumsum must work by group")

    uuids = _run("uuid_generator", [df], {"output_column": "uuid"})
    assert_equal(len(set(uuids["uuid"].tolist())), 4, "uuid_generator must create unique values")

    melted = _run(
        "melt",
        [df[["id", "qty", "price"]]],
        {"id_columns": ["id"], "value_columns": ["qty", "price"], "var_name": "metric", "value_name": "metric_value"},
    )
    assert_equal(len(melted), 8, "melt must turn two value columns into eight rows")

    pivot_input = pd.DataFrame(
        [
            {"id": 1, "metric": "qty", "value": 2},
            {"id": 1, "metric": "price", "value": 10},
            {"id": 2, "metric": "qty", "value": 3},
            {"id": 2, "metric": "price", "value": 20},
        ]
    )
    pivoted = _run(
        "pivot",
        [pivot_input],
        {
            "index_col": "id",
            "pivot_col": "metric",
            "value_col": "value",
            "aggr_func": "first",
            "mapping": {"qty": "qty_value", "price": "price_value"},
        },
    ).sort_values("id").reset_index(drop=True)
    assert_equal(
        pivoted.to_dict("records"),
        [
            {"id": 1, "qty_value": 2, "price_value": 10},
            {"id": 2, "qty_value": 3, "price_value": 20},
        ],
        "pivot must create mapped metric columns",
    )

    old = pd.DataFrame([{"id": 1, "value": "a"}, {"id": 2, "value": "b"}])
    new = pd.DataFrame([{"id": 1, "value": "a"}, {"id": 2, "value": "c"}, {"id": 3, "value": "d"}])
    diff = _run(
        "table_diff",
        [old, new],
        {"left_keys": ["id"], "right_keys": ["id"], "compare_columns": ["value"], "include_unchanged": "no"},
    )
    assert_equal(
        sorted(diff["_diff_status"].tolist()),
        ["ADDED", "MODIFIED"],
        "table_diff must report added and modified rows",
    )


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            test_core_transforms()
