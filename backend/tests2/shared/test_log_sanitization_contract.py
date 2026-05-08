"""Contracts for shared log redaction helpers."""

from __future__ import annotations

import importlib

from app.shared.log_sanitization import REDACTED, sanitize_for_log, sanitize_url_for_log


def test_sanitize_url_for_log_redacts_query_and_basic_auth() -> None:
    url = (
        "https://user:pass@example.test/path"
        "?token=secret-token&safe=value&client_secret=hidden"
    )

    sanitized = sanitize_url_for_log(url)

    assert sanitized == (
        f"https://{REDACTED}@example.test/path"
        f"?token={REDACTED}&safe=value&client_secret={REDACTED}"
    )


def test_sanitize_for_log_redacts_nested_sensitive_values_without_mutating() -> None:
    payload = {
        "headers": {"Authorization": "Bearer secret", "Accept": "json"},
        "params": [{"apiKey": "secret-key", "city": "Rome"}],
    }

    sanitized = sanitize_for_log(payload, auth={"key_name": "city"})

    assert payload["headers"]["Authorization"] == "Bearer secret"
    assert sanitized["headers"]["Authorization"] == REDACTED
    assert sanitized["headers"]["Accept"] == "json"
    assert sanitized["params"][0]["apiKey"] == REDACTED
    assert sanitized["params"][0]["city"] == REDACTED


def test_api_utils_does_not_reexport_log_sanitizers() -> None:
    api_utils = importlib.import_module("app.shared.api_utils")

    assert not hasattr(api_utils, "sanitize_for_log")
    assert not hasattr(api_utils, "sanitize_url_for_log")
