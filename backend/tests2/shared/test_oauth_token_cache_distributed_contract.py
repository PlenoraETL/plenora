"""Contract tests for distributed OAuth/API token cache."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from app.shared.connections import oauth


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []
        self.set_calls: list[tuple[str, str, bool, int | None]] = []
        self.delete_calls: list[str] = []

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))
        self.store[key] = value

    def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool:
        self.set_calls.append((key, value, nx, ex))
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def delete(self, key: str) -> None:
        self.delete_calls.append(key)
        self.store.pop(key, None)


@pytest.fixture(autouse=True)
def _reset_token_cache_state():
    oauth._reset_token_cache_state_for_tests()
    yield
    oauth._reset_token_cache_state_for_tests()


def _oauth2_auth() -> dict[str, Any]:
    return {
        "type": "oauth2_client_credentials",
        "token_url": "https://issuer.example.test/oauth/token",
        "client_id": "client-secret-bearing-id",
        "client_secret": "super-secret-client-secret",
        "scope": "read",
    }


def test_oauth2_token_reads_distributed_cache_without_refresh(monkeypatch):
    fake_redis = _FakeRedis()
    oauth._set_token_cache_client_for_tests(fake_redis)
    auth = _oauth2_auth()
    raw_key = oauth._oauth2_cache_key(auth)
    redis_key = oauth._distributed_token_cache_key("oauth2", raw_key)
    fake_redis.setex(
        redis_key,
        3600,
        json.dumps({"token": "cached-token", "expires_at": time.time() + 3600}),
    )

    def _unexpected_fetch(_auth):
        pytest.fail("token refresh should not run when Redis has a valid token")

    monkeypatch.setattr(oauth, "_fetch_oauth2_client_credentials_token", _unexpected_fetch)

    assert oauth.get_cached_oauth2_token(auth) == "cached-token"
    assert all("super-secret" not in key for key in fake_redis.store)
    assert all("issuer.example.test" not in key for key in fake_redis.store)


def test_oauth2_refresh_writes_redis_once_and_second_pod_reuses_it(monkeypatch):
    fake_redis = _FakeRedis()
    oauth._set_token_cache_client_for_tests(fake_redis)
    calls: list[dict[str, Any]] = []

    def _fetch(_auth):
        calls.append(dict(_auth))
        return {"access_token": "fresh-token", "expires_in": 3600}

    monkeypatch.setattr(oauth, "_fetch_oauth2_client_credentials_token", _fetch)
    auth = _oauth2_auth()

    assert oauth.get_cached_oauth2_token(auth) == "fresh-token"

    with oauth._OAUTH2_TOKEN_LOCK:
        oauth._OAUTH2_TOKEN_CACHE.clear()

    assert oauth.get_cached_oauth2_token(auth) == "fresh-token"
    assert len(calls) == 1
    assert len(fake_redis.setex_calls) == 1
    assert any(call[2] is True for call in fake_redis.set_calls)

