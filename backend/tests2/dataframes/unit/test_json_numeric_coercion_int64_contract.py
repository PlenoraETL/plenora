"""Contract — coercion numerica JSON preserva la precisione degli interi con null.

Backend master Part II #4: pd.to_numeric declassa a float64 una colonna di interi
con null → i valori > 2^53 si arrotondano. La coercion ora usa Int64 (nullable)
riconvertendo dalle stringhe originali, preservando precisione e null.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.core.dataframes.frame_io import _coerce_json_dataframe_columns


pytestmark = [pytest.mark.unit, pytest.mark.contract]

_BIG = 9007199254740993  # 2^53 + 1: non rappresentabile esatto in float64


def test_large_int_with_null_preserved_as_nullable_int():
    df = pd.DataFrame({"id": [str(_BIG), None, "42"]})
    out = _coerce_json_dataframe_columns(df)
    assert str(out["id"].dtype) == "Int64"
    assert int(out["id"].iloc[0]) == _BIG  # precisione preservata
    assert pd.isna(out["id"].iloc[1])
    assert int(out["id"].iloc[2]) == 42


def test_fractional_column_stays_float():
    df = pd.DataFrame({"x": ["1.5", None, "2.0"]})
    out = _coerce_json_dataframe_columns(df)
    assert str(out["x"].dtype).startswith("float")


def test_integer_strings_without_null_still_numeric():
    df = pd.DataFrame({"id": ["1", "2", "3"]})
    out = _coerce_json_dataframe_columns(df)
    assert "int" in str(out["id"].dtype).lower()
    assert out["id"].tolist() == [1, 2, 3]


def test_non_numeric_column_untouched():
    df = pd.DataFrame({"name": ["a", "b", None]})
    out = _coerce_json_dataframe_columns(df)
    # resta object/string (nessuna conversione numerica)
    assert out["name"].iloc[0] == "a"
