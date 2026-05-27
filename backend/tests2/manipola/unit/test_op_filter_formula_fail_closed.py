"""
Contract: op_filter / op_formula fail closed on unsafe transform errors.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.core.manipola.transforms.filtering import op_filter
from app.core.manipola.transforms.formula import op_formula


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]


def test_op_filter_incompatible_operator_value_raises_value_error(monkeypatch):
    """Runtime error pandas nel blocco operator -> catturato W2 -> `raise
    ValueError` con messaggio safe user-facing (NO str(e) pandas interpolato).

    Mock di `pandas.DataFrame.__getitem__` (o altro hook interno) e' fragile;
    uso monkeypatch diretto su `pd.Series.__gt__` per forzare TypeError in
    modo deterministico a prescindere da versione pandas e auto-convert."""

    def failing_gt(self, other):
        raise TypeError("simulated: '>' not supported between instances")

    monkeypatch.setattr(pd.Series, "__gt__", failing_gt)

    df = pd.DataFrame({"prezzo": [1.0, 2.0, 3.0]})

    with pytest.raises(ValueError) as exc_info:
        op_filter(df, column="prezzo", operator=">", value=42)

    msg = str(exc_info.value)
    # Messaggio safe contiene identifier user-facing
    assert "Filter" in msg
    assert "prezzo" in msg
    assert ">" in msg
    assert "Verificare tipo e sintassi" in msg
    # Chain preservata via `from e`
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, TypeError)


def test_op_filter_happy_path_preserved():
    """Anti-regressione: filtro numerico valido â†’ df filtrato correttamente,
    NO raise. Il fix non deve toccare il happy path."""
    df = pd.DataFrame({"a": [1, 2, 3, 4], "b": [10, 20, 30, 40]})
    result = op_filter(df, column="a", operator=">", value=2)

    assert len(result) == 2
    assert list(result["a"]) == [3, 4]


# SITO W3 â€” op_formula fail-closed su formula invalida / AST-blocked


def test_op_formula_invalid_formula_raises_value_error():
    """Formula che riferisce colonna inesistente â†’ runtime error eval
    namespace â†’ catturato W3 â†’ `raise ValueError` con messaggio safe.
    No leak di traceback pandas/eval internals."""
    df = pd.DataFrame({"a": [1, 2, 3], "b": [10, 20, 30]})

    with pytest.raises(ValueError) as exc_info:
        # Formula con colonna inesistente â†’ NameError dentro eval â†’
        # catturato da W3 broad except â†’ raise ValueError
        op_formula(df, new_column="calc", formula="colonna_inesistente + 1")

    msg = str(exc_info.value)
    # Messaggio safe contiene formula + canonical hint
    assert "Formula" in msg
    assert "colonna_inesistente" in msg
    assert "non valida o non consentita" in msg
    assert "Verificare sintassi" in msg
    # Chain preservata via `from e`
    assert exc_info.value.__cause__ is not None


def test_op_formula_happy_path_preserved():
    """Anti-regressione: formula valida `a + b` â†’ df con new_col calcolata,
    NO raise. Il fix non deve toccare il happy path."""
    df = pd.DataFrame({"a": [1, 2, 3], "b": [10, 20, 30]})
    result = op_formula(df, new_column="somma", formula="a + b")

    assert "somma" in result.columns
    assert list(result["somma"]) == [11, 22, 33]
