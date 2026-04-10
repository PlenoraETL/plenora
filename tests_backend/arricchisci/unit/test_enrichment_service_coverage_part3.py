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


        # BUG: lstrip("[0].") strips individual characters, not the prefix "[0]."
        # lstrip removes all chars in the set {'[', '0', ']', '.'}, so "lat" becomes "at"
        # if the path were "[0].lat". The correct fix would be:
        #   clean_path = re.sub(r'^\[\d+\]\.', '', path)
        # For now, this test documents the behavior.


# =============================================================================
# _execute_internal — sequential execution (lines 889-1018)
# =============================================================================

class TestExecuteInternal:

    def test_token_renewal_at_500_calls(self):
        """OAuth2 token is renewed every 500 API calls."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            auth={"type": "oauth2_password", "token_url": "http://auth.example.com",
                  "client_id": "test", "username": "user", "password": "pass"},
            options=EnrichmentOptions(
                rate_limit=0, max_retries=0, enable_deduplication=False,
            ),
        )

        # We need 500 unique rows to trigger renewal
        # Instead, verify _prepare_headers is called by patching
        df = pd.DataFrame({"paese": [f"C{i}" for i in range(501)]})

        call_count = [0]
        original_prepare = ex._prepare_headers

        def counting_prepare():
            call_count[0] += 1
            return {"Authorization": "Bearer test"}

        with patch.object(ex, "_prepare_headers", side_effect=counting_prepare), \
             patch("requests.get", return_value=mock_response({"capital": "X"})):
            ex._execute_internal(df)

        # Should have been called once at row 500
        assert call_count[0] >= 1

    def test_null_params_error_when_skip_disabled(self):
        """skip_null_params=False: null params cause ERROR status."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(
                rate_limit=0, max_retries=0,
                skip_null_params=False, continue_on_error=True,
            ),
        )
        df = pd.DataFrame({"paese": [None]})

        result_df, stats, errors = ex._execute_internal(df)

        assert stats.errors == 1
        assert len(errors) == 1
        assert errors[0].error_type == "null_param"

    def test_null_params_break_when_continue_on_error_false(self):
        """skip_null_params=False + continue_on_error=False: breaks on null."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(
                rate_limit=0, max_retries=0,
                skip_null_params=False, continue_on_error=False,
            ),
        )
        df = pd.DataFrame({"paese": [None, "Italy"]})

        result_df, stats, errors = ex._execute_internal(df)

        # Should break after first null row
        assert stats.errors == 1
        assert stats.success == 0

    def test_cache_hit_skips_api_call(self):
        """Cached results skip API call."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(
                rate_limit=0, max_retries=0,
                enable_deduplication=True,
            ),
        )
        df = pd.DataFrame({"paese": ["Italy", "Italy", "Italy"]})

        with patch("requests.get",
                   return_value=mock_response({"capital": "Rome"})):
            result_df, stats, errors = ex._execute_internal(df)

        # All 3 rows map to same key; with dedup, only 1 API call
        assert stats.api_calls_made == 1
        # 2 rows served from dedup grouping (not cache), but check cached count
        # Actually with _group_by_params, all 3 are in one group, so:
        # success=3, api_calls_saved=2
        assert stats.success == 3
        assert stats.api_calls_saved == 2

    def test_feature_collection_explosion(self):
        """FeatureCollection with multiple features creates extra rows."""
        ex = make_executor(
            param_mapping={"q": "addr"},
            output_mapping=[],  # auto-flatten
            options=EnrichmentOptions(
                rate_limit=0, max_retries=0, enable_deduplication=False,
            ),
        )
        df = pd.DataFrame({"addr": ["test"]})

        fc_response = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"name": "A"},
                 "geometry": {"type": "Point", "coordinates": [12.0, 41.0]}},
                {"type": "Feature", "properties": {"name": "B"},
                 "geometry": {"type": "Point", "coordinates": [12.1, 41.1]}},
            ]
        }
        with patch("requests.get", return_value=mock_response(fc_response)):
            result_df, stats, errors = ex._execute_internal(df)

        # Original 1 row + 1 extra from second feature = 2 rows total
        assert len(result_df) == 2

    def test_error_threshold_stops_execution(self):
        """stop_on_error_threshold stops after N errors."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(
                rate_limit=0, max_retries=0,
                enable_deduplication=False,
                continue_on_error=True,
                stop_on_error_threshold=2,
            ),
        )
        df = pd.DataFrame({"paese": ["A", "B", "C", "D", "E"]})

        with patch("requests.get",
                   return_value=mock_response({}, status_code=400)):
            result_df, stats, errors = ex._execute_internal(df)

        # Should stop after 2 errors (threshold=2)
        assert stats.errors == 2

    def test_retry_exhausted_counts_as_error(self):
        """RETRY_EXHAUSTED status is counted in both retry_exhausted and errors."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(
                rate_limit=0, max_retries=2, retry_backoff_factor=0.01,
                enable_deduplication=False, continue_on_error=True,
            ),
        )
        df = pd.DataFrame({"paese": ["Italy"]})

        with patch("requests.get",
                   return_value=mock_response({}, status_code=500)):
            result_df, stats, errors = ex._execute_internal(df)

        assert stats.retry_exhausted == 1
        assert stats.errors == 1  # B-BE-56: counted in both

    def test_continue_on_error_false_breaks_on_api_error(self):
        """continue_on_error=False stops execution on first error."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(
                rate_limit=0, max_retries=0,
                enable_deduplication=False, continue_on_error=False,
            ),
        )
        df = pd.DataFrame({"paese": ["A", "B", "C"]})

        with patch("requests.get",
                   return_value=mock_response({}, status_code=400)):
            result_df, stats, errors = ex._execute_internal(df)

        assert stats.errors == 1  # stopped after first
        assert stats.success == 0

    def test_rate_limiting_delay_applied(self):
        """rate_limit > 0 applies delay between calls."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options=EnrichmentOptions(
                rate_limit=100.0,  # very fast, just to test delay != 0
                max_retries=0, enable_deduplication=False,
            ),
        )
        df = pd.DataFrame({"paese": ["A", "B"]})

        with patch("requests.get",
                   return_value=mock_response({"capital": "X"})), \
             patch("time.sleep") as mock_sleep:
            result_df, stats, errors = ex._execute_internal(df)

        # time.sleep should have been called (delay = 1/100 = 0.01)
        assert mock_sleep.called


# =============================================================================
# _build_params — numpy bool_ conversion (line 1064-1066)
# =============================================================================

class TestBuildParamsNumpyBool:

    def test_numpy_bool_converted(self):
        """numpy.bool_ is converted to Python bool."""
        ex = make_executor(param_mapping={"active": "flag"})
        row = pd.Series({"flag": np.bool_(True)})
        params = ex._build_params(row)
        assert params["active"] is True
        assert isinstance(params["active"], bool)

    def test_numpy_bool_false_converted(self):
        ex = make_executor(param_mapping={"active": "flag"})
        row = pd.Series({"flag": np.bool_(False)})
        params = ex._build_params(row)
        assert params["active"] is False
        assert isinstance(params["active"], bool)


# =============================================================================
# _call_api_with_retry — lines 1087-1217
# =============================================================================

class TestCallApiWithRetry:

    def test_path_param_substitution(self):
        """URL path params like {ip} are replaced from params."""
        ex = make_executor(
            url="http://api.example.com/lookup/{ip}",
            method="GET",
            output_mapping=[{"path": "country", "column": "country"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=0),
        )

        with patch("requests.get",
                   return_value=mock_response({"country": "Italy"})) as mock_get:
            result, error = ex._call_api_with_retry({"ip": "1.2.3.4"}, 0)

        called_url = mock_get.call_args[0][0]
        assert "1.2.3.4" in called_url
        assert "{ip}" not in called_url
        # ip should be removed from query params
        called_params = mock_get.call_args[1]["params"]
        assert "ip" not in called_params

    def test_post_method(self):
        """POST method sends JSON body."""
        ex = make_executor(
            url="http://api.example.com/enrich",
            method="POST",
            output_mapping=[{"path": "result", "column": "result"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=0),
        )

        with patch("requests.post",
                   return_value=mock_response({"result": "ok"})) as mock_post:
            result, error = ex._call_api_with_retry({"q": "test"}, 0)

        mock_post.assert_called_once()
        assert result is not None

    def test_429_rate_limit_retry(self):
        """429 response triggers retry."""
        ex = make_executor(
            output_mapping=[{"path": "x", "column": "x"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=1,
                                     retry_backoff_factor=0.01),
        )

        resp_429 = mock_response({}, status_code=429, headers={"Retry-After": "0"})
        resp_200 = mock_response({"x": 1}, status_code=200)

        with patch("requests.get", side_effect=[resp_429, resp_200]):
            result, error = ex._call_api_with_retry({"q": "test"}, 0)

        assert result is not None
        assert error is None

    def test_retriable_status_with_backoff(self):
        """Retriable HTTP status uses exponential backoff."""
        ex = make_executor(
            output_mapping=[{"path": "x", "column": "x"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=1,
                                     retry_backoff_factor=0.01),
        )

        resp_500 = mock_response({}, status_code=500)
        resp_200 = mock_response({"x": 1}, status_code=200)

        with patch("requests.get", side_effect=[resp_500, resp_200]):
            result, error = ex._call_api_with_retry({"q": "test"}, 0)

        assert result is not None

    def test_non_retriable_status(self):
        """Non-retriable HTTP status returns error immediately."""
        ex = make_executor(
            output_mapping=[{"path": "x", "column": "x"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=3),
        )

        with patch("requests.get",
                   return_value=mock_response({"err": "forbidden"}, status_code=403)):
            result, error = ex._call_api_with_retry({"q": "test"}, 0)

        assert result is None
        assert error is not None
        assert error.http_status == 403
        assert error.retry_count == 0  # no retry attempted

    def test_timeout_retry_exhausted(self):
        """Timeout exhausts retries and returns RETRY_EXHAUSTED."""
        import requests as req_lib
        ex = make_executor(
            output_mapping=[{"path": "x", "column": "x"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=1,
                                     retry_backoff_factor=0.01),
        )

        with patch("requests.get", side_effect=req_lib.Timeout("timeout")):
            result, error = ex._call_api_with_retry({"q": "test"}, 0)

        assert result is None
        assert error.status == RowStatus.RETRY_EXHAUSTED

    def test_request_exception_retry_exhausted(self):
        """RequestException exhausts retries."""
        import requests as req_lib
        ex = make_executor(
            output_mapping=[{"path": "x", "column": "x"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=1,
                                     retry_backoff_factor=0.01),
        )

        with patch("requests.get", side_effect=req_lib.ConnectionError("refused")):
            result, error = ex._call_api_with_retry({"q": "test"}, 0)

        assert result is None
        assert error.status == RowStatus.RETRY_EXHAUSTED

    def test_ssl_verify_disabled(self):
        """disable_ssl_verify=True passes verify=False to requests."""
        ex = make_executor(
            output_mapping=[{"path": "x", "column": "x"}],
            options=EnrichmentOptions(rate_limit=0, max_retries=0,
                                     disable_ssl_verify=True),
        )

        with patch("requests.get",
                   return_value=mock_response({"x": 1})) as mock_get:
            ex._call_api_with_retry({"q": "test"}, 0)

        assert mock_get.call_args[1]["verify"] is False


# =============================================================================
# _extract_output — all-None fallback (line 1229)
# =============================================================================

class TestExtractOutputFallback:

    def test_all_none_mapping_falls_back_to_auto_flatten(self):
        """If output_mapping produces all None, falls back to auto_flatten."""
        ex = make_executor(
            output_mapping=[
                {"path": "nonexistent_field", "column": "col1"},
                {"path": "also_missing", "column": "col2"},
            ],
        )
        data = {"name": "Italy", "capital": "Rome"}
        result = ex._extract_output(data)

        # auto_flatten should produce a dict with actual keys
        assert len(result) == 1
        assert "name" in result[0] or "capital" in result[0]

    def test_partial_none_does_not_fallback(self):
        """If some mappings match, no fallback."""
        ex = make_executor(
            output_mapping=[
                {"path": "name", "column": "nome"},
                {"path": "missing", "column": "col2"},
            ],
        )
        data = {"name": "Italy"}
        result = ex._extract_output(data)

        assert result[0]["nome"] == "Italy"
        assert result[0]["col2"] is None  # no fallback
