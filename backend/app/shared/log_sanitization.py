"""Shared helpers for redacting sensitive values before logging."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


SENSITIVE_KEY_PATTERNS = (
    "token",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "authorization",
    "auth_key",
    "access_key",
    "client_secret",
    "private_key",
    "x-api-key",
)

REDACTED = "***REDACTED***"


def sanitize_url_for_log(url: str) -> str:
    """Return a URL with sensitive query and basic-auth values redacted."""
    if not isinstance(url, str) or not url:
        return url
    if "?" not in url and "@" not in url:
        return url

    url = re.sub(
        r"(?i)(https?://)([^:/@\s]+):([^@\s]+)@",
        lambda m: f"{m.group(1)}{REDACTED}@",
        url,
    )

    if "?" in url:
        sensitive_pattern = "|".join(re.escape(k) for k in SENSITIVE_KEY_PATTERNS)
        qs_redact_re = re.compile(
            r"(?i)([?&])((?:" + sensitive_pattern + r")=)[^&\s'\"]+"
        )
        url = qs_redact_re.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", url)

    return url


def sanitize_for_log(data: Any, auth: Optional[Dict[str, Any]] = None) -> Any:
    """Return a copy of ``data`` with sensitive dict values redacted."""
    extra_keys = set()
    if isinstance(auth, dict):
        key_name = auth.get("key_name")
        if key_name:
            extra_keys.add(str(key_name).lower())

    def _is_sensitive(key: str) -> bool:
        normalized = key.lower()
        if normalized in extra_keys:
            return True
        return any(pattern in normalized for pattern in SENSITIVE_KEY_PATTERNS)

    def _walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                key: (REDACTED if _is_sensitive(str(key)) else _walk(value))
                for key, value in obj.items()
            }
        if isinstance(obj, (list, tuple)):
            return type(obj)(_walk(value) for value in obj)
        return obj

    return _walk(data)
