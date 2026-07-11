"""Contratti del guard byte-aware per i merge MANIPOLA."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.resources.exceptions import MemoryLimitExceededError


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.manipola]


def test_estimate_merge_memory_uses_width_rows_and_overhead():
    from app.core.manipola.join_memory import estimate_merge_memory

    left = pd.DataFrame({"k": [1, 2], "x": [10, 20]})
    right = pd.DataFrame({"k": [1, 2], "y": [30, 40]})

    estimate = estimate_merge_memory(
        left,
        right,
        output_rows=3,
        worker_limit_bytes=10_000,
        current_rss_bytes=1_000,
    )

    assert estimate.left_rows == 2
    assert estimate.right_rows == 2
    assert estimate.left_row_width_bytes == 16
    assert estimate.right_row_width_bytes == 16
    assert estimate.output_row_width_bytes == 32
    assert estimate.output_payload_bytes == 96
    assert estimate.additional_allocation_bytes == 192
    assert estimate.current_rss_bytes == 1_000
    assert estimate.projected_peak_bytes == 1_192
    assert estimate.worker_limit_bytes == 10_000


def test_estimate_merge_memory_falls_back_to_input_bytes_without_rss():
    from app.core.manipola.join_memory import estimate_merge_memory

    left = pd.DataFrame({"k": [1, 2], "x": [10, 20]})
    right = pd.DataFrame({"k": [1, 2], "y": [30, 40]})

    estimate = estimate_merge_memory(
        left,
        right,
        output_rows=3,
        worker_limit_bytes=10_000,
        current_rss_bytes=None,
    )

    # Input residenti: 2 righe x 16 byte per lato = 64 byte.
    assert estimate.projected_peak_bytes == 64 + 192


def test_op_join_byte_guard_raises_before_merge(monkeypatch):
    from app.config import settings
    from app.core.manipola import join_memory
    from app.core.manipola.transforms import joins

    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 1_100)
    monkeypatch.setattr(join_memory, "get_current_rss_bytes", lambda: 1_000)

    def _merge_should_not_run(*_args, **_kwargs):
        raise AssertionError("merge materializzato nonostante il budget")

    monkeypatch.setattr(joins, "merge_dataframes", _merge_should_not_run)
    left = pd.DataFrame({"k": [1, 2, 3], "x": [10, 20, 30]})
    right = pd.DataFrame({"k": [1, 2, 3], "y": [40, 50, 60]})

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        joins.op_join([left, right], left_on="k", right_on="k", how="inner")

    exc = exc_info.value
    assert exc.stage == join_memory.JOIN_MEMORY_STAGE
    assert exc.path_basename == "join_output"
    assert exc.observed_bytes > exc.limit_bytes == 1_100


def test_cross_join_byte_guard_raises_before_merge(monkeypatch):
    from app.config import settings
    from app.core.manipola import join_memory
    from app.core.manipola.transforms import joins

    monkeypatch.setitem(settings.DEFAULTS, "CROSS_JOIN_MAX_OUTPUT_ROWS", 1_000)
    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 1_100)
    monkeypatch.setattr(join_memory, "get_current_rss_bytes", lambda: 1_000)

    def _merge_should_not_run(*_args, **_kwargs):
        raise AssertionError("cross merge materializzato nonostante il budget")

    monkeypatch.setattr(joins, "merge_dataframes", _merge_should_not_run)
    left = pd.DataFrame({"a": [1, 2, 3]})
    right = pd.DataFrame({"b": [10, 20]})

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        joins.op_cross_join([left, right])

    exc = exc_info.value
    assert exc.stage == join_memory.CROSS_JOIN_MEMORY_STAGE
    assert exc.path_basename == "cross_join_output"
    assert exc.observed_bytes > exc.limit_bytes == 1_100


def test_multi_join_byte_guard_raises_before_merge(monkeypatch):
    from app.config import settings
    from app.core.manipola import executor as executor_module
    from app.core.manipola import join_memory

    monkeypatch.setitem(settings.DEFAULTS, "MULTI_JOIN_MAX_OUTPUT_ROWS", 1_000)
    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 1_100)
    monkeypatch.setattr(join_memory, "get_current_rss_bytes", lambda: 1_000)

    def _merge_should_not_run(*_args, **_kwargs):
        raise AssertionError("multi merge materializzato nonostante il budget")

    monkeypatch.setattr(executor_module, "merge_dataframes", _merge_should_not_run)
    executor = executor_module.ManipolaExecutor.__new__(
        executor_module.ManipolaExecutor
    )
    node = SimpleNamespace(
        join_keys=["k"],
        join_pairs=[],
        left_keys=[],
        right_keys=[],
        multi_join_type="inner",
        data={"config": {}},
    )
    left = pd.DataFrame({"k": [1, 2, 3], "x": [10, 20, 30]})
    right = pd.DataFrame({"k": [1, 2, 3], "y": [40, 50, 60]})

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        executor._esegui_multi_join(node, [left, right])

    exc = exc_info.value
    assert exc.stage == join_memory.MULTI_JOIN_MEMORY_STAGE
    assert exc.path_basename == "multi_join_output"
    assert exc.observed_bytes > exc.limit_bytes == 1_100


def test_join_memory_guard_disabled_when_worker_cap_non_positive(monkeypatch):
    from app.config import settings
    from app.core.manipola.join_memory import guard_estimated_merge_memory

    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 0)
    left = pd.DataFrame({"k": [1]})
    right = pd.DataFrame({"k": [1]})

    assert guard_estimated_merge_memory(
        left,
        right,
        1_000_000,
        stage="test",
        path_basename="join_output",
    ) is None


def test_app_factory_has_join_memory_specific_error_message():
    app_factory = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "app"
        / "app_factory.py"
    )
    source = app_factory.read_text(encoding="utf-8")

    assert "_JOIN_MEMORY_STAGES" in source
    assert '"manipola_join_memory"' in source
    assert '"manipola_cross_join_memory"' in source
    assert '"manipola_multi_join_memory"' in source
    assert 'suggested_action = "reduce_join_inputs_or_worker_concurrency"' in source
