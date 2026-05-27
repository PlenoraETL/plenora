"""Contracts for removed compatibility shims."""

from __future__ import annotations

from typing import Any

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Removed alias tombstones


def test_deprecated_alias_helpers_are_removed():
    """R-21/R-22 compatibility helpers must not remain as runtime surface."""
    from app.core.security import outbound_url
    from app.infrastructure.storage import cloud_backends

    assert not hasattr(outbound_url, "_warn_urby_loopback_alias_once")
    assert not hasattr(cloud_backends, "_warn_get_object_storage_deprecated_once")
    assert not hasattr(cloud_backends, "get_object_storage")


def test_api_utils_compat_module_is_tombstoned():
    """R-23/R-24 API utility shim was removed after migrating callers."""
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[2]

    assert not (backend_root / "app" / "shared" / "api_utils.py").exists()


def test_prepare_headers_uses_canonical_cached_oauth2_token():
    """OAuth auth headers must use the canonical cache helper directly."""
    from app.shared.connections import oauth

    calls: list[dict[str, Any]] = []

    def _fake_cached(auth: dict[str, Any]) -> str:
        calls.append(dict(auth))
        return "tok-1"

    headers = oauth.prepare_headers(
        {
            "type": "oauth2_client_credentials",
            "token_url": "https://auth.example/token",
            "client_id": "client-1",
        },
        oauth2_token_getter=_fake_cached,
    )

    assert headers["Authorization"] == "Bearer tok-1"
    assert calls == [
        {
            "type": "oauth2_client_credentials",
            "token_url": "https://auth.example/token",
            "client_id": "client-1",
        }
    ]
