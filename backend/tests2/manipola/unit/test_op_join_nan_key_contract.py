"""
Contract test — op_join NaN key handling (C1 Phase 4).

Background:
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.manipola,
    pytest.mark.contract,
]


# Logging fixture — pattern allineato a test_n4_formula_validator_contract


class _ListHandler(logging.Handler):
    """Handler custom: colleziona LogRecord. Necessario perche' il logger
    di base.py potrebbe avere propagate=False (caplog non sempre arriva)."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def base_log_records():
    target = logging.getLogger("app.core.manipola.transforms.base")
    handler = _ListHandler()
    target.addHandler(handler)
    try:
        yield handler.records
    finally:
        target.removeHandler(handler)


def _nan_key_warnings(records):
    """Filtra solo i warning emessi dalla telemetria C1 (substring 'chiavi NaN')."""
    return [
        r for r in records
        if r.levelno >= logging.WARNING and "chiavi nan" in r.getMessage().lower()
    ]


# 1. Semantica NaN per how


def test_inner_join_excludes_rows_with_nan_keys():
    """inner + key NaN -> riga esclusa, no NaN==NaN match."""
    from app.core.manipola.transforms.base import op_join

    left = pd.DataFrame({"k": [1.0, 2.0, np.nan, 4.0], "lv": ["a", "b", "c", "d"]})
    right = pd.DataFrame({"k": [1.0, 2.0, 3.0, 4.0], "rv": ["x", "y", "z", "w"]})

    result = op_join([left, right], left_on="k", right_on="k", how="inner")
    # Solo le 3 chiavi non-NaN matchano (1, 2, 4); la riga con NaN e' esclusa.
    assert len(result) == 3
    assert sorted(result["lv_L"].tolist()) == ["a", "b", "d"]


def test_left_join_preserves_left_nan_keys_with_null_right_columns():
    """left + key NaN sul left -> riga preservata, colonne right = NaN."""
    from app.core.manipola.transforms.base import op_join

    left = pd.DataFrame({"k": [1.0, np.nan, 3.0], "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"k": [1.0, 2.0, 3.0], "rv": ["x", "y", "z"]})

    result = op_join([left, right], left_on="k", right_on="k", how="left")
    assert len(result) == 3
    nan_row = result[result["lv_L"] == "b"]
    assert len(nan_row) == 1
    assert nan_row["rv_R"].isna().all()


def test_right_join_preserves_right_nan_keys_symmetric_to_left():
    """right + key NaN sul right -> riga preservata, colonne left = NaN."""
    from app.core.manipola.transforms.base import op_join

    left = pd.DataFrame({"k": [1.0, 2.0, 3.0], "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"k": [1.0, np.nan, 3.0], "rv": ["x", "y", "z"]})

    result = op_join([left, right], left_on="k", right_on="k", how="right")
    assert len(result) == 3
    nan_row = result[result["rv_R"] == "y"]
    assert len(nan_row) == 1
    assert nan_row["lv_L"].isna().all()


def test_outer_join_separates_nan_on_both_sides_no_cross_match():
    """outer + NaN su entrambi i lati -> righe separate, mai matchate
    fra loro (no cross-NaN match)."""
    from app.core.manipola.transforms.base import op_join

    left = pd.DataFrame({"k": [1.0, np.nan], "lv": ["a", "b"]})
    right = pd.DataFrame({"k": [1.0, np.nan], "rv": ["x", "y"]})

    result = op_join([left, right], left_on="k", right_on="k", how="outer")
    # 1 match (k=1) + 1 left-NaN (lv='b', rv=NaN) + 1 right-NaN (lv=NaN, rv='y')
    assert len(result) == 3
    matched = result[(result["lv_L"] == "b") & (result["rv_R"] == "y")]
    assert len(matched) == 0, (
        "NaN left e NaN right NON devono matchare fra loro: pandas tratta "
        "NaN!=NaN per design."
    )


# 2. Anti-stringificazione: stringhe legittime vs veri NaN


def test_legitimate_nan_string_matches_on_both_sides_when_dtypes_align():
    """Stringa letterale 'nan' su entrambi i lati (dtype object identico,
    no coercion path) -> deve matchare come valore reale. Stato pre-C1
    gia' funzionante, blindato qui per regressione."""
    from app.core.manipola.transforms.base import op_join

    left = pd.DataFrame({"k": ["a", "nan", "c"], "lv": [1, 2, 3]})
    right = pd.DataFrame({"k": ["nan", "c", "d"], "rv": [10, 20, 30]})

    result = op_join([left, right], left_on="k", right_on="k", how="inner")
    # 'nan' (entrambi) e 'c' matchano. 2 righe.
    assert len(result) == 2
    nan_match = result[result["lv_L"] == 2]
    assert len(nan_match) == 1
    assert (nan_match["rv_R"] == 10).all()


def test_legitimate_nan_string_preserved_through_dtype_mismatch_coercion():
    """Stringa letterale 'nan' su un lato, valori numerici sull'altro: la
    coercion clean_to_str NON deve trasformare la stringa in None.
    Pre-C1: la stringa "nan" finiva nulla -> esclusa. Post-C1: preservata."""
    from app.core.manipola.transforms.base import op_join

    # Left object con stringhe (incluso il letterale "nan").
    left = pd.DataFrame({"k": ["1", "nan", "3"], "lv": ["a", "b", "c"]})
    # Right numeric (no NaN reale).
    right = pd.DataFrame({"k": [1, 2, 3], "rv": ["x", "y", "z"]})

    result = op_join([left, right], left_on="k", right_on="k", how="left")
    # Tutte le righe left preservate (left join).
    assert len(result) == 3
    # La riga "nan" deve essere preservata; non matcha right (right ha 2, non
    # "nan"), quindi rv_R = NaN.
    nan_row = result[result["lv_L"] == "b"]
    assert len(nan_row) == 1
    assert nan_row["rv_R"].isna().all(), (
        "La stringa 'nan' su left deve restare valore reale: non matcha 2, "
        "rv_R = NaN per quel record. Pre-C1 invece la stringa veniva "
        "cancellata e il record veniva contato come key-null."
    )


def test_real_nan_does_not_match_legitimate_nan_string_across_sides():
    """Vero NaN da una parte + stringa 'nan' dall'altra -> NON match.
    Caso canonico del bug pre-C1: la coercion stringificava il NaN come
    'nan' (object cleanup invertito) e lo joinava col 'nan' string sul
    lato opposto, producendo match falso."""
    from app.core.manipola.transforms.base import op_join

    # Left object con stringa "nan" reale (no missing).
    left = pd.DataFrame(
        {"k": pd.array(["a", "nan", "c"], dtype="object"), "lv": [1, 2, 3]}
    )
    # Right object con vero NaN.
    right = pd.DataFrame(
        {"k": pd.array(["a", np.nan, "c"], dtype="object"), "rv": [10, 20, 30]}
    )

    result = op_join([left, right], left_on="k", right_on="k", how="inner")
    # Atteso: 'a' e 'c' matchano. 'nan' string (lv=2) vs NaN (rv=20): no match.
    assert len(result) == 2
    matched_lv = sorted(result["lv_L"].tolist())
    assert matched_lv == [1, 3]


def test_real_nan_in_int64_does_not_collide_with_na_string_after_coercion():
    """Left Int64 con NaN, right object con stringa '<NA>' legittima:
    il roundtrip astype('Int64').astype(str) produce '<NA>' per i null, ma
    la mask C1 li riconverte a pd.NA. La stringa '<NA>' a destra resta
    valore reale e NON matcha il null sinistro."""
    from app.core.manipola.transforms.base import op_join

    left = pd.DataFrame(
        {"k": pd.array([1, pd.NA, 3], dtype="Int64"), "lv": ["a", "b", "c"]}
    )
    right = pd.DataFrame({"k": ["1", "<NA>", "3"], "rv": ["x", "y", "z"]})

    result = op_join([left, right], left_on="k", right_on="k", how="inner")
    # Solo k=1 e k=3 matchano. Il NaN sinistro NON deve matchare la stringa
    # '<NA>' sul right (regressione della coercion sloppy pre-C1).
    assert len(result) == 2
    matched_lv = sorted(result["lv_L"].tolist())
    assert matched_lv == ["a", "c"]


# 3. Compatibilita' coercion numeric/object con valori validi


def test_dtype_mismatch_numeric_object_with_valid_values_still_matches():
    """Pre-C1 funzionava: id 1,2,3 (int) vs '1','2','3' (string).
    La rewrite C1 di clean_to_str preserva esattamente il path int -> '1'
    via Int64 nullable, quindi il match deve continuare a riuscire."""
    from app.core.manipola.transforms.base import op_join

    left = pd.DataFrame({"k": [1, 2, 3], "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"k": ["1", "2", "3"], "rv": ["x", "y", "z"]})

    result = op_join([left, right], left_on="k", right_on="k", how="inner")
    assert len(result) == 3
    assert sorted(result["lv_L"].tolist()) == ["a", "b", "c"]


def test_dtype_mismatch_both_numeric_uses_to_numeric_coerce():
    """Path "entrambi numeric ma dtype diversi" (es. int vs float):
    pd.to_numeric con errors='coerce'. Valori validi matchano normalmente."""
    from app.core.manipola.transforms.base import op_join

    left = pd.DataFrame({"k": pd.array([1, 2, 3], dtype="int32"), "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"k": pd.array([1.0, 2.0, 3.0], dtype="float64"), "rv": ["x", "y", "z"]})

    result = op_join([left, right], left_on="k", right_on="k", how="inner")
    assert len(result) == 3


# 4. Telemetria warning


def test_warning_emitted_when_left_has_nan_keys(base_log_records):
    """key-null count > 0 -> WARNING con how, left_on/right_on, conteggi."""
    from app.core.manipola.transforms.base import op_join

    left = pd.DataFrame({"k": [1.0, np.nan, 3.0], "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"k": [1.0, 2.0, 3.0], "rv": ["x", "y", "z"]})

    op_join([left, right], left_on="k", right_on="k", how="left")

    warnings = _nan_key_warnings(base_log_records)
    assert len(warnings) >= 1, "atteso un WARNING di telemetria sulle chiavi NaN"
    msg = warnings[0].getMessage()
    assert "left" in msg.lower() or "Join left" in msg
    assert "1" in msg, "il warning deve riportare il count (1 chiave NaN sinistra)"


def test_warning_emitted_when_right_has_nan_keys(base_log_records):
    """Telemetria simmetrica: NaN sul right -> warning."""
    from app.core.manipola.transforms.base import op_join

    left = pd.DataFrame({"k": [1.0, 2.0, 3.0], "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"k": [1.0, np.nan, 3.0], "rv": ["x", "y", "z"]})

    op_join([left, right], left_on="k", right_on="k", how="right")

    warnings = _nan_key_warnings(base_log_records)
    assert len(warnings) >= 1


def test_no_warning_when_keys_are_clean(base_log_records):
    """key-null count == 0 -> nessun warning di telemetria (no log spam)."""
    from app.core.manipola.transforms.base import op_join

    left = pd.DataFrame({"k": [1.0, 2.0, 3.0], "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"k": [1.0, 2.0, 3.0], "rv": ["x", "y", "z"]})

    op_join([left, right], left_on="k", right_on="k", how="inner")

    warnings = _nan_key_warnings(base_log_records)
    assert warnings == [], (
        f"con chiavi pulite non deve essere emesso il warning di telemetria; "
        f"trovati: {[r.getMessage() for r in warnings]}"
    )
