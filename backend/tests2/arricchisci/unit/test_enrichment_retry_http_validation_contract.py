"""Contracts for per-row REST URL validation around the retry loop."""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

import pytest

import app.core.arricchisci.enrichment_retry_http as retry_mod
from app.core.arricchisci.enrichment_retry_http import EnrichmentRetryHttpMixin
from app.core.arricchisci.models import EnrichmentOptions


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.contract,
    pytest.mark.security,
    pytest.mark.regression,
]


class _Response:
    def __init__(self, status_code: int, json_data: Any = None) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = ""
        self.headers = {}
        self.elapsed_ms = 1.0

    def json(self) -> Any:
        return self._json_data


class _NoopRateLimiter:
    def __init__(self) -> None:
        self.acquired = 0

    def acquire(self) -> None:
        self.acquired += 1


class _Host(EnrichmentRetryHttpMixin):
    def __init__(self, *, options: EnrichmentOptions) -> None:
        self.api_config = {"request": {}, "options": {}}
        self.url = "https://api.example.com/q"
        self.method = "GET"
        self.options = options
        self.auth = {}
        self._headers = {"Accept": "application/json"}
        self._stats_lock = threading.Lock()
        self._response_times = deque(maxlen=1000)
        self._rate_limit_hits_observed = 0
        self._rate_limiter = _NoopRateLimiter()

    def _extract_output(self, data: Any) -> Any:
        return data


def test_per_row_validates_url_once_before_retry_loop(monkeypatch):
    host = _Host(options=EnrichmentOptions(max_retries=1, retry_on_status=[500]))
    validated_urls = []
    specs = []

    monkeypatch.setattr(
        retry_mod,
        "validate_external_url",
        lambda url: validated_urls.append(url),
    )
    monkeypatch.setattr(
        retry_mod,
        "resolve_tls_verify",
        lambda enabled, *, context: enabled,
    )
    monkeypatch.setattr(retry_mod.time, "sleep", lambda *_args, **_kwargs: None)

    def execute(spec):
        specs.append(spec)
        if len(specs) == 1:
            return _Response(500)
        return _Response(200, {"ok": True})

    monkeypatch.setattr(retry_mod, "execute_http_request", execute)

    result, err = host._call_api_with_retry({"q": "abc"}, row_index=4)

    assert err is None
    assert result == {"ok": True}
    assert validated_urls == ["https://api.example.com/q"]
    assert len(specs) == 2
    assert all(spec.validate_url is False for spec in specs)


def test_per_row_request_config_reaches_request_spec(monkeypatch):
    host = _Host(options=EnrichmentOptions(max_retries=0))
    host.api_config = {
        "request": {
            "body_type": "form-urlencoded",
            "allow_redirects": True,
        },
    }
    specs = []

    monkeypatch.setattr(retry_mod, "validate_external_url", lambda _url: None)
    monkeypatch.setattr(
        retry_mod,
        "resolve_tls_verify",
        lambda enabled, *, context: enabled,
    )

    def execute(spec):
        specs.append(spec)
        return _Response(200, {"ok": True})

    monkeypatch.setattr(retry_mod, "execute_http_request", execute)

    result, err = host._call_api_with_retry({"q": "abc"}, row_index=4)

    assert err is None
    assert result == {"ok": True}
    assert specs[0].body_type == "form-urlencoded"
    assert specs[0].allow_redirects is True


def test_per_row_async_polls_and_extracts_final_result(monkeypatch):
    host = _Host(options=EnrichmentOptions(max_retries=0))
    host.method = "POST"
    host.api_config = {
        "async": {
            "enabled": True,
            "job_id_path": "id",
            "status_url": "{base}/jobs/{job_id}",
            "result_path": "result",
            "poll_interval_s": 0,
            "max_wait_s": 1,
        },
    }
    specs = []
    responses = [
        _Response(202, {"id": "job-1"}),
        _Response(200, {"status": "completed", "result": {"ok": True}}),
    ]

    monkeypatch.setattr(retry_mod, "validate_external_url", lambda _url: None)
    monkeypatch.setattr(
        retry_mod,
        "resolve_tls_verify",
        lambda enabled, *, context: enabled,
    )
    monkeypatch.setattr(retry_mod.time, "sleep", lambda *_args, **_kwargs: None)

    def execute(spec):
        specs.append(spec)
        return responses.pop(0)

    monkeypatch.setattr(retry_mod, "execute_http_request", execute)

    result, err = host._call_api_with_retry({"q": "abc"}, row_index=4)

    assert err is None
    assert result == {"ok": True}
    assert [spec.context for spec in specs] == [
        "API enricher per-row",
        "API async status",
    ]
    assert specs[1].url == "https://api.example.com/jobs/job-1"
    assert host._rate_limiter.acquired == 1


def test_per_row_async_submit_429_uses_existing_retry(monkeypatch):
    host = _Host(
        options=EnrichmentOptions(
            max_retries=1,
            respect_retry_after=False,
            retry_on_status=[429],
        )
    )
    host.method = "POST"
    host.api_config = {
        "async": {
            "enabled": True,
            "job_id_path": "id",
            "status_url": "{base}/jobs/{job_id}",
            "result_path": "result",
            "poll_interval_s": 0,
            "max_wait_s": 1,
        },
    }
    responses = [
        _Response(429),
        _Response(202, {"id": "job-1"}),
        _Response(200, {"status": "completed", "result": {"ok": True}}),
    ]

    monkeypatch.setattr(retry_mod, "validate_external_url", lambda _url: None)
    monkeypatch.setattr(
        retry_mod,
        "resolve_tls_verify",
        lambda enabled, *, context: enabled,
    )
    monkeypatch.setattr(retry_mod.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        retry_mod,
        "execute_http_request",
        lambda _spec: responses.pop(0),
    )

    result, err = host._call_api_with_retry({"q": "abc"}, row_index=4)

    assert err is None
    assert result == {"ok": True}
    assert host._rate_limit_hits_observed == 1


def test_per_row_async_status_error_does_not_resubmit(monkeypatch):
    host = _Host(
        options=EnrichmentOptions(max_retries=1, retry_on_status=[503])
    )
    host.method = "POST"
    host.api_config = {
        "async": {
            "enabled": True,
            "job_id_path": "id",
            "status_url": "{base}/jobs/{job_id}",
            "result_path": "result",
            "poll_interval_s": 0,
            "max_wait_s": 1,
            "retry_status": [],
        },
    }
    specs = []
    responses = [
        _Response(202, {"id": "job-1"}),
        _Response(503, {"error": "busy"}),
    ]

    monkeypatch.setattr(retry_mod, "validate_external_url", lambda _url: None)
    monkeypatch.setattr(
        retry_mod,
        "resolve_tls_verify",
        lambda enabled, *, context: enabled,
    )
    monkeypatch.setattr(retry_mod.time, "sleep", lambda *_args, **_kwargs: None)

    def execute(spec):
        specs.append(spec)
        if not responses:
            pytest.fail("status-stage async error must not re-submit")
        return responses.pop(0)

    monkeypatch.setattr(retry_mod, "execute_http_request", execute)

    result, err = host._call_api_with_retry({"q": "abc"}, row_index=4)

    assert result is None
    assert err is not None
    assert err.error_type == "async_status_error"
    assert err.http_status == 503
    assert err.retry_count == 0
    assert [spec.context for spec in specs] == [
        "API enricher per-row",
        "API async status",
    ]
    assert host._rate_limiter.acquired == 1


def test_per_row_unsupported_method_returns_config_row_error(monkeypatch):
    host = _Host(options=EnrichmentOptions(max_retries=3))
    host.method = "HEAD"

    monkeypatch.setattr(retry_mod, "validate_external_url", lambda _url: None)
    monkeypatch.setattr(
        retry_mod,
        "execute_http_request",
        lambda _spec: pytest.fail("HTTP must not run after config rejection"),
    )

    result, err = host._call_api_with_retry({"q": "abc"}, row_index=4)

    assert result is None
    assert err is not None
    assert err.error_type == "request_config_error"
    assert err.retry_count == 0
    assert "Metodo HTTP non supportato" in err.error_message


def test_per_row_unsupported_body_type_returns_config_row_error(monkeypatch):
    host = _Host(options=EnrichmentOptions(max_retries=3))
    host.method = "POST"
    host.api_config = {"request": {"body_type": "xml"}}

    monkeypatch.setattr(retry_mod, "validate_external_url", lambda _url: None)
    monkeypatch.setattr(
        retry_mod,
        "execute_http_request",
        lambda _spec: pytest.fail("HTTP must not run after config rejection"),
    )

    result, err = host._call_api_with_retry({"q": "abc"}, row_index=4)

    assert result is None
    assert err is not None
    assert err.error_type == "request_config_error"
    assert err.retry_count == 0
    assert "body_type non supportato" in err.error_message


def test_per_row_invalid_async_config_returns_config_row_error(monkeypatch):
    host = _Host(options=EnrichmentOptions(max_retries=3))
    host.api_config = {"async": {"enabled": True}}

    monkeypatch.setattr(retry_mod, "validate_external_url", lambda _url: None)
    monkeypatch.setattr(
        retry_mod,
        "execute_http_request",
        lambda _spec: pytest.fail("HTTP must not run after config rejection"),
    )

    result, err = host._call_api_with_retry({"q": "abc"}, row_index=4)

    assert result is None
    assert err is not None
    assert err.error_type == "request_config_error"
    assert err.retry_count == 0
    assert "async.status_url" in err.error_message


def test_per_row_application_error_path_returns_row_error(monkeypatch):
    host = _Host(options=EnrichmentOptions(max_retries=0))
    host.api_config = {"error_path": "error"}

    monkeypatch.setattr(retry_mod, "validate_external_url", lambda _url: None)
    monkeypatch.setattr(
        retry_mod,
        "resolve_tls_verify",
        lambda enabled, *, context: enabled,
    )
    monkeypatch.setattr(
        retry_mod,
        "execute_http_request",
        lambda _spec: _Response(
            200,
            {"error": {"message": "quota exceeded"}},
        ),
    )

    result, err = host._call_api_with_retry({"q": "abc"}, row_index=4)

    assert result is None
    assert err is not None
    assert err.error_type == "application_error"
    assert err.error_message == "quota exceeded"


def test_per_row_output_extraction_error_returns_row_error(monkeypatch):
    class _BadOutputHost(_Host):
        def _extract_output(self, data: Any) -> Any:
            raise KeyError("missing")

    host = _BadOutputHost(options=EnrichmentOptions(max_retries=0))

    monkeypatch.setattr(retry_mod, "validate_external_url", lambda _url: None)
    monkeypatch.setattr(
        retry_mod,
        "resolve_tls_verify",
        lambda enabled, *, context: enabled,
    )
    monkeypatch.setattr(
        retry_mod,
        "execute_http_request",
        lambda _spec: _Response(200, {"ok": True}),
    )

    result, err = host._call_api_with_retry({"q": "abc"}, row_index=4)

    assert result is None
    assert err is not None
    assert err.error_type == "response_processing_error"
    assert err.retry_count == 0


def test_per_row_validation_error_short_circuits_before_http(monkeypatch):
    host = _Host(options=EnrichmentOptions(max_retries=3))

    def reject_url(_url):
        raise ValueError("blocked outbound URL")

    def fail_http(_spec):
        pytest.fail("HTTP must not run after URL rejection")

    monkeypatch.setattr(retry_mod, "validate_external_url", reject_url)
    monkeypatch.setattr(retry_mod, "execute_http_request", fail_http)

    result, err = host._call_api_with_retry({"q": "abc"}, row_index=7)

    assert result is None
    assert err is not None
    assert err.error_type == "request_error"
    assert err.retry_count == 0
