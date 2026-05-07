"""
Contract test — fail-closed sui 32 transform top-level di base.py (E1).

Verifica che un errore interno dei 32 transform top-level di
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from app.core.manipola.transforms.base import (
    op_aggregate,
    op_pivot,
    op_sort,
    op_type_cast,
)


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]


# 1. Contratto fail-closed su 3 transform rappresentative

@pytest.mark.parametrize(
    "transform, patch_target, kwargs",
    [
        (
            op_aggregate,
            "pandas.DataFrame.groupby",
            {"group_by": ["grp"], "aggregations": [{"column": "val", "function": "sum"}]},
        ),
        (
            op_sort,
            "pandas.DataFrame.sort_values",
            {"columns": ["val"]},
        ),
        (
            op_pivot,
            "pandas.pivot_table",
            {"index_col": "grp", "pivot_col": "cat", "value_col": "val"},
        ),
    ],
    ids=["aggregate", "sort", "pivot"],
)
def test_top_level_transform_reraises_on_internal_failure(transform, patch_target, kwargs):
    """Se un'API pandas interna solleva, la transform non deve ingoiare
    l'errore: deve propagarlo al caller (fail-closed)."""
    df = pd.DataFrame({"grp": ["A", "A", "B"], "val": [1, 2, 3], "cat": ["x", "y", "x"]})

    with patch(patch_target, side_effect=RuntimeError("mock-base-failure")):
        with pytest.raises(RuntimeError, match="mock-base-failure"):
            transform(df, **kwargs)


# 2. Smoke regression sull'happy path di op_aggregate

def test_aggregate_happy_path_unchanged():
    """Il fix E1 tocca solo il ramo except: l'happy path deve restare
    identico. op_aggregate con sum su un gruppo valido produce il
    DataFrame aggregato atteso."""
    df = pd.DataFrame({"grp": ["A", "A", "B"], "val": [1, 2, 3]})
    result = op_aggregate(
        df,
        group_by=["grp"],
        aggregations=[{"column": "val", "function": "sum"}],
    )
    assert len(result) == 2
    grouped = result.set_index("grp")["val"].to_dict()
    assert grouped["A"] == 3
    assert grouped["B"] == 3


# 3. op_type_cast con errors='coerce' (copertura R1 audit §7.4)

def test_type_cast_coerce_happy_path_unchanged():
    """Happy path di errors='coerce' invariato: stringhe non numeriche
    diventano NaN (semantica di pd.to_numeric(errors='coerce')).
    Il fix E1 non tocca il percorso in cui pd.to_numeric assorbe gli
    errori di parsing per design."""
    df = pd.DataFrame({"col": ["10", "abc", "30"]})
    result = op_type_cast(df, column="col", target_type="int", errors="coerce")
    assert result["col"].iloc[0] == 10
    assert pd.isna(result["col"].iloc[1])  # 'abc' -> NaN via coerce
    assert result["col"].iloc[2] == 30


def test_type_cast_coerce_reraises_on_internal_failure():
    """Pre-fix E1: errori esterni a pd.to_numeric (es. crash interno)
    venivano silenziati anche con errors='coerce', ritornando df
    invariato. Post-fix: l'errore propaga al caller.

    Nota (audit §7.4): cambio di semantica osservabile, accettato come
    allineamento behaviour-doc. 'coerce' documenta il comportamento
    di pd.to_numeric sul parsing, non un silenziamento di bug."""
    df = pd.DataFrame({"col": ["10", "20", "30"]})

    with patch(
        "app.core.manipola.transforms.base.pd.to_numeric",
        side_effect=RuntimeError("mock-type-cast-failure"),
    ):
        with pytest.raises(RuntimeError, match="mock-type-cast-failure"):
            op_type_cast(df, column="col", target_type="int", errors="coerce")
