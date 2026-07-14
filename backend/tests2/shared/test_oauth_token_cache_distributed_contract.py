"""Contract tests for distributed OAuth/API token cache."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from app.shared.connections import oauth
from app.shared.kv import InMemoryKeyValueStore


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

class _RecordingStore(InMemoryKeyValueStore):
    """KeyValueStore in-memory (Fase 4: il backend e' PG) che registra le
    scritture token e le acquisizioni lock per le asserzioni del contract."""

    def __init__(self) -> None:
        super().__init__()
        self.set_calls: list[tuple[str, int | None]] = []
        self.lock_calls: list[tuple[str, str | None]] = []

    def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None:
        self.set_calls.append((key, ttl_seconds))
        super().set(key, value, ttl_seconds=ttl_seconds)

    def acquire_lock(
        self, key: str, *, ttl_seconds: int, owner: str | None = None
    ) -> bool:
        self.lock_calls.append((key, owner))
        return super().acquire_lock(key, ttl_seconds=ttl_seconds, owner=owner)


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
    store = _RecordingStore()
    oauth._set_token_cache_client_for_tests(store)
    auth = _oauth2_auth()
    raw_key = oauth._oauth2_cache_key(auth)
    store_key = oauth._distributed_token_cache_key("oauth2", raw_key)
    store.set(
        store_key,
        json.dumps({"token": "cached-token", "expires_at": time.time() + 3600}),
        ttl_seconds=3600,
    )

    def _unexpected_fetch(_auth):
        pytest.fail("token refresh should not run when store has a valid token")

    monkeypatch.setattr(oauth, "_fetch_oauth2_client_credentials_token", _unexpected_fetch)

    assert oauth.get_cached_oauth2_token(auth) == "cached-token"
    # Le chiavi sono hash: nessun segreto/host in chiaro.
    assert all("super-secret" not in key for key in store.scan_keys(""))
    assert all("issuer.example.test" not in key for key in store.scan_keys(""))


def test_oauth2_refresh_writes_store_once_and_second_pod_reuses_it(monkeypatch):
    store = _RecordingStore()
    oauth._set_token_cache_client_for_tests(store)
    calls: list[dict[str, Any]] = []

    def _fetch(_auth):
        calls.append(dict(_auth))
        return {"access_token": "fresh-token", "expires_in": 3600}

    monkeypatch.setattr(oauth, "_fetch_oauth2_client_credentials_token", _fetch)
    auth = _oauth2_auth()

    assert oauth.get_cached_oauth2_token(auth) == "fresh-token"

    with oauth._OAUTH2_TOKEN_LOCK:
        oauth._OAUTH2_TOKEN_CACHE.clear()

    # Secondo "pod": la cache locale e' vuota ma il token e' nello store
    # distribuito -> riuso senza nuovo fetch.
    assert oauth.get_cached_oauth2_token(auth) == "fresh-token"
    assert len(calls) == 1
    # Un solo write del token sullo store (la chiave token, non il lock).
    token_writes = [c for c in store.set_calls if c[0].startswith("plenora:connections:token:")]
    assert len(token_writes) == 1
    # Il refresh ha acquisito il lock distribuito (owner-safe).
    assert any(owner is not None for _key, owner in store.lock_calls)

