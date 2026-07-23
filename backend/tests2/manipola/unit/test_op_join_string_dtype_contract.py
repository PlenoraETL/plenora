"""
Contract test — op_join: chiavi stringa con dtype misti (pandas>=3).

Background:
Con pandas>=3 le colonne testo hanno dtype 'str' (arrow-backed) e non piu'
'object'. La riconciliazione tipi di op_join decideva "stringa vs numerico"
con un check `dtype == 'object'`: due chiavi entrambe stringa ma con
varianti di dtype diverse (es. 'str' da un roundtrip parquet vs 'string'
StringDtype da un output groupby) non erano 'object' e cadevano nel ramo
numerico, dove to_numeric(errors='coerce') azzerava a NaN tutte le chiavi
alfanumeriche su ENTRAMBI i lati: il join "riusciva" con zero match e le
colonne chiave uscivano svuotate, senza alcun errore visibile.

Caso reale: pipeline Scheda Complesso — spina immobiliare (chiave
psll_ls_id, dtype 'str' dall'artifact) left join anagrafica contratti
(chiave ls_ls_id, dtype 'string' post-aggregate): 295k chiavi -> tutte NaN.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.manipola,
    pytest.mark.contract,
]


def _string_series(values, dtype: str) -> pd.Series:
    # pd.array(dtype="object") in pandas>=3 normalizza comunque a 'str':
    # per l'object legacy serve pd.Series con dtype esplicito.
    if dtype == "object":
        return pd.Series(values, dtype=object)
    return pd.Series(pd.array(values, dtype=dtype))


def _build_frames(left_dtype: str, right_dtype: str):
    left = pd.DataFrame({
        "unita": [f"SU{i:06d}" for i in range(20)],
        "psll_ls_id": _string_series(
            [f"LS_{i % 8:03d}" for i in range(12)] + [None] * 8,
            left_dtype,
        ),
    })
    right = pd.DataFrame({
        "ls_ls_id": _string_series([f"LS_{i:03d}" for i in range(8)], right_dtype),
        "inquilino": [f"NOME {i}" for i in range(8)],
    })
    return left, right


@pytest.mark.parametrize(
    ("left_dtype", "right_dtype"),
    [
        ("str", "string"),      # arrow-default vs StringDtype: il caso reale
        ("string", "str"),
        ("str", "object"),      # legacy mixato con pandas>=3
        ("object", "string"),
    ],
)
def test_join_string_keys_mixed_dtypes_preserve_values_and_match(
    left_dtype: str, right_dtype: str
):
    """Chiavi stringa con dtype diversi: valori preservati e match eseguiti."""
    from app.core.manipola.transforms.joins import op_join

    left, right = _build_frames(left_dtype, right_dtype)
    assert left["psll_ls_id"].dtype != right["ls_ls_id"].dtype

    result = op_join(
        [left, right],
        left_keys=["psll_ls_id"],
        right_keys=["ls_ls_id"],
        how="left",
    )

    # Il left join preserva tutte le righe di sinistra
    assert len(result) == len(left)
    # Le chiavi valorizzate NON devono essere azzerate dalla coercizione tipi
    assert int(result["psll_ls_id"].notna().sum()) == 12
    # E devono aver trovato il match sul lato destro
    inquilino_col = next(c for c in result.columns if c.startswith("inquilino"))
    assert int(result[inquilino_col].notna().sum()) == 12


def test_join_numeric_vs_numericlike_string_still_coerces_to_match():
    """Il ramo numerico resta attivo per chiavi davvero numeriche.

    int64 vs stringhe numeriche ("1", "2"...): un lato e' string-like,
    quindi si converge a stringa e il match funziona comunque.
    """
    from app.core.manipola.transforms.joins import op_join

    left = pd.DataFrame({"k": [1, 2, 3, 4], "a": list("wxyz")})
    right = pd.DataFrame({"j": pd.array(["1", "2", "3"], dtype="str"),
                          "b": list("abc")})

    result = op_join([left, right], left_keys=["k"], right_keys=["j"], how="left")

    assert len(result) == 4
    b_col = next(c for c in result.columns if c.startswith("b"))
    assert int(result[b_col].notna().sum()) == 3


def test_join_pure_numeric_dtype_mismatch_unaffected():
    """int64 vs float64: coppia davvero numerica, coercizione numerica ok."""
    from app.core.manipola.transforms.joins import op_join

    left = pd.DataFrame({"k": np.array([1, 2, 3], dtype="int64"), "a": list("xyz")})
    right = pd.DataFrame({"j": np.array([1.0, 2.0], dtype="float64"), "b": list("ab")})

    result = op_join([left, right], left_keys=["k"], right_keys=["j"], how="left")

    assert len(result) == 3
    assert int(result["k"].notna().sum()) == 3
    b_col = next(c for c in result.columns if c.startswith("b"))
    assert int(result[b_col].notna().sum()) == 2
