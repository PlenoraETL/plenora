"""
Contract tests for BE-GO-READY-TRANSFORMATION-ENGINE-002.

Locks the multi-join row-count guard:
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.resources.exceptions import MemoryLimitExceededError


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _make_node(join_keys, join_type="inner"):
    return SimpleNamespace(
        join_keys=join_keys,
        multi_join_type=join_type,
        data={"config": {}},
    )


def _make_executor():
    from app.core.manipola.executor import ManipolaExecutor

    return ManipolaExecutor.__new__(ManipolaExecutor)


def test_multi_join_max_output_rows_in_settings_defaults():
    from app.config import Settings

    assert "MULTI_JOIN_MAX_OUTPUT_ROWS" in Settings.DEFAULTS
    assert isinstance(Settings.DEFAULTS["MULTI_JOIN_MAX_OUTPUT_ROWS"], int)
    assert Settings.DEFAULTS["MULTI_JOIN_MAX_OUTPUT_ROWS"] > 0


def test_multi_join_stage_constant_value():
    from app.core.manipola.executor import MULTI_JOIN_STAGE

    assert MULTI_JOIN_STAGE == "manipola_multi_join"


def test_multi_join_above_cap_raises_before_materialization(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setitem(_settings.DEFAULTS, "MULTI_JOIN_MAX_OUTPUT_ROWS", 5)

    a = pd.DataFrame({"k": [1, 1, 1], "x": ["a", "b", "c"]})
    b = pd.DataFrame({"k": [1, 1, 1], "y": [10, 20, 30]})

    def _merge_should_not_run(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("pd.merge should not run when estimate exceeds cap")

    monkeypatch.setattr(pd, "merge", _merge_should_not_run)

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        _make_executor()._esegui_multi_join(_make_node(["k"]), [a, b])

    exc = exc_info.value
    assert exc.stage == "manipola_multi_join"
    assert exc.path_basename == "multi_join_output"
    assert exc.observed_bytes == 9
    assert exc.limit_bytes == 5


def test_multi_join_unique_keys_not_blocked_by_cartesian_worst_case(monkeypatch):
    """1k x 1k unique-key join has cartesian worst-case 1M but real output 1k."""
    from app.config import settings as _settings

    monkeypatch.setitem(_settings.DEFAULTS, "MULTI_JOIN_MAX_OUTPUT_ROWS", 2_000)

    a = pd.DataFrame({"k": list(range(1_000)), "x": list(range(1_000))})
    b = pd.DataFrame({"k": list(range(1_000)), "y": list(range(1_000))})

    result = _make_executor()._esegui_multi_join(_make_node(["k"]), [a, b])

    assert len(result) == 1_000


def test_multi_join_left_join_estimate_counts_unmatched_left_rows(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setitem(_settings.DEFAULTS, "MULTI_JOIN_MAX_OUTPUT_ROWS", 4)

    left = pd.DataFrame({"k": [1, 1, 2, 3], "x": [1, 2, 3, 4]})
    right = pd.DataFrame({"k": [1, 1], "y": [10, 20]})

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        _make_executor()._esegui_multi_join(
            _make_node(["k"], join_type="left"), [left, right]
        )

    # key=1 -> 2*2 rows, plus unmatched key 2 and 3 -> 6 rows.
    assert exc_info.value.observed_bytes == 6


def test_multi_join_below_cap_passes(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setitem(_settings.DEFAULTS, "MULTI_JOIN_MAX_OUTPUT_ROWS", 1_000_000)

    a = pd.DataFrame({"k": [1, 2, 3], "x": ["a", "b", "c"]})
    b = pd.DataFrame({"k": [1, 2, 3], "y": [10, 20, 30]})
    c = pd.DataFrame({"k": [1, 2, 3], "z": [100, 200, 300]})

    result = _make_executor()._esegui_multi_join(
        _make_node(["k"], join_type="inner"), [a, b, c]
    )

    assert len(result) == 3
    assert {"k", "x", "y", "z"}.issubset(set(result.columns))


def test_multi_join_cap_zero_disables_guard(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setitem(_settings.DEFAULTS, "MULTI_JOIN_MAX_OUTPUT_ROWS", 0)

    a = pd.DataFrame({"k": [1, 1, 1], "x": ["a", "b", "c"]})
    b = pd.DataFrame({"k": [1, 1, 1], "y": [10, 20, 30]})

    result = _make_executor()._esegui_multi_join(_make_node(["k"]), [a, b])

    assert len(result) == 9


def test_multi_join_cap_negative_disables_guard(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setitem(_settings.DEFAULTS, "MULTI_JOIN_MAX_OUTPUT_ROWS", -1)

    a = pd.DataFrame({"k": [1, 1], "x": ["a", "b"]})
    b = pd.DataFrame({"k": [1, 1], "y": [10, 20]})

    result = _make_executor()._esegui_multi_join(_make_node(["k"]), [a, b])

    assert len(result) == 4


def test_multi_join_base_input_already_above_cap_raises(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setitem(_settings.DEFAULTS, "MULTI_JOIN_MAX_OUTPUT_ROWS", 2)

    big = pd.DataFrame({"k": [1, 2, 3, 4, 5], "x": list("abcde")})
    small = pd.DataFrame({"k": [1, 2, 3], "y": [10, 20, 30]})

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        _make_executor()._esegui_multi_join(_make_node(["k"]), [big, small])

    assert exc_info.value.observed_bytes == 5
    assert exc_info.value.limit_bytes == 2


def test_multi_join_single_input_returns_first_no_guard(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setitem(_settings.DEFAULTS, "MULTI_JOIN_MAX_OUTPUT_ROWS", 1)

    only = pd.DataFrame({"k": [1, 2, 3]})
    result = _make_executor()._esegui_multi_join(_make_node(["k"]), [only])

    assert len(result) == 3


def test_multi_join_missing_keys_returns_first_no_guard(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setitem(_settings.DEFAULTS, "MULTI_JOIN_MAX_OUTPUT_ROWS", 1)

    a = pd.DataFrame({"k": [1, 2]})
    b = pd.DataFrame({"different": [10, 20]})

    result = _make_executor()._esegui_multi_join(_make_node(["k"]), [a, b])

    assert len(result) == 2


def test_executor_source_has_estimate_before_merge():
    src_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "app" / "core" / "manipola" / "executor.py"
    )
    src = src_path.read_text(encoding="utf-8")
    estimate_pos = src.index("_estimate_merge_output_rows(")
    merge_pos = src.index("result = merge_dataframes(")

    assert 'MULTI_JOIN_STAGE = "manipola_multi_join"' in src
    assert 'settings.get("MULTI_JOIN_MAX_OUTPUT_ROWS"' in src
    assert estimate_pos < merge_pos
    assert "MemoryLimitExceededError" in src
    assert '"multi_join_output"' in src


def test_app_factory_recognizes_manipola_multi_join_stage():
    app_factory_py = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "app" / "app_factory.py"
    )
    src = app_factory_py.read_text(encoding="utf-8")
    assert "_ROW_COUNT_STAGES" in src
    assert '"manipola_multi_join"' in src
    assert '"manipola_cross_join"' in src


def test_app_factory_multi_join_message_has_specific_hint():
    app_factory_py = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "app" / "app_factory.py"
    )
    src = app_factory_py.read_text(encoding="utf-8")
    assert '"genererebbe"' in src
    assert "chiavi non-unique" in src
    assert 'exc.stage == "manipola_multi_join"' in src
