"""
Contract test per hardening cluster del rate limiter arricchisci.
"""

from __future__ import annotations

import pytest

from app.core.arricchisci._rate_limiter import (
    RateLimiter,
    _reset_backend_for_tests,
    _reset_state_for_tests,
    _set_backend_for_tests,
    rate_limit_scope_for_api_config,
)


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _reset_limiter_state():
    _reset_backend_for_tests()
    _reset_state_for_tests()
    yield
    _reset_backend_for_tests()
    _reset_state_for_tests()


class _RecordingGcraBackend:
    """Backend GCRA fake (Fase 4: il backend reale e' PG): registra le call."""

    def __init__(self, wait_ms: int = 0):
        self.wait_ms = wait_ms
        self.calls: list[tuple[str, int]] = []

    def acquire_wait_ms(self, key: str, *, interval_ms: int) -> int:
        self.calls.append((key, interval_ms))
        return self.wait_ms


def test_distributed_enabled_uses_bucket_key():
    backend = _RecordingGcraBackend()
    _set_backend_for_tests(backend)

    limiter = RateLimiter(1000, scope="api:test", distributed_enabled=True)
    limiter.acquire()

    assert len(backend.calls) == 1
    key, interval_ms = backend.calls[0]
    assert key == "plenora:arricchisci:rl:api:test"
    assert interval_ms == 1


def test_distributed_enabled_falls_back_to_local_when_allowed():
    _set_backend_for_tests(None)

    limiter = RateLimiter(
        1000,
        scope="api:fallback",
        distributed_enabled=True,
        distributed_required=False,
    )

    limiter.acquire()


def test_distributed_required_fails_closed_when_unavailable():
    _set_backend_for_tests(None)
    limiter = RateLimiter(
        1000,
        scope="api:required",
        distributed_enabled=True,
        distributed_required=True,
    )

    with pytest.raises(RuntimeError, match="distributed rate limiter unavailable"):
        limiter.acquire()


def test_rate_limit_scope_is_stable_and_redacted():
    api_config = {
        "id": "conn-1",
        "name": "Geocoder",
        "endpoint": {
            "method": "GET",
            "url": "https://example.test/search?api_key=secret",
        },
    }

    scope = rate_limit_scope_for_api_config(api_config)

    assert scope == rate_limit_scope_for_api_config(api_config)
    assert scope.startswith("api:")
    assert "secret" not in scope
    assert "example.test" not in scope
