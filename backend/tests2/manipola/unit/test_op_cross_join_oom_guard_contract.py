"""
Contract test BE-GO-READY-TRANSFORMATION-ENGINE-001-CROSS-JOIN-GUARD.

Lock il preflight row-count guard di `op_cross_join`:
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.core.resources.exceptions import MemoryLimitExceededError


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# 1. Config presence


def test_cross_join_max_output_rows_in_settings_defaults():
    """CROSS_JOIN_MAX_OUTPUT_ROWS must be present in Settings.DEFAULTS."""
    from app.config import Settings
    assert "CROSS_JOIN_MAX_OUTPUT_ROWS" in Settings.DEFAULTS
    assert isinstance(Settings.DEFAULTS["CROSS_JOIN_MAX_OUTPUT_ROWS"], int)
    assert Settings.DEFAULTS["CROSS_JOIN_MAX_OUTPUT_ROWS"] > 0


# 2. Guard rejects cross-join above cap


def test_cross_join_above_cap_raises_memory_limit_exceeded(monkeypatch):
    """Cross-join che produrrebbe |left| * |right| > cap solleva
    `MemoryLimitExceededError` con stage `manipola_cross_join`."""
    from app.config import settings as _settings
    from app.core.manipola.transforms.joins import op_cross_join, CROSS_JOIN_STAGE

    # Cap a 10 righe: 4 x 5 = 20 > 10 -> guard scatta.
    monkeypatch.setitem(_settings.DEFAULTS, "CROSS_JOIN_MAX_OUTPUT_ROWS", 10)

    left = pd.DataFrame({"a": [1, 2, 3, 4]})
    right = pd.DataFrame({"b": [10, 20, 30, 40, 50]})

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        op_cross_join([left, right])

    e = exc_info.value
    assert e.stage == CROSS_JOIN_STAGE == "manipola_cross_join"
    assert e.observed_bytes == 4 * 5  # 20 rows
    assert e.limit_bytes == 10
    assert e.path_basename == "cross_join_output"


# 3. Guard allows cross-join below cap


def test_cross_join_below_cap_passes(monkeypatch):
    """Cross-join valido (output rows <= cap) procede normalmente.

    Verifica `len(left) * len(right) == len(result)` (Cartesian product
    cardinality preservato)."""
    from app.config import settings as _settings
    from app.core.manipola.transforms.joins import op_cross_join

    monkeypatch.setitem(_settings.DEFAULTS, "CROSS_JOIN_MAX_OUTPUT_ROWS", 1_000_000)

    left = pd.DataFrame({"a": [1, 2, 3]})
    right = pd.DataFrame({"b": ["x", "y"]})

    result = op_cross_join([left, right])
    assert len(result) == 6  # 3 x 2
    assert set(result.columns) >= {"a", "b"}


# 4. Cap <= 0 disables guard (escape hatch)


def test_cross_join_cap_zero_disables_guard(monkeypatch):
    """Cap = 0 -> guard inattivo. Cross-join procede anche se la
    cardinalita' sarebbe formalmente "oltre cap"."""
    from app.config import settings as _settings
    from app.core.manipola.transforms.joins import op_cross_join

    monkeypatch.setitem(_settings.DEFAULTS, "CROSS_JOIN_MAX_OUTPUT_ROWS", 0)

    left = pd.DataFrame({"a": [1, 2]})
    right = pd.DataFrame({"b": [10, 20, 30]})

    result = op_cross_join([left, right])
    assert len(result) == 6


def test_cross_join_cap_negative_disables_guard(monkeypatch):
    """Cap negativo -> guard inattivo (alias di disabled)."""
    from app.config import settings as _settings
    from app.core.manipola.transforms.joins import op_cross_join

    monkeypatch.setitem(_settings.DEFAULTS, "CROSS_JOIN_MAX_OUTPUT_ROWS", -1)

    left = pd.DataFrame({"a": [1, 2]})
    right = pd.DataFrame({"b": [10, 20]})

    result = op_cross_join([left, right])
    assert len(result) == 4


# 5. Edge: empty / single-input keep historical fail-soft


def test_cross_join_empty_input_returns_empty_no_guard(monkeypatch):
    """Input vuoto: comportamento storico preservato (return DataFrame()).
    Il guard non scatta — non c'e' cardinality da stimare."""
    from app.config import settings as _settings
    from app.core.manipola.transforms.joins import op_cross_join

    monkeypatch.setitem(_settings.DEFAULTS, "CROSS_JOIN_MAX_OUTPUT_ROWS", 1)

    result = op_cross_join([])
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0


def test_cross_join_single_input_returns_first_no_guard(monkeypatch):
    """Singolo input: comportamento storico preservato (return dfs[0]).
    Il guard non scatta — fail-soft warning gia' loggato dal codice."""
    from app.config import settings as _settings
    from app.core.manipola.transforms.joins import op_cross_join

    monkeypatch.setitem(_settings.DEFAULTS, "CROSS_JOIN_MAX_OUTPUT_ROWS", 1)

    only = pd.DataFrame({"a": [1, 2, 3]})
    result = op_cross_join([only])
    assert len(result) == 3


# 6. Non-cross join NOT impacted by the guard


def test_op_join_not_impacted_by_cross_join_cap(monkeypatch):
    """`op_join` (inner/left/right/outer) non triggera il preflight
    cross-join guard, anche con cap = 1. Lock dell'invariante
    "preserve non-cross join behavior exactly"."""
    from app.config import settings as _settings
    from app.core.manipola.transforms.joins import op_join

    monkeypatch.setitem(_settings.DEFAULTS, "CROSS_JOIN_MAX_OUTPUT_ROWS", 1)

    left = pd.DataFrame({"k": [1, 2, 3], "x": ["a", "b", "c"]})
    right = pd.DataFrame({"k": [1, 2, 3], "y": [10, 20, 30]})

    result = op_join([left, right], left_on="k", right_on="k", how="inner")
    assert len(result) == 3


# 7. Source-bound: handler in main.py routes the new stage


def test_main_py_recognizes_manipola_cross_join_stage():
    """`app/main.py` deve mappare `manipola_cross_join` a
    `_ROW_COUNT_STAGES` con `suggested_action="reduce_inputs_or_raise_cap"`.

    Membership-based: il set puo' contenere stage aggiuntivi
    (es. `manipola_multi_join` da batch 002).
    """
    main_py = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "app" / "main.py"
    )
    src = main_py.read_text(encoding="utf-8")
    # Stage routing — `manipola_cross_join` membership in _ROW_COUNT_STAGES
    assert "_ROW_COUNT_STAGES" in src
    assert '"manipola_cross_join"' in src
    # Action label per row-count
    assert 'suggested_action = "reduce_inputs_or_raise_cap"' in src
    # Locked: status code split unchanged from batch 002 contract
    assert "status_code = 503 if is_rss else 413" in src
    # Locked: original suggested_action ternary still present (batch 002)
    assert (
        'suggested_action = "retry_later_or_contact_admin" if is_rss '
        'else "split_file_or_contact_admin"' in src
    )


def test_main_py_renders_row_count_message_for_cross_join():
    """Source-bound: il branch `is_row_count` in main.py compone un
    messaggio row-count-aware (non MB) per cross-join (verbo
    `genererebbe` = preflight pre-merge)."""
    main_py = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "app" / "main.py"
    )
    src = main_py.read_text(encoding="utf-8")
    # Il messaggio cross-join usa verbo `genererebbe` (preflight).
    assert '"genererebbe"' in src
    assert "exc.observed_bytes" in src
    assert "exc.limit_bytes" in src
    # Narrative parametrica row-count: stage + righe.
    assert "Lo stage" in src
    assert "righe" in src


def test_main_py_adds_row_count_keys_to_details_for_row_count_stages():
    """Source-bound: per row-count stages, details include
    `limit_rows` e `observed_rows` (additive — le 7 chiavi base
    restano stabili)."""
    main_py = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "app" / "main.py"
    )
    src = main_py.read_text(encoding="utf-8")
    assert 'details["limit_rows"] = exc.limit_bytes' in src
    assert 'details["observed_rows"] = exc.observed_bytes' in src
