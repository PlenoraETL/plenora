"""
Contract test RB.#11 (2026-04-28) — TypeInferrer date detection.

BUG.md #11:
"""

from __future__ import annotations

import warnings

import pandas as pd
import pytest

from app.services.file_service import TypeInferrer


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# Casi positivi: classificati come 'date'


def test_infer_iso_date_only_returns_date():
    """ISO `YYYY-MM-DD` puro × 10 valori -> 'date'."""
    series = pd.Series(["2025-03-15"] * 10)
    assert TypeInferrer._infer_from_values(series) == 'date'


def test_infer_iso_datetime_returns_date():
    """ISO con time `YYYY-MM-DDTHH:MM:SS` × 10 -> 'date'."""
    series = pd.Series(["2025-03-15T10:30:00"] * 10)
    assert TypeInferrer._infer_from_values(series) == 'date'


def test_infer_iso_datetime_with_space_returns_date():
    """`YYYY-MM-DD HH:MM:SS` (postgres-style) × 10 -> 'date'."""
    series = pd.Series(["2025-03-15 10:30:00"] * 10)
    assert TypeInferrer._infer_from_values(series) == 'date'


def test_infer_italian_dd_slash_mm_slash_yyyy_returns_date():
    """Formato italiano `dd/mm/yyyy` × 10 -> 'date'."""
    series = pd.Series(["15/03/2025"] * 10)
    assert TypeInferrer._infer_from_values(series) == 'date'


def test_infer_italian_dd_dash_mm_dash_yyyy_returns_date():
    """Formato italiano con dashes `dd-mm-yyyy` × 10 -> 'date'."""
    series = pd.Series(["15-03-2025"] * 10)
    assert TypeInferrer._infer_from_values(series) == 'date'


# Casi negativi: classificati come 'str' (no falsi positivi)


def test_infer_us_mm_dd_yyyy_with_day_gt_12_is_not_date():
    """Date US-style `mm/dd/yyyy` con giorno > 12 NON matchano `%d/%m/%Y`
    (mese=15 invalido). Risultato: 'str'. Meglio non classificare che
    interpretare male su utenza italiana."""
    series = pd.Series(["03/15/2025"] * 10)
    assert TypeInferrer._infer_from_values(series) == 'str'


def test_infer_pseudo_numeric_with_dashes_is_not_date():
    """Stringhe semi-numeriche tipo `"42-3-1"` non matchano nessun formato
    esplicito (anno troppo corto, ambiguita`). Pre-fix dateutil le
    parsava liberalmente come date -> falso positivo eliminato."""
    series = pd.Series(["42-3-1"] * 10)
    assert TypeInferrer._infer_from_values(series) == 'str'


def test_infer_below_threshold_is_not_date():
    """Mix di 3 date ISO + 7 garbage su 10 -> sotto soglia 80% -> 'str'.
    La soglia previene classificazione 'date' su colonne miste o sporche."""
    series = pd.Series(
        ["2025-03-15", "2025-04-01", "2025-05-10"]  # 3 date valide
        + ["pippo", "qwerty", "123abc", "no", "vuoto", "x", "y"]  # 7 garbage
    )
    assert TypeInferrer._infer_from_values(series) == 'str'


# Anti-rumore: nessun UserWarning emesso


def test_no_pandas_user_warning_emitted_during_inference():
    """Pre-fix: pandas 3.x emetteva `UserWarning: Could not infer format ...`
    ad ogni invocazione. Post-fix: cascata di formati espliciti + `errors=coerce`
    -> nessun warning emesso, ne' su date valide ne' su input non-date."""
    series_date = pd.Series(["15/03/2025"] * 10)
    series_garbage = pd.Series(["42-3-1"] * 10)
    series_mixed = pd.Series(
        ["2025-03-15", "garbage", "2025-04-01"] + ["x"] * 7
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        TypeInferrer._infer_from_values(series_date)
        TypeInferrer._infer_from_values(series_garbage)
        TypeInferrer._infer_from_values(series_mixed)

    # Filtra UserWarning di pandas (escludi DeprecationWarning di altri lib)
    pandas_warnings = [
        w for w in caught
        if issubclass(w.category, UserWarning)
        and "format" in str(w.message).lower()
    ]
    assert pandas_warnings == [], (
        f"Atteso 0 UserWarning su parsing format, trovati: "
        f"{[(w.category.__name__, str(w.message)) for w in pandas_warnings]}"
    )


# Helper standalone: smoke test


def test_looks_like_date_column_empty_series_returns_false():
    """Sample vuoto -> False (no inference possibile)."""
    series = pd.Series([], dtype=object)
    assert TypeInferrer._looks_like_date_column(series) is False


def test_looks_like_date_column_all_null_returns_false():
    """Tutti null -> False (sample dropna vuoto)."""
    series = pd.Series([None, None, None])
    assert TypeInferrer._looks_like_date_column(series) is False
