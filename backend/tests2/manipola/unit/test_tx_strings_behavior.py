"""
Behavioral coverage — app/core/manipola/transforms/strings.py

Esercita le quattro funzioni pubbliche del cluster STRINGS:
  - op_string_pad
  - op_string_length
  - op_string_extract
  - op_text_normalize

Test comportamentali: input pandas.DataFrame minimi/realistici, verifica
sui valori prodotti + rami significativi (colonna mancante, df vuoto,
pattern regex non valido, gruppi nominati, extract_all, preservazione
non-string in normalize, strip_accents, overwrite vs colonna _norm).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.manipola.transforms.strings import (
    op_string_extract,
    op_string_length,
    op_string_pad,
    op_text_normalize,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.manipola,
]


# ---------------------------------------------------------------------------
# op_string_pad
# ---------------------------------------------------------------------------


def test_pad_left_default_zero_fill():
    df = pd.DataFrame({"code": ["7", "42", "999"]})
    out = op_string_pad(df, column="code", width=5)
    assert out["code"].tolist() == ["00007", "00042", "00999"]


def test_pad_right_with_custom_fill_char():
    df = pd.DataFrame({"code": ["ab", "c"]})
    out = op_string_pad(df, column="code", width=4, side="right", fill_char="*")
    assert out["code"].tolist() == ["ab**", "c***"]


def test_pad_writes_to_output_column_preserving_original():
    df = pd.DataFrame({"code": ["7"]})
    out = op_string_pad(df, column="code", width=3, output_column="padded")
    assert out["padded"].tolist() == ["007"]
    # originale invariata
    assert out["code"].tolist() == ["7"]


def test_pad_value_longer_than_width_is_unchanged():
    df = pd.DataFrame({"code": ["abcdef"]})
    out = op_string_pad(df, column="code", width=3)
    assert out["code"].tolist() == ["abcdef"]


def test_pad_does_not_mutate_input_dataframe():
    df = pd.DataFrame({"code": ["7", "8"]})
    op_string_pad(df, column="code", width=3)
    assert df["code"].tolist() == ["7", "8"]


def test_pad_empty_dataframe_returns_unchanged():
    df = pd.DataFrame({"code": pd.Series([], dtype=object)})
    out = op_string_pad(df, column="code", width=5)
    assert len(out) == 0
    assert out is df  # ritorna lo stesso oggetto sul ramo len==0


def test_pad_missing_column_returns_df_unchanged():
    df = pd.DataFrame({"code": ["7"]})
    out = op_string_pad(df, column="absent", width=5)
    assert out is df
    assert list(out.columns) == ["code"]


# ---------------------------------------------------------------------------
# op_string_length
# ---------------------------------------------------------------------------


def test_length_default_output_column_name():
    df = pd.DataFrame({"name": ["ab", "abcd", ""]})
    out = op_string_length(df, column="name")
    assert "name_length" in out.columns
    assert out["name_length"].tolist() == [2, 4, 0]


def test_length_custom_output_column():
    df = pd.DataFrame({"name": ["hello"]})
    out = op_string_length(df, column="name", output_column="len")
    assert out["len"].tolist() == [5]


def test_length_counts_numeric_as_string_repr():
    df = pd.DataFrame({"n": [123, 4]})
    out = op_string_length(df, column="n")
    # astype(str): "123" -> 3, "4" -> 1
    assert out["n_length"].tolist() == [3, 1]


def test_length_empty_dataframe_returns_same_object():
    df = pd.DataFrame({"name": pd.Series([], dtype=object)})
    out = op_string_length(df, column="name")
    assert out is df


def test_length_missing_column_returns_df_unchanged():
    df = pd.DataFrame({"name": ["x"]})
    out = op_string_length(df, column="missing")
    assert out is df
    assert list(out.columns) == ["name"]


# ---------------------------------------------------------------------------
# op_string_extract
# ---------------------------------------------------------------------------


def test_extract_single_capture_group():
    df = pd.DataFrame({"sku": ["ID-001", "ID-042", "ID-999"]})
    out = op_string_extract(df, column="sku", pattern=r"ID-(\d+)")
    assert "sku_extracted" in out.columns
    assert out["sku_extracted"].tolist() == ["001", "042", "999"]


def test_extract_single_group_no_match_yields_nan():
    df = pd.DataFrame({"sku": ["ID-001", "nope"]})
    out = op_string_extract(df, column="sku", pattern=r"ID-(\d+)")
    vals = out["sku_extracted"].tolist()
    assert vals[0] == "001"
    assert vals[1] is np.nan or pd.isna(vals[1])


def test_extract_custom_output_column():
    df = pd.DataFrame({"sku": ["X-5"]})
    out = op_string_extract(
        df, column="sku", pattern=r"X-(\d+)", output_column="num"
    )
    assert out["num"].tolist() == ["5"]


def test_extract_named_groups_create_multiple_columns():
    df = pd.DataFrame({"loc": ["B1-F3", "B2-F7"]})
    out = op_string_extract(
        df,
        column="loc",
        pattern=r"B(?P<building>\d+)-F(?P<floor>\d+)",
    )
    assert "building" in out.columns
    assert "floor" in out.columns
    assert out["building"].tolist() == ["1", "2"]
    assert out["floor"].tolist() == ["3", "7"]


def test_extract_all_joins_matches_with_comma():
    df = pd.DataFrame({"text": ["a1 b2 c3", "z9"]})
    out = op_string_extract(
        df, column="text", pattern=r"[a-z](\d)", extract_all=True
    )
    assert out["text_extracted"].tolist() == ["1,2,3", "9"]


def test_extract_all_no_match_yields_none():
    df = pd.DataFrame({"text": ["no digits here"]})
    out = op_string_extract(
        df, column="text", pattern=r"(\d)", extract_all=True
    )
    assert out["text_extracted"].tolist() == [None]


def test_extract_invalid_regex_returns_df_unchanged():
    df = pd.DataFrame({"sku": ["X-1"]})
    out = op_string_extract(df, column="sku", pattern=r"(unbalanced")
    # pattern non valido: ritorna il df senza colonna estratta
    assert out is df
    assert "sku_extracted" not in out.columns


def test_extract_missing_column_returns_df_unchanged():
    df = pd.DataFrame({"sku": ["X-1"]})
    out = op_string_extract(df, column="absent", pattern=r"(\d)")
    assert out is df


def test_extract_does_not_mutate_input():
    df = pd.DataFrame({"sku": ["ID-1"]})
    op_string_extract(df, column="sku", pattern=r"ID-(\d)")
    assert list(df.columns) == ["sku"]


# ---------------------------------------------------------------------------
# op_text_normalize
# ---------------------------------------------------------------------------


def test_normalize_full_trims_lowercases_and_collapses_spaces():
    df = pd.DataFrame({"name": ["  Hello   World  "]})
    out = op_text_normalize(df, columns=["name"], operations="full")
    assert out["name"].tolist() == ["hello world"]


def test_normalize_upper():
    df = pd.DataFrame({"name": ["abc", "DeF"]})
    out = op_text_normalize(df, columns=["name"], operations="upper")
    assert out["name"].tolist() == ["ABC", "DEF"]


def test_normalize_lower():
    df = pd.DataFrame({"name": ["ABC", "DeF"]})
    out = op_text_normalize(df, columns=["name"], operations="lower")
    assert out["name"].tolist() == ["abc", "def"]


def test_normalize_title():
    df = pd.DataFrame({"name": ["hello world"]})
    out = op_text_normalize(df, columns=["name"], operations="title")
    assert out["name"].tolist() == ["Hello World"]


def test_normalize_trim_only():
    df = pd.DataFrame({"name": ["  spaced  "]})
    out = op_text_normalize(df, columns=["name"], operations="trim")
    assert out["name"].tolist() == ["spaced"]


def test_normalize_strip_double_spaces():
    df = pd.DataFrame({"name": ["a    b\t c"]})
    out = op_text_normalize(df, columns=["name"], operations="strip_double_spaces")
    assert out["name"].tolist() == ["a b c"]


def test_normalize_strip_accents_uses_apply_branch():
    df = pd.DataFrame({"name": ["caffè", "città", "über"]})
    out = op_text_normalize(df, columns=["name"], operations="strip_accents")
    assert out["name"].tolist() == ["caffe", "citta", "uber"]


def test_normalize_full_includes_strip_accents():
    df = pd.DataFrame({"name": ["  CAFFÈ  Latte "]})
    out = op_text_normalize(df, columns=["name"], operations="full")
    # full: trim + lower + strip_accents + collapse spaces
    assert out["name"].tolist() == ["caffe latte"]


def test_normalize_no_overwrite_creates_norm_column():
    df = pd.DataFrame({"name": ["ABC"]})
    out = op_text_normalize(
        df, columns=["name"], operations="lower", overwrite=False
    )
    assert out["name"].tolist() == ["ABC"]  # originale preservata
    assert out["name_norm"].tolist() == ["abc"]


def test_normalize_preserves_non_string_values_vectorized_branch():
    # operations != strip_accents/full -> branch vettoriale che preserva i
    # non-string (qui un int) invariati.
    df = pd.DataFrame({"mix": ["Hello", 42, None]})
    out = op_text_normalize(df, columns=["mix"], operations="lower")
    vals = out["mix"].tolist()
    assert vals[0] == "hello"
    assert vals[1] == 42  # int preservato, non NaN
    assert vals[2] is None or pd.isna(vals[2])


def test_normalize_preserves_non_string_in_strip_accents_branch():
    # branch .apply(): non-string ritornato invariato da normalize_text
    df = pd.DataFrame({"mix": ["caffè", 99]})
    out = op_text_normalize(df, columns=["mix"], operations="strip_accents")
    vals = out["mix"].tolist()
    assert vals[0] == "caffe"
    assert vals[1] == 99


def test_normalize_empty_columns_list_returns_df_unchanged():
    df = pd.DataFrame({"name": ["X"]})
    out = op_text_normalize(df, columns=[], operations="full")
    assert out is df


def test_normalize_no_valid_columns_returns_df_unchanged():
    df = pd.DataFrame({"name": ["X"]})
    out = op_text_normalize(df, columns=["absent"], operations="full")
    assert out is df
    assert list(out.columns) == ["name"]


def test_normalize_multiple_columns_at_once():
    df = pd.DataFrame({"a": ["AB"], "b": ["CD"], "skip": [1]})
    out = op_text_normalize(df, columns=["a", "b", "absent"], operations="lower")
    assert out["a"].tolist() == ["ab"]
    assert out["b"].tolist() == ["cd"]
    assert out["skip"].tolist() == [1]


def test_normalize_does_not_mutate_input():
    df = pd.DataFrame({"name": ["ABC"]})
    op_text_normalize(df, columns=["name"], operations="lower")
    assert df["name"].tolist() == ["ABC"]
