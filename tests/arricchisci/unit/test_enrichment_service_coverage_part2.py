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
# _call_batch_api — lines 669-776
# =============================================================================

class TestCallBatchApi:

    def _make_batch_executor(self):
        return make_executor(
            url="http://batch.example.com/api",
            method="POST",
            param_mapping={"q": "addr"},
            output_mapping=[{"path": "lat", "column": "lat"}],
            batch={"enabled": True, "max_size": 10, "output_path": "results"},
            options=EnrichmentOptions(rate_limit=0, max_retries=2,
                                     retry_backoff_factor=0.01,
                                     request_timeout=5),
        )

    def test_batch_api_get_method(self):
        """GET batch (rare but supported)."""
        ex = make_executor(
            url="http://batch.example.com/api",
            method="GET",
            output_mapping=[{"path": "lat", "column": "lat"}],
            batch={"enabled": True, "max_size": 10, "output_path": "results",
                   "method_override": "GET"},
            options=EnrichmentOptions(rate_limit=0, max_retries=0),
        )
        response_data = {"results": [{"lat": 41.9}]}
        with patch("requests.get", return_value=mock_response(response_data)) as mock_get:
            results, error = ex._call_batch_api([{"q": "test"}], [0])

        mock_get.assert_called_once()
        assert results is not None
        assert error is None

    def test_batch_api_429_retry(self):
        """429 response triggers retry and respects Retry-After."""
        ex = self._make_batch_executor()
        ex.options.respect_retry_after = True

        resp_429 = mock_response({}, status_code=429, headers={"Retry-After": "0"})
        resp_200 = mock_response({"results": [{"lat": 41.9}]}, status_code=200)

        with patch("requests.post", side_effect=[resp_429, resp_200]):
            results, error = ex._call_batch_api([{"q": "test"}], [0])

        assert results is not None
        assert error is None

    def test_batch_api_retriable_status(self):
        """Retriable HTTP status triggers retry with backoff."""
        ex = self._make_batch_executor()

        resp_500 = mock_response({}, status_code=500)
        resp_200 = mock_response({"results": [{"lat": 1.0}]}, status_code=200)

        with patch("requests.post", side_effect=[resp_500, resp_200]):
            results, error = ex._call_batch_api([{"q": "test"}], [0])

        assert results is not None

    def test_batch_api_non_retriable_status(self):
        """Non-retriable HTTP status returns error immediately."""
        ex = self._make_batch_executor()

        with patch("requests.post", return_value=mock_response({}, status_code=403)):
            results, error = ex._call_batch_api([{"q": "test"}], [0])

        assert results is None
        assert error is not None
        assert error.http_status == 403

    def test_batch_api_timeout(self):
        """Timeout triggers retry, eventually RETRY_EXHAUSTED."""
        import requests as req_lib
        ex = self._make_batch_executor()

        with patch("requests.post", side_effect=req_lib.Timeout("timeout")):
            results, error = ex._call_batch_api([{"q": "test"}], [0])

        assert results is None
        assert error is not None
        assert error.status == RowStatus.RETRY_EXHAUSTED

    def test_batch_api_request_exception(self):
        """RequestException triggers retry, eventually RETRY_EXHAUSTED."""
        import requests as req_lib
        ex = self._make_batch_executor()

        with patch("requests.post", side_effect=req_lib.ConnectionError("refused")):
            results, error = ex._call_batch_api([{"q": "test"}], [0])

        assert results is None
        assert error is not None
        assert error.status == RowStatus.RETRY_EXHAUSTED

    def test_batch_api_endpoint_override(self):
        """endpoint_override is used instead of default URL."""
        ex = make_executor(
            url="http://default.example.com/api",
            method="POST",
            output_mapping=[{"path": "lat", "column": "lat"}],
            batch={"enabled": True, "max_size": 10, "output_path": "results",
                   "endpoint_override": "http://batch.example.com/batch"},
            options=EnrichmentOptions(rate_limit=0, max_retries=0),
        )
        response_data = {"results": [{"lat": 41.9}]}
        with patch("requests.post", return_value=mock_response(response_data)) as mock_post:
            results, error = ex._call_batch_api([{"q": "test"}], [0])

        called_url = mock_post.call_args[0][0]
        assert called_url == "http://batch.example.com/batch"


# =============================================================================
# _build_batch_payload — lines 787-819
# =============================================================================

class TestBuildBatchPayload:

    def test_flat_array_format(self):
        """FLAT_ARRAY extracts first non-API-key value from each params dict."""
        ex = make_executor(
            param_mapping={"q": "addr"},
            batch={"enabled": True, "input_key": "addresses",
                   "input_format": "flat_array"},
            options=EnrichmentOptions(rate_limit=0, max_retries=0),
        )
        batch_params = [{"q": "Via Roma 1"}, {"q": "Via Milano 2"}]
        payload = ex._build_batch_payload(batch_params)

        assert payload["addresses"] == ["Via Roma 1", "Via Milano 2"]

    def test_array_format_removes_api_key(self):
        """ARRAY format removes API key from each params dict."""
        ex = make_executor(
            param_mapping={"q": "addr"},
            auth={"type": "api_key", "location": "query",
                  "key_name": "apiKey", "key_value": "secret"},
            batch={"enabled": True, "input_key": "items"},
            options=EnrichmentOptions(rate_limit=0, max_retries=0),
        )
        batch_params = [{"q": "test", "apiKey": "secret"}]
        payload = ex._build_batch_payload(batch_params)

        assert "apiKey" not in payload["items"][0]
        assert payload["items"][0]["q"] == "test"
        # API key added at top level
        assert payload["apiKey"] == "secret"

    def test_flat_array_skips_api_key(self):
        """FLAT_ARRAY skips the API key parameter."""
        ex = make_executor(
            param_mapping={"q": "addr"},
            auth={"type": "api_key", "location": "query",
                  "key_name": "apiKey", "key_value": "secret"},
            batch={"enabled": True, "input_key": "addresses",
                   "input_format": "flat_array"},
            options=EnrichmentOptions(rate_limit=0, max_retries=0),
        )
        batch_params = [{"apiKey": "secret", "q": "Via Roma"}]
        payload = ex._build_batch_payload(batch_params)

        assert "Via Roma" in payload["addresses"]
        assert "secret" not in payload["addresses"]


# =============================================================================
# _extract_batch_output — lines 829-852
# =============================================================================

class TestExtractBatchOutput:

    def test_non_list_response_returns_empty(self):
        """Non-list results_array returns empty list."""
        ex = make_executor(
            output_mapping=[{"path": "lat", "column": "lat"}],
            batch={"enabled": True, "output_path": "results"},
            options=EnrichmentOptions(rate_limit=0, max_retries=0),
        )
        data = {"results": "not_a_list"}
        result = ex._extract_batch_output(data)
        assert result == []

    def test_none_item_in_array(self):
        """None items in results array are preserved as None."""
        ex = make_executor(
            output_mapping=[{"path": "lat", "column": "lat"}],
            batch={"enabled": True, "output_path": "results"},
            options=EnrichmentOptions(rate_limit=0, max_retries=0),
        )
        data = {"results": [None, {"lat": 41.9}]}
        result = ex._extract_batch_output(data)
        assert len(result) == 2
        assert result[0] is None
        assert result[1]["lat"] == 41.9

    def test_no_output_path_uses_root(self):
        """No output_path uses root data directly."""
        ex = make_executor(
            output_mapping=[{"path": "lat", "column": "lat"}],
            batch={"enabled": True, "output_path": ""},
            options=EnrichmentOptions(rate_limit=0, max_retries=0),
        )
        data = [{"lat": 41.9}, {"lat": 45.4}]
        result = ex._extract_batch_output(data)
        assert len(result) == 2

    def test_path_stripping_removes_array_prefix(self):
        """Path like '[0].lat' gets cleaned to 'lat' for batch items."""
        ex = make_executor(
            output_mapping=[{"path": "[0].lat", "column": "latitude"}],
            batch={"enabled": True, "output_path": ""},
            options=EnrichmentOptions(rate_limit=0, max_retries=0),
        )
        data = [{"lat": 41.9}]
        result = ex._extract_batch_output(data)
        assert len(result) == 1
