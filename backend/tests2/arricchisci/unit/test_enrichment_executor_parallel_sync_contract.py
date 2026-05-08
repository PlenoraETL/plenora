"""
Contract test per il path parallelo sync di EnrichmentExecutor.

Il dispatcher production usa ThreadPoolExecutor quando parallel_workers > 1.
"""

import threading
from typing import Any, List, Tuple

import pandas as pd
import pytest

from app.core.arricchisci.enrichment_service import EnrichmentExecutor
from app.core.arricchisci.models import (
    EnrichmentOptions,
    RowError,
    RowStatus,
)


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci]


def _make_executor(
    *,
    rate_limit: float = 0.0,
    parallel_workers: int = 5,
    max_retries: int = 0,
    enable_deduplication: bool = True,
    skip_null_params: bool = True,
    stop_on_error_threshold: int = 0,
    continue_on_error: bool = True,
    progress_callback=None,
) -> EnrichmentExecutor:
    api_config = {
        "name": "TestAPI",
        "endpoint": {"url": "https://api.test/lookup", "method": "GET"},
        "auth": {},
        "output_mapping": [{"path": "result", "column": "result"}],
    }
    options = EnrichmentOptions(
        validate_input=False,
        enable_deduplication=enable_deduplication,
        rate_limit=rate_limit,
        parallel_workers=parallel_workers,
        max_retries=max_retries,
        skip_null_params=skip_null_params,
        stop_on_error_threshold=stop_on_error_threshold,
        continue_on_error=continue_on_error,
    )
    return EnrichmentExecutor(
        api_config=api_config,
        param_mapping={"q": "q"},
        options=options,
        progress_callback=progress_callback,
    )


def _install_fake_call(executor, fn):
    executor._call_api_with_retry = fn


def test_parallel_sync_happy_path_no_dedup():
    df = pd.DataFrame({"q": [f"v{i}" for i in range(10)]})

    def fake_call(params, row_index):
        return {"result": f"out-{params.get('q')}"}, None

    executor = _make_executor(enable_deduplication=False)
    _install_fake_call(executor, fake_call)

    result_df, stats, errors = executor._execute_parallel(df)

    assert list(result_df["result"]) == [f"out-v{i}" for i in range(10)]
    assert stats.success == 10
    assert stats.errors == 0
    assert errors == []


def test_parallel_sync_deduplicates_identical_params():
    values = ["a", "b", "c", "a", "b", "c", "a", "b", "c", "a"]
    df = pd.DataFrame({"q": values})
    calls = []

    def fake_call(params, row_index):
        calls.append(params.get("q"))
        return {"result": f"out-{params.get('q')}"}, None

    executor = _make_executor()
    _install_fake_call(executor, fake_call)

    result_df, stats, errors = executor._execute_parallel(df)

    assert sorted(calls) == ["a", "b", "c"]
    assert list(result_df["result"]) == [f"out-{value}" for value in values]
    assert stats.errors == 0
    assert errors == []


def test_parallel_sync_single_group_makes_one_api_call():
    df = pd.DataFrame({"q": ["same"] * 20})
    call_count = [0]
    call_lock = threading.Lock()

    def fake_call(params, row_index):
        with call_lock:
            call_count[0] += 1
        return {"result": "ok"}, None

    executor = _make_executor()
    _install_fake_call(executor, fake_call)

    executor._execute_parallel(df)

    assert call_count[0] == 1


def test_parallel_sync_rate_limiter_invoked_per_api_call():
    df = pd.DataFrame({"q": [f"v{i}" for i in range(10)]})

    def fake_call(params, row_index):
        return {"result": "ok"}, None

    executor = _make_executor(enable_deduplication=False)
    _install_fake_call(executor, fake_call)
    acquire_count = [0]
    acquire_lock = threading.Lock()
    original_acquire = executor._rate_limiter.acquire

    def counted_acquire():
        with acquire_lock:
            acquire_count[0] += 1
        original_acquire()

    executor._rate_limiter.acquire = counted_acquire

    executor._execute_parallel(df)

    assert acquire_count[0] == 10


def test_parallel_sync_skip_null_params():
    df = pd.DataFrame({"q": ["a", None, "b", "", "c"]})

    def fake_call(params, row_index):
        return {"result": "ok"}, None

    executor = _make_executor()
    _install_fake_call(executor, fake_call)

    _, stats, errors = executor._execute_parallel(df)

    assert stats.skipped == 2
    assert errors == []


def test_parallel_sync_error_granularity_per_row_in_group():
    df = pd.DataFrame({"q": ["same"] * 20})

    def fake_call(params, row_index):
        return None, RowError(
            row_index=row_index,
            status=RowStatus.ERROR,
            error_message="API 503",
            error_type="http_5xx",
            http_status=503,
            retry_count=3,
        )

    executor = _make_executor()
    _install_fake_call(executor, fake_call)

    _, stats, errors = executor._execute_parallel(df)

    assert stats.errors == 20
    assert len(errors) == 20
    assert all(e.http_status == 503 for e in errors)
    assert all(e.retry_count == 3 for e in errors)


def test_parallel_sync_progress_callback_invoked():
    df = pd.DataFrame({"q": [f"v{i}" for i in range(10)]})

    def fake_call(params, row_index):
        return {"result": "ok"}, None

    calls: List[Tuple[Any, ...]] = []

    def callback(*args, **kwargs):
        calls.append(args)

    executor = _make_executor(
        enable_deduplication=False,
        progress_callback=callback,
    )
    _install_fake_call(executor, fake_call)

    executor._execute_parallel(df)

    assert calls
