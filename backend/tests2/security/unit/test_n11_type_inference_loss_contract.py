"""
Contract test N11 — ColumnMapper preserva int quando pandas promuove a float.

Scenario reale:
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def _mapper():
    from app.core.carica.column_mapper import ColumnMapper
    # db_type non rilevante per inference: usa un any valid
    return ColumnMapper(db_type="postgres")


# 1. Caso specifico del finding: int promosso a float per NaN -> rilevato come int


def test_float_col_with_all_integer_values_classified_as_int():
    """pandas float64 con valori tutti integer (tipico dopo LEFT JOIN)."""
    m = _mapper()
    series = pd.Series([1.0, 2.0, 3.0], dtype=float)
    assert m._infer_type_from_pandas(series) == "int"


def test_float_col_with_nan_but_rest_integer_classified_as_int():
    """Caso principale del finding: int promoted to float because of NaN."""
    m = _mapper()
    series = pd.Series([1.0, np.nan, 3.0, 4.0], dtype=float)
    assert m._infer_type_from_pandas(series) == "int"


def test_float_col_all_nan_falls_back_to_real():
    """Tutta NaN: nessun valore per decidere -> fallback conservativo real."""
    m = _mapper()
    series = pd.Series([np.nan, np.nan, np.nan], dtype=float)
    assert m._infer_type_from_pandas(series) == "real"


# 2. Sanity: float veri non diventano int


def test_float_col_with_fractional_values_stays_real():
    """Float con valori frazionari reali -> resta real (no false positive)."""
    m = _mapper()
    series = pd.Series([1.5, 2.7, 3.14], dtype=float)
    assert m._infer_type_from_pandas(series) == "real"


def test_float_col_mixed_integer_and_fractional_stays_real():
    """Anche un solo valore frazionario -> real."""
    m = _mapper()
    series = pd.Series([1.0, 2.0, 3.5, 4.0], dtype=float)
    assert m._infer_type_from_pandas(series) == "real"


# 3. Regressione: int puro resta int


def test_int_col_stays_int():
    m = _mapper()
    series = pd.Series([1, 2, 3], dtype="int64")
    assert m._infer_type_from_pandas(series) == "int"


def test_int64_col_with_pd_na_stays_int():
    """Pandas Int64 (nullable) -> resta int."""
    m = _mapper()
    series = pd.Series([1, pd.NA, 3], dtype="Int64")
    assert m._infer_type_from_pandas(series) == "int"


# 4. Regressione: altri tipi invariati


def test_bool_col_stays_bool():
    m = _mapper()
    assert m._infer_type_from_pandas(pd.Series([True, False], dtype=bool)) == "bool"


def test_datetime_col_stays_date():
    m = _mapper()
    s = pd.Series(pd.to_datetime(["2026-01-01", "2026-02-01"]))
    assert m._infer_type_from_pandas(s) == "date"


def test_object_col_with_strings_stays_str():
    m = _mapper()
    assert m._infer_type_from_pandas(pd.Series(["a", "b", "c"], dtype=object)) == "str"


def test_object_col_with_numeric_strings_becomes_real():
    """Pattern esistente: object con valori numerici -> real (CSV importato)."""
    m = _mapper()
    assert m._infer_type_from_pandas(pd.Series(["1.5", "2.7"], dtype=object)) == "real"


# 5. Policy esplicita: trade-off documentato


def test_policy_float_as_integer_may_mislabel_intentional_floats():
    """Documentazione esplicita della policy N11:
    Se un utente ha una colonna float intenzionale con valori che *sembrano*
    integer (es. rating 1.0, 2.0, 5.0 scritti come decimal), verranno
    classificati come int. Workaround: passare canvas_types espliciti.

    Questo test NON fallisce mai: documenta il trade-off per review futura."""
    m = _mapper()
    # Rating "float" che in realta' sono tutti integer
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], dtype=float)
    result = m._infer_type_from_pandas(series)
    # Policy attuale: -> int (se in futuro si preferisce preservare float
    # semantica a costo del bug int-promoted, cambiare e aggiornare il test)
    assert result == "int", (
        "Policy N11: float con tutti valori integer -> int. "
        "Se cambia, aggiorna questo test + docstring + policy docs."
    )
