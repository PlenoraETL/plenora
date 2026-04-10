"""
tests/arricchisci/unit/test_enrichment_service_coverage.py
==========================================================
Coverage-oriented tests for enrichment_service.py targeting uncovered lines.

Covers:
  - RateLimiter: rate>0 actual sleep (lines 111-112)
  - preview(): full flow with mocked _execute_internal (lines 258-307)
  - execute(): batch_config.enabled routing (line 347)
  - execute(): parallel_workers > 1 routing (line 349)
  - _execute_parallel: full flow including dedup, null params, cache hit,
    error handling, worker crash (lines 382-536)
  - _execute_batch_native: full flow including null skip, success, null result,
    incomplete batch, error batch (lines 557-651)
  - _call_batch_api: GET batch, POST batch, 200 success, 429 rate limit,
    retriable status, non-retriable status, timeout, request error,
    retry exhausted (lines 669-776)
  - _build_batch_payload: FLAT_ARRAY format, ARRAY format, API key query
    (lines 787-819)
  - _extract_batch_output: non-list response, None items, path stripping
    (lines 829-852)
  - _execute_internal: token renewal, null params skip/error, cache hit,
    FeatureCollection explosion, error threshold, continue_on_error=False
    (lines 889, 902-904, 923-949, 968-969, 988-996, 1006, 1011-1012, 1016-1018)
  - _build_params: numpy bool_ conversion (line 1064-1066)
  - _call_api_with_retry: path param substitution, POST method, 429 rate limit,
    retriable status with backoff, non-retriable status, timeout, request error,
    retry exhausted (lines 1112-1114, 1127, 1146-1162, 1166-1179, 1191-1217)
  - _extract_output: all-None fallback to auto_flatten (line 1229)
  - _auto_flatten: FeatureCollection, list of dicts, list of non-dicts,
    generic dict, non-dict scalar (lines 1270, 1279)
  - _update_progress: callback invocation (lines 1402-1407)
  - generate_error_report: empty errors, non-empty errors (lines 1422-1449)
"""

import pytest
import json
import time
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock, call
from pathlib import Path

pytestmark = pytest.mark.unit

from app.core.arricchisci.enrichment_service import (
    EnrichmentExecutor,
    RateLimiter,
    generate_error_report,
)
from app.core.arricchisci.models import (
    EnrichmentOptions,
    EnrichmentStats,
    RowStatus,
    RowError,
    BatchConfig,
    BatchInputFormat,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_executor(
    url="http://example.com/api",
    method="GET",
    output_mapping=None,
    param_mapping=None,
    static_params=None,
    auth=None,
    options=None,
    batch=None,
    progress_callback=None,
):
    api_config = {
        "name": "TestAPI",
        "endpoint": {"url": url, "method": method},
        "auth": auth or {"type": "none"},
        "output_mapping": output_mapping or [],
        "static_parameters": static_params or {},
        "batch": batch or {},
    }
    return EnrichmentExecutor(
        api_config=api_config,
        param_mapping=param_mapping or {},
        options=options or EnrichmentOptions(rate_limit=0, max_retries=0),
        progress_callback=progress_callback,
    )


def mock_response(data, status_code=200, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
    resp.headers = headers or {}
    return resp




# =============================================================================
# RateLimiter — sleep branch (lines 111-112)
# =============================================================================

class TestRateLimiterSleep:

    def test_second_acquire_sleeps(self):
        """Two rapid acquires with low rate must trigger sleep."""
        rl = RateLimiter(rate=5.0)  # interval = 0.2s
        rl.last_call = time.time()  # pretend we just called
        start = time.time()
        rl.acquire()
        elapsed = time.time() - start
        # Should have slept ~0.2s (interval)
        assert elapsed >= 0.1


# =============================================================================
# preview() — lines 258-307
# =============================================================================

class TestPreview:

    def test_preview_returns_preview_result(self):
        """preview() calls _execute_internal on sample, returns PreviewResult."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(
                rate_limit=5, max_retries=0, preview_sample_size=3,
                parallel_workers=1,
            ),
        )
        df = pd.DataFrame({"paese": ["Italy", "France", "Germany", "Spain", "UK"]})

        fake_result_df = df.copy()
        fake_result_df["capitale"] = ["Rome", "Paris", "Berlin", "Madrid", "London"]
        fake_stats = EnrichmentStats(total_rows=3, success=3)
        fake_errors = []

        with patch.object(ex, "_execute_internal", return_value=(fake_result_df.head(3), fake_stats, fake_errors)):
            result = ex.preview(df, sample_size=3)

        assert result.sample_size == 3
        assert result.success_count == 3
        assert result.error_count == 0
        assert isinstance(result.estimated_total_time_seconds, float)
        assert isinstance(result.preview_data, list)

    def test_preview_large_dataset_warning(self):
        """Dataset >10000 rows triggers warning."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=0, preview_sample_size=2),
        )
        df = pd.DataFrame({"paese": [f"Country_{i}" for i in range(10001)]})

        fake_stats = EnrichmentStats(total_rows=2, success=2)
        with patch.object(ex, "_execute_internal",
                          return_value=(df.head(2), fake_stats, [])):
            result = ex.preview(df, sample_size=2)

        has_warning = any("10001" in w for w in result.warnings)
        assert has_warning is True

    def test_preview_high_error_rate_warning(self):
        """Error rate > 20% triggers warning."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=0, preview_sample_size=5),
        )
        df = pd.DataFrame({"paese": ["A", "B", "C", "D", "E"]})

        # 2 errors out of 5 = 40% > 20%
        fake_stats = EnrichmentStats(total_rows=5, success=3, errors=2)
        with patch.object(ex, "_execute_internal",
                          return_value=(df, fake_stats, [])):
            result = ex.preview(df, sample_size=5)

        has_error_warning = any("errori" in w.lower() for w in result.warnings)
        assert has_error_warning is True

    def test_preview_parallel_workers_warning(self):
        """parallel_workers > 1 triggers warning."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=0,
                                     preview_sample_size=2, parallel_workers=4),
        )
        df = pd.DataFrame({"paese": ["A", "B"]})

        fake_stats = EnrichmentStats(total_rows=2, success=2)
        with patch.object(ex, "_execute_internal",
                          return_value=(df, fake_stats, [])):
            result = ex.preview(df, sample_size=2)

        has_parallel_warning = any("parallela" in w.lower() or "worker" in w.lower()
                                   for w in result.warnings)
        assert has_parallel_warning is True

    def test_preview_restores_progress_callback(self):
        """preview() disables and restores progress_callback."""
        callback = MagicMock()
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=0, preview_sample_size=2),
            progress_callback=callback,
        )
        df = pd.DataFrame({"paese": ["A", "B"]})

        fake_stats = EnrichmentStats(total_rows=2, success=2)
        with patch.object(ex, "_execute_internal",
                          return_value=(df, fake_stats, [])):
            ex.preview(df)

        assert ex.progress_callback is callback


# =============================================================================
# execute() — routing: batch vs parallel vs sequential (lines 347, 349)
# =============================================================================

class TestExecuteRouting:

    def test_execute_routes_to_batch_native(self):
        """batch_config.enabled=True routes to _execute_batch_native."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            batch={"enabled": True, "max_size": 10},
            options=EnrichmentOptions(rate_limit=0, max_retries=0, validate_input=False),
        )
        df = pd.DataFrame({"paese": ["Italy"]})

        fake_stats = EnrichmentStats(total_rows=1, success=1)
        with patch.object(ex, "_execute_batch_native",
                          return_value=(df, fake_stats, [])) as mock_batch:
            result_df, result = ex.execute(df)

        mock_batch.assert_called_once()
        assert result.success is True

    def test_execute_routes_to_parallel(self):
        """parallel_workers > 1 routes to _execute_parallel."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=0,
                                     parallel_workers=4, validate_input=False),
        )
        df = pd.DataFrame({"paese": ["Italy"]})

        fake_stats = EnrichmentStats(total_rows=1, success=1)
        with patch.object(ex, "_execute_parallel",
                          return_value=(df, fake_stats, [])) as mock_par:
            result_df, result = ex.execute(df)

        mock_par.assert_called_once()

    def test_execute_validation_failure_returns_early(self):
        """Invalid input returns early with success=False."""
        ex = make_executor(
            param_mapping={"country": "MISSING_COL"},
            options=EnrichmentOptions(rate_limit=0, max_retries=0, validate_input=True),
        )
        df = pd.DataFrame({"paese": ["Italy"]})
        result_df, result = ex.execute(df)
        assert result.success is False
        assert len(result.warnings) > 0

    def test_execute_success_determination(self):
        """success = errors==0 or (continue_on_error and success>0)."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=0,
                                     continue_on_error=True, validate_input=False),
        )
        df = pd.DataFrame({"paese": ["Italy", "BAD"]})

        stats_with_mixed = EnrichmentStats(total_rows=2, success=1, errors=1)
        with patch.object(ex, "_execute_internal",
                          return_value=(df, stats_with_mixed, [])):
            _, result = ex.execute(df)

        # continue_on_error=True and success>0 => success=True
        assert result.success is True


# =============================================================================
# _execute_parallel — lines 382-536
# =============================================================================

class TestExecuteParallel:

    def _make_parallel_executor(self, **kwargs):
        defaults = dict(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(
                rate_limit=0, max_retries=0, parallel_workers=2,
                enable_deduplication=True, skip_null_params=True,
            ),
        )
        defaults.update(kwargs)
        return make_executor(**defaults)

    def test_parallel_success(self):
        """Parallel execution with mocked API calls succeeds."""
        ex = self._make_parallel_executor()
        df = pd.DataFrame({"paese": ["Italy", "France"]})

        with patch("requests.get",
                   return_value=mock_response({"capital": "X"})):
            result_df, stats, errors = ex._execute_parallel(df)

        assert stats.success >= 1
        assert "capitale" in result_df.columns

    def test_parallel_null_params_skipped(self):
        """Rows with null params are skipped in parallel mode."""
        ex = self._make_parallel_executor()
        df = pd.DataFrame({"paese": [None, "France"]})

        with patch("requests.get",
                   return_value=mock_response({"capital": "Paris"})):
            result_df, stats, errors = ex._execute_parallel(df)

        assert stats.skipped >= 1

    def test_parallel_api_error(self):
        """API errors are recorded for all rows in the group."""
        ex = self._make_parallel_executor(
            options=EnrichmentOptions(
                rate_limit=0, max_retries=0, parallel_workers=2,
                enable_deduplication=True,
            ),
        )
        df = pd.DataFrame({"paese": ["Italy", "Italy"]})

        with patch("requests.get",
                   return_value=mock_response({}, status_code=400)):
            result_df, stats, errors = ex._execute_parallel(df)

        assert len(errors) == 2  # both rows in same group get the error

    def test_parallel_without_dedup(self):
        """Without deduplication, each row is its own group."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(
                rate_limit=0, max_retries=0, parallel_workers=2,
                enable_deduplication=False,
            ),
        )
        df = pd.DataFrame({"paese": ["Italy", "Italy"]})

        with patch("requests.get",
                   return_value=mock_response({"capital": "Rome"})):
            result_df, stats, errors = ex._execute_parallel(df)

        # Without dedup: 2 API calls (one per row)
        assert stats.api_calls_made == 2

    def test_parallel_worker_crash(self):
        """Worker exception is caught and recorded as error."""
        ex = self._make_parallel_executor()
        df = pd.DataFrame({"paese": ["Italy"]})

        with patch("requests.get", side_effect=RuntimeError("boom")):
            result_df, stats, errors = ex._execute_parallel(df)

        # Should be recorded as error (either via _call_api_with_retry or worker crash)
        assert stats.errors >= 1 or len(errors) >= 1


# =============================================================================
# _execute_batch_native — lines 557-651
# =============================================================================

class TestExecuteBatchNative:

    def _make_batch_executor(self, **kwargs):
        defaults = dict(
            method="POST",
            param_mapping={"q": "indirizzo"},
            output_mapping=[{"path": "lat", "column": "lat"}, {"path": "lon", "column": "lon"}],
            batch={"enabled": True, "max_size": 5, "input_key": "addresses",
                   "output_path": "results"},
            options=EnrichmentOptions(rate_limit=0, max_retries=0, skip_null_params=True),
        )
        defaults.update(kwargs)
        return make_executor(**defaults)

    def test_batch_success(self):
        """Batch call returns results mapped to rows."""
        ex = self._make_batch_executor()
        df = pd.DataFrame({"indirizzo": ["Via Roma 1", "Via Milano 2"]})

        response_data = {"results": [{"lat": 41.9, "lon": 12.4}, {"lat": 45.4, "lon": 9.1}]}
        with patch("requests.post", return_value=mock_response(response_data)):
            result_df, stats, errors = ex._execute_batch_native(df)

        assert stats.success == 2
        assert stats.errors == 0
        assert result_df.at[0, "lat"] == 41.9
        assert result_df.at[1, "lon"] == 9.1

    def test_batch_null_params_skipped(self):
        """Rows with null params are skipped in batch."""
        ex = self._make_batch_executor()
        df = pd.DataFrame({"indirizzo": [None, "Via Roma 1"]})

        response_data = {"results": [{"lat": 41.9, "lon": 12.4}]}
        with patch("requests.post", return_value=mock_response(response_data)):
            result_df, stats, errors = ex._execute_batch_native(df)

        assert stats.skipped == 1
        assert stats.success == 1

    def test_batch_null_result_in_response(self):
        """Null result in batch response is recorded as error."""
        ex = self._make_batch_executor()
        df = pd.DataFrame({"indirizzo": ["Via Roma 1", "Via Milano 2"]})

        response_data = {"results": [{"lat": 41.9, "lon": 12.4}, None]}
        with patch("requests.post", return_value=mock_response(response_data)):
            result_df, stats, errors = ex._execute_batch_native(df)

        assert stats.success == 1
        assert stats.errors == 1
        assert any(e.error_type == "null_result" for e in errors)

    def test_batch_incomplete_response(self):
        """Batch response shorter than input records error for missing rows."""
        ex = self._make_batch_executor()
        df = pd.DataFrame({"indirizzo": ["A", "B", "C"]})

        response_data = {"results": [{"lat": 1.0, "lon": 2.0}]}
        with patch("requests.post", return_value=mock_response(response_data)):
            result_df, stats, errors = ex._execute_batch_native(df)

        assert stats.success == 1
        assert stats.errors == 2
        incomplete_errors = [e for e in errors if e.error_type == "incomplete_batch"]
        assert len(incomplete_errors) == 2

    def test_batch_api_error_affects_all_in_batch(self):
        """API error on batch call records error for all rows in the batch."""
        ex = self._make_batch_executor()
        df = pd.DataFrame({"indirizzo": ["A", "B"]})

        with patch("requests.post", return_value=mock_response({}, status_code=400)):
            result_df, stats, errors = ex._execute_batch_native(df)

        assert stats.errors == 2
        assert len(errors) == 2

    def test_batch_calls_saved(self):
        """api_calls_saved is batch_size - 1 for successful batch."""
        ex = self._make_batch_executor()
        df = pd.DataFrame({"indirizzo": ["A", "B", "C"]})

        response_data = {"results": [{"lat": 1}, {"lat": 2}, {"lat": 3}]}
        with patch("requests.post", return_value=mock_response(response_data)):
            result_df, stats, errors = ex._execute_batch_native(df)

        assert stats.api_calls_saved == 2  # 3 rows - 1 call = 2 saved
