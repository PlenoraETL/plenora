"""
Contract test — op_fill_na type compatibility (C2 Phase 4).

Background:
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


# 1. Casi canonici richiesti dal prompt


def test_fill_na_numeric_column_with_non_numeric_string_raises():
    """numeric + value='N/A' -> ValueError fail-loud, niente upcast a object."""
    from app.core.manipola.transforms.base import op_fill_na

    df = pd.DataFrame({"amount": [1.0, 2.0, np.nan, 4.0]})
    assert pd.api.types.is_float_dtype(df["amount"])

    with pytest.raises(ValueError) as exc:
        op_fill_na(df, column="amount", method="value", value="N/A")
    msg = str(exc.value).lower()
    assert "amount" in msg
    assert "numeric" in msg or "numerico" in msg


def test_fill_na_numeric_column_with_int_value_preserves_dtype():
    """numeric + value=0 -> ok, dtype numerico preservato (no upcast)."""
    from app.core.manipola.transforms.base import op_fill_na

    df = pd.DataFrame({"amount": [1.0, 2.0, np.nan, 4.0]})
    result = op_fill_na(df, column="amount", method="value", value=0)
    assert pd.api.types.is_float_dtype(result["amount"]), (
        f"dtype atteso float, ottenuto {result['amount'].dtype}"
    )
    assert result["amount"].tolist() == [1.0, 2.0, 0.0, 4.0]


def test_fill_na_numeric_column_with_int_dtype_and_int_value():
    """int column + int value -> int preservato (no upcast a float)."""
    from app.core.manipola.transforms.base import op_fill_na

    df = pd.DataFrame({"qty": pd.Series([1, 2, 3, 4], dtype="int64")})
    # NB: int64 non puo' rappresentare NaN; testiamo che il fill su una
    # serie int senza null non rompa il dtype.
    result = op_fill_na(df, column="qty", method="value", value=0)
    assert pd.api.types.is_integer_dtype(result["qty"]), (
        f"dtype atteso integer, ottenuto {result['qty'].dtype}"
    )


def test_fill_na_numeric_column_with_coercible_string_value():
    """numeric + value='1.5' -> ok, coerced a float (no upcast object)."""
    from app.core.manipola.transforms.base import op_fill_na

    df = pd.DataFrame({"amount": [1.0, 2.0, np.nan, 4.0]})
    result = op_fill_na(df, column="amount", method="value", value="1.5")
    assert pd.api.types.is_float_dtype(result["amount"]), (
        f"dtype atteso float dopo coerce, ottenuto {result['amount'].dtype}"
    )
    assert result["amount"].tolist() == [1.0, 2.0, 1.5, 4.0]


def test_fill_na_numeric_column_with_coercible_int_string():
    """numeric + value='0' -> ok, coerced (caso piu' frequente in canvas)."""
    from app.core.manipola.transforms.base import op_fill_na

    df = pd.DataFrame({"amount": [1.0, 2.0, np.nan, 4.0]})
    result = op_fill_na(df, column="amount", method="value", value="0")
    assert pd.api.types.is_numeric_dtype(result["amount"])
    assert result["amount"].iloc[2] == 0


def test_fill_na_object_column_accepts_arbitrary_string():
    """object/string column + value='N/A' -> ok, free-form (no policy)."""
    from app.core.manipola.transforms.base import op_fill_na

    df = pd.DataFrame({"label": ["a", "b", None, "d"]})
    # pandas 2.x produce dtype object; pandas 3.x produce StringDtype.
    # Entrambi sono "non-numerici" per la nostra policy: il fill passa raw.
    assert not pd.api.types.is_numeric_dtype(df["label"])
    result = op_fill_na(df, column="label", method="value", value="N/A")
    assert result["label"].tolist() == ["a", "b", "N/A", "d"]


# 2. Edge cases della policy


def test_fill_na_numeric_column_with_none_value_is_noop():
    """numeric + value=None -> accept, no upcast, NaN restano NaN."""
    from app.core.manipola.transforms.base import op_fill_na

    df = pd.DataFrame({"amount": [1.0, np.nan, 3.0]})
    result = op_fill_na(df, column="amount", method="value", value=None)
    assert pd.api.types.is_float_dtype(result["amount"])


def test_fill_na_numeric_column_with_nan_value_is_noop():
    """numeric + value=float('nan') -> accept, no upcast."""
    from app.core.manipola.transforms.base import op_fill_na

    df = pd.DataFrame({"amount": [1.0, np.nan, 3.0]})
    result = op_fill_na(df, column="amount", method="value", value=float("nan"))
    assert pd.api.types.is_float_dtype(result["amount"])


def test_fill_na_numeric_column_with_bool_value_raises():
    """numeric + value=True -> ValueError (anti-confusione fill flag).

    bool e' un sottotipo di int in Python; lasciar passare il True su una
    colonna float darebbe 1.0 silenzioso, mascherando un bug del canvas
    (utente che ha confuso il fill di un flag con un fill numerico)."""
    from app.core.manipola.transforms.base import op_fill_na

    df = pd.DataFrame({"amount": [1.0, np.nan]})
    with pytest.raises(ValueError, match="bool"):
        op_fill_na(df, column="amount", method="value", value=True)


def test_fill_na_numeric_column_with_empty_string_raises():
    """numeric + value='' (default!) -> ValueError esplicito.

    Default Python `value=""` su colonna numerica e' la fonte canonica
    del bug pre-C2: senza override il fill demoteva la colonna a object.
    Il messaggio di errore deve indirizzare l'utente al fix."""
    from app.core.manipola.transforms.base import op_fill_na

    df = pd.DataFrame({"amount": [1.0, np.nan]})
    with pytest.raises(ValueError) as exc:
        op_fill_na(df, column="amount", method="value", value="")
    msg = str(exc.value).lower()
    # messaggio cita la colonna e suggerisce il fix
    assert "amount" in msg


def test_fill_na_all_columns_validates_each_numeric_column():
    """column=None + df misto: la validazione deve scattare se ALMENO
    una colonna numerica esiste e value non e' compatibile."""
    from app.core.manipola.transforms.base import op_fill_na

    df = pd.DataFrame({
        "label": ["a", None, "c"],
        "amount": [1.0, np.nan, 3.0],
    })
    # value="N/A" e' compatibile con label (object) ma NON con amount (float)
    with pytest.raises(ValueError, match="amount"):
        op_fill_na(df, column=None, method="value", value="N/A")


def test_fill_na_all_columns_with_compatible_value():
    """column=None + value=0 -> ok per colonne numeriche; le object/
    string vengono comunque riempite con 0 raw (comportamento pandas
    pre-esistente, non rompiamo questo path)."""
    from app.core.manipola.transforms.base import op_fill_na

    df = pd.DataFrame({
        "amount": [1.0, np.nan, 3.0],
        "qty":    pd.Series([10, 20, 30], dtype="int64"),
    })
    result = op_fill_na(df, column=None, method="value", value=0)
    assert pd.api.types.is_numeric_dtype(result["amount"])
    assert pd.api.types.is_integer_dtype(result["qty"])
    assert result["amount"].iloc[1] == 0


# 3. ffill/bfill restano invariati (regression check)


def test_fill_na_ffill_bfill_unchanged_by_c2_policy():
    """C2 modifica solo il path method='value'. Il forward/backward fill
    non legge il parametro `value`, quindi non deve scattare la
    validazione type-vs-dtype."""
    from app.core.manipola.transforms.base import op_fill_na

    df = pd.DataFrame({"amount": [1.0, np.nan, 3.0, np.nan]})
    # value="N/A" sarebbe ValueError con method='value'; con ffill viene
    # ignorato e il fill avanza.
    result = op_fill_na(df, column="amount", method="ffill", value="N/A")
    assert pd.api.types.is_float_dtype(result["amount"])
    assert result["amount"].tolist() == [1.0, 1.0, 3.0, 3.0]
