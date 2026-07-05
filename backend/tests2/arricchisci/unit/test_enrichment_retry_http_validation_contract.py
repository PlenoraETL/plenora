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


class _Host(EnrichmentRetryHttpMixin):
    def __init__(self, *, options: EnrichmentOptions) -> None:
        self.url = "https://api.example.com/q"
        self.method = "GET"
        self.options = options
        self.auth = {}
        self._headers = {"Accept": "application/json"}
        self._stats_lock = threading.Lock()
        self._response_times = deque(maxlen=1000)
        self._rate_limit_hits_observed = 0

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
