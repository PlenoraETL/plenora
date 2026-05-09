"""Contracts for observability log redaction helpers."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from app.observability.log_sanitization import (
    REDACTED,
    sanitize_for_log,
    sanitize_url_for_log,
)


pytestmark = [pytest.mark.unit, pytest.mark.observability, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _BACKEND_ROOT / "app"


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


def test_shared_no_longer_owns_log_sanitization_implementation() -> None:
    assert not (_APP_ROOT / "shared" / "log_sanitization.py").exists()

    offenders: list[str] = []
    for path in _APP_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "app.shared.log_sanitization"
            ):
                offenders.append(path.relative_to(_BACKEND_ROOT).as_posix())
                break
            if isinstance(node, ast.Import):
                if any(
                    alias.name == "app.shared.log_sanitization"
                    for alias in node.names
                ):
                    offenders.append(path.relative_to(_BACKEND_ROOT).as_posix())
                    break

    assert offenders == []
