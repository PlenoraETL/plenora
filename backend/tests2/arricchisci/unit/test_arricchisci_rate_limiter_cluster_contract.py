"""
Contract test per hardening cluster del rate limiter arricchisci.
"""

from __future__ import annotations

import pytest

from app.core.arricchisci._rate_limiter import (
    RateLimiter,
    _reset_client_for_tests,
    _reset_state_for_tests,
    _set_client_for_tests,
    rate_limit_scope_for_api_config,
)


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _reset_limiter_state():
    _reset_client_for_tests()
    _reset_state_for_tests()
    yield
    _reset_client_for_tests()
    _reset_state_for_tests()


class _FakeRedisLimiter:
    def __init__(self, wait_ms: int = 0):
        self.wait_ms = wait_ms
        self.calls = []

    def eval(self, script, num_keys, key, interval_ms, ttl_ms):
        self.calls.append((script, num_keys, key, interval_ms, ttl_ms))
        return self.wait_ms


def test_redis_enabled_uses_distributed_bucket_key():
    client = _FakeRedisLimiter()
    _set_client_for_tests(client)

    limiter = RateLimiter(1000, scope="api:test", redis_enabled=True)
    limiter.acquire()

    assert len(client.calls) == 1
    _, num_keys, key, interval_ms, ttl_ms = client.calls[0]
    assert num_keys == 1
    assert key == "plenora:arricchisci:rl:api:test"
    assert interval_ms == 1
    assert ttl_ms > interval_ms


def test_redis_enabled_falls_back_to_local_when_allowed():
    _set_client_for_tests(None)

    limiter = RateLimiter(
        1000,
        scope="api:fallback",
        redis_enabled=True,
        redis_required=False,
    )

    limiter.acquire()


def test_redis_required_fails_closed_when_unavailable():
    _set_client_for_tests(None)
    limiter = RateLimiter(
        1000,
        scope="api:required",
        redis_enabled=True,
        redis_required=True,
    )

    with pytest.raises(RuntimeError, match="Redis rate limiter unavailable"):
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
