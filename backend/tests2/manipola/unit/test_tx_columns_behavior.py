"""
Behavioral coverage — app/core/manipola/transforms/columns.py

Esercita le funzioni pubbliche del cluster COLUMNS:
  - op_drop_columns
  - op_rename
  - op_reorder_columns
  - op_concat_columns
  - op_split_column

Stile: happy-path + rami significativi (no-op guards, colonne
inesistenti, NULL handling, batch RAM-bounded, immutabilita' input).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.manipola.transforms import columns as tx
from app.core.manipola.transforms.columns import (
    op_concat_columns,
    op_drop_columns,
    op_rename,
    op_reorder_columns,
    op_split_column,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.manipola,
]


# --------------------------------------------------------------------------
# op_drop_columns
# --------------------------------------------------------------------------


def test_drop_columns_removes_existing():
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
    out = op_drop_columns(df, ["b"])
    assert list(out.columns) == ["a", "c"]
    # input non mutato
    assert list(df.columns) == ["a", "b", "c"]


def test_drop_columns_removes_multiple():
    df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
    out = op_drop_columns(df, ["a", "c"])
    assert list(out.columns) == ["b"]


def test_drop_columns_empty_list_is_noop_returns_same_object():
    df = pd.DataFrame({"a": [1]})
    out = op_drop_columns(df, [])
    assert out is df


def test_drop_columns_none_is_noop():
    df = pd.DataFrame({"a": [1]})
    out = op_drop_columns(df, None)
    assert out is df


def test_drop_columns_only_nonexistent_returns_same_object():
    df = pd.DataFrame({"a": [1], "b": [2]})
    out = op_drop_columns(df, ["x", "y"])
    assert out is df
    assert list(out.columns) == ["a", "b"]


def test_drop_columns_filters_nonexistent_keeps_existing():
    df = pd.DataFrame({"a": [1], "b": [2]})
    out = op_drop_columns(df, ["a", "zzz"])
    assert list(out.columns) == ["b"]


# --------------------------------------------------------------------------
# op_rename
# --------------------------------------------------------------------------


def test_rename_single_legacy_args():
    df = pd.DataFrame({"old": [1, 2]})
    out = op_rename(df, old_name="old", new_name="new")
    assert list(out.columns) == ["new"]
    assert list(df.columns) == ["old"]  # input intatto


def test_rename_single_nonexistent_is_noop():
    df = pd.DataFrame({"a": [1]})
    out = op_rename(df, old_name="missing", new_name="new")
    assert out is df


def test_rename_list_multiple():
    df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
    out = op_rename(
        df,
        renames=[
            {"old_name": "a", "new_name": "x"},
            {"old_name": "c", "new_name": "z"},
        ],
    )
    assert list(out.columns) == ["x", "b", "z"]


def test_rename_list_skips_invalid_entries():
    df = pd.DataFrame({"a": [1], "b": [2]})
    out = op_rename(
        df,
        renames=[
            {"old_name": "a", "new_name": "x"},
            {"old_name": "missing", "new_name": "y"},  # colonna assente
            {"old_name": "b", "new_name": ""},          # new vuoto -> skip
            {"old_name": "", "new_name": "q"},          # old vuoto -> skip
        ],
    )
    assert list(out.columns) == ["x", "b"]


def test_rename_no_valid_returns_same_object():
    df = pd.DataFrame({"a": [1]})
    out = op_rename(df, renames=[{"old_name": "nope", "new_name": "x"}])
    assert out is df


def test_rename_list_takes_priority_over_legacy_args():
    # `renames` presente -> ramo legacy ignorato anche se old/new dati
    df = pd.DataFrame({"a": [1], "b": [2]})
    out = op_rename(
        df,
        old_name="b",
        new_name="legacy",
        renames=[{"old_name": "a", "new_name": "fromlist"}],
    )
    assert "fromlist" in out.columns
    assert "b" in out.columns
    assert "legacy" not in out.columns


def test_rename_no_args_at_all_noop():
    df = pd.DataFrame({"a": [1]})
    out = op_rename(df)
    assert out is df


# --------------------------------------------------------------------------
# op_reorder_columns
# --------------------------------------------------------------------------


def test_reorder_explicit_partial_appends_remaining():
    df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
    out = op_reorder_columns(df, columns=["c", "a"])
    # valid_cols seguiti dai remaining in ordine originale
    assert list(out.columns) == ["c", "a", "b"]


def test_reorder_ignores_unknown_columns():
    df = pd.DataFrame({"a": [1], "b": [2]})
    out = op_reorder_columns(df, columns=["b", "ghost", "a"])
    assert list(out.columns) == ["b", "a"]


def test_reorder_alphabetical():
    df = pd.DataFrame({"banana": [1], "apple": [2], "cherry": [3]})
    out = op_reorder_columns(df, sort_alphabetical=True)
    assert list(out.columns) == ["apple", "banana", "cherry"]


def test_reorder_columns_wins_over_alphabetical():
    df = pd.DataFrame({"b": [1], "a": [2]})
    out = op_reorder_columns(df, columns=["b", "a"], sort_alphabetical=True)
    assert list(out.columns) == ["b", "a"]


def test_reorder_no_params_returns_same_object():
    df = pd.DataFrame({"a": [1], "b": [2]})
    out = op_reorder_columns(df)
    assert out is df


def test_reorder_empty_df_returns_same_object():
    df = pd.DataFrame({"a": [], "b": []})
    out = op_reorder_columns(df, columns=["b", "a"])
    assert out is df


def test_reorder_preserves_values():
    df = pd.DataFrame({"a": [10, 20], "b": [1, 2]})
    out = op_reorder_columns(df, columns=["b", "a"])
    assert out["a"].tolist() == [10, 20]
    assert out["b"].tolist() == [1, 2]


# --------------------------------------------------------------------------
# op_concat_columns
# --------------------------------------------------------------------------


def test_concat_basic_two_columns():
    df = pd.DataFrame({"cognome": ["ROSSI", "VERDI"], "nome": ["MARIO", "ANNA"]})
    out = op_concat_columns(df, ["cognome", "nome"], output_column="full")
    assert out["full"].tolist() == ["ROSSI MARIO", "VERDI ANNA"]


def test_concat_custom_separator_and_output_name():
    df = pd.DataFrame({"a": ["x", "y"], "b": ["1", "2"]})
    out = op_concat_columns(df, ["a", "b"], output_column="combo", separator="-")
    assert out["combo"].tolist() == ["x-1", "y-2"]


def test_concat_skip_null_omits_na_values():
    df = pd.DataFrame({"a": ["X", None], "b": ["Y", "Z"]})
    out = op_concat_columns(df, ["a", "b"], output_column="r", skip_null=True)
    # riga 0: "X Y"; riga 1: solo "Z" (None saltato)
    assert out["r"].tolist() == ["X Y", "Z"]


def test_concat_skip_null_all_na_row_becomes_none():
    df = pd.DataFrame({"a": [None, "K"], "b": [None, "L"]})
    out = op_concat_columns(df, ["a", "b"], output_column="r", skip_null=True)
    # riga 0: join di nulla -> "" -> missing; riga 1: "K L"
    assert pd.isna(out["r"].iloc[0])
    assert out["r"].iloc[1] == "K L"


def test_concat_skip_null_false_treats_na_as_string():
    df = pd.DataFrame({"a": ["X", None], "b": ["Y", "Z"]})
    out = op_concat_columns(df, ["a", "b"], output_column="r", skip_null=False)
    # non skippa: NaN diventa "" via join_columns_as_strings
    assert out["r"].tolist() == ["X Y", " Z"]


def test_concat_no_columns_returns_same_object():
    df = pd.DataFrame({"a": [1]})
    out = op_concat_columns(df, [], output_column="r")
    assert out is df


def test_concat_no_valid_columns_returns_same_object():
    df = pd.DataFrame({"a": [1]})
    out = op_concat_columns(df, ["ghost1", "ghost2"], output_column="r")
    assert out is df


def test_concat_empty_dataframe_creates_empty_output_column():
    df = pd.DataFrame({"a": pd.Series([], dtype=object), "b": pd.Series([], dtype=object)})
    out = op_concat_columns(df, ["a", "b"], output_column="r", skip_null=True)
    assert "r" in out.columns
    assert len(out) == 0


def test_concat_does_not_mutate_input():
    df = pd.DataFrame({"a": ["1"], "b": ["2"]})
    op_concat_columns(df, ["a", "b"], output_column="r")
    assert "r" not in df.columns


def test_concat_batch_boundary_skip_null(monkeypatch):
    # Forza piu' batch (batch size piccolo) per coprire il loop range().
    monkeypatch.setattr(tx, "_CONCAT_BATCH_SIZE", 2)
    df = pd.DataFrame(
        {
            "a": ["a0", "a1", None, "a3", "a4"],
            "b": ["b0", "b1", "b2", None, "b4"],
        }
    )
    out = op_concat_columns(df, ["a", "b"], output_column="r", skip_null=True)
    assert out["r"].tolist() == ["a0 b0", "a1 b1", "b2", "a3", "a4 b4"]


def test_concat_numeric_columns_coerced_to_string():
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    out = op_concat_columns(df, ["a", "b"], output_column="r", skip_null=True)
    assert out["r"].tolist() == ["1 3", "2 4"]


# --------------------------------------------------------------------------
# op_split_column
# --------------------------------------------------------------------------


def test_split_auto_names():
    df = pd.DataFrame({"full": ["a,b,c", "x,y,z"]})
    out = op_split_column(df, "full", delimiter=",")
    assert out["full_part1"].tolist() == ["a", "x"]
    assert out["full_part2"].tolist() == ["b", "y"]
    assert out["full_part3"].tolist() == ["c", "z"]


def test_split_with_explicit_new_columns():
    df = pd.DataFrame({"full": ["a-b", "c-d"]})
    out = op_split_column(df, "full", delimiter="-", new_columns=["first", "second"])
    assert out["first"].tolist() == ["a", "c"]
    assert out["second"].tolist() == ["b", "d"]


def test_split_new_columns_fewer_than_parts():
    df = pd.DataFrame({"full": ["a,b,c"]})
    out = op_split_column(df, "full", delimiter=",", new_columns=["only"])
    assert out["only"].tolist() == ["a"]
    # le altre parti non vengono create
    assert "full_part2" not in out.columns


def test_split_max_splits_limits_parts():
    df = pd.DataFrame({"full": ["a,b,c,d"]})
    out = op_split_column(df, "full", delimiter=",", max_splits=2)
    # max_splits=2 -> n=2 -> 3 colonne (2 split + resto)
    assert out["full_part1"].tolist() == ["a"]
    assert out["full_part2"].tolist() == ["b"]
    assert out["full_part3"].tolist() == ["c,d"]


def test_split_max_splits_negative_means_all():
    df = pd.DataFrame({"full": ["a.b.c"]})
    out = op_split_column(df, "full", delimiter=".", max_splits=-1)
    assert out["full_part1"].tolist() == ["a"]
    assert out["full_part3"].tolist() == ["c"]


def test_split_missing_column_returns_same_object():
    df = pd.DataFrame({"a": [1]})
    out = op_split_column(df, "ghost", delimiter=",")
    assert out is df


def test_split_empty_column_name_returns_same_object():
    df = pd.DataFrame({"a": [1]})
    out = op_split_column(df, "", delimiter=",")
    assert out is df


def test_split_does_not_mutate_input():
    df = pd.DataFrame({"full": ["a,b"]})
    op_split_column(df, "full", delimiter=",")
    assert list(df.columns) == ["full"]


def test_split_no_delimiter_present_single_part():
    df = pd.DataFrame({"full": ["nodelim", "alsoone"]})
    out = op_split_column(df, "full", delimiter=",")
    assert out["full_part1"].tolist() == ["nodelim", "alsoone"]
    assert "full_part2" not in out.columns


def test_split_numeric_column_coerced_to_string():
    df = pd.DataFrame({"code": [12, 34]})
    out = op_split_column(df, "code", delimiter="0")
    # "12" e "34" non contengono "0" -> singola parte
    assert out["code_part1"].tolist() == ["12", "34"]
