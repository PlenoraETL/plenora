"""
Contratto KeyValueStore (Plenora 2.0, Fase 0 piano unificazione stack).

Blinda la semantica del protocol per OGNI implementazione: la stessa
batteria gira sull'in-memory (sempre) e, in Fase 4, sul backend
PostgreSQL. Il backend Redis e' coperto a livello di mapping operazioni
(fake client) — la lane non ha un server Redis.
"""

from __future__ import annotations

import pytest

from app.shared.kv import InMemoryKeyValueStore, KeyValueStore

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture()
def store(clock: _FakeClock) -> InMemoryKeyValueStore:
    return InMemoryKeyValueStore(clock=clock)


def test_satisfies_protocol(store):
    assert isinstance(store, KeyValueStore)


def test_set_get_roundtrip(store):
    store.set("k", "v")
    assert store.get("k") == "v"
    assert store.exists("k") is True


def test_get_missing_returns_none(store):
    assert store.get("ghost") is None
    assert store.exists("ghost") is False


def test_ttl_expiry(store, clock):
    store.set("k", "v", ttl_seconds=10)
    assert store.get("k") == "v"
    clock.advance(9.9)
    assert store.get("k") == "v"
    clock.advance(0.2)
    assert store.get("k") is None
    assert store.exists("k") is False


def test_set_without_ttl_never_expires(store, clock):
    store.set("k", "v")
    clock.advance(10**6)
    assert store.get("k") == "v"


def test_set_overwrites_value_and_ttl(store, clock):
    store.set("k", "v1", ttl_seconds=5)
    store.set("k", "v2")  # niente TTL: non scade piu'
    clock.advance(10)
    assert store.get("k") == "v2"


def test_delete_is_idempotent(store):
    store.set("k", "v")
    assert store.delete("k") is True
    assert store.delete("k") is False
    assert store.get("k") is None


def test_scan_keys_by_prefix_excludes_expired(store, clock):
    store.set("app:a", "1")
    store.set("app:b", "2", ttl_seconds=5)
    store.set("other:c", "3")
    clock.advance(6)
    assert store.scan_keys("app:") == ["app:a"]


def test_lock_is_exclusive_until_released(store):
    assert store.acquire_lock("lock:x", ttl_seconds=30) is True
    assert store.acquire_lock("lock:x", ttl_seconds=30) is False
    store.release_lock("lock:x")
    assert store.acquire_lock("lock:x", ttl_seconds=30) is True


def test_lock_expires_with_ttl(store, clock):
    assert store.acquire_lock("lock:x", ttl_seconds=10) is True
    clock.advance(11)
    assert store.acquire_lock("lock:x", ttl_seconds=10) is True


# ---------------------------------------------------------------------------
# Mapping operazioni del backend Redis (fake client, niente server)
# ---------------------------------------------------------------------------


class _FakeRedisClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.values: dict[str, str] = {}

    def setex(self, key, ttl, value):
        self.calls.append(("setex", key, ttl, value))
        self.values[key] = value

    def set(self, key, value, nx=False, ex=None):
        self.calls.append(("set", key, value, nx, ex))
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        return 1 if self.values.pop(key, None) is not None else 0

    def exists(self, key):
        return 1 if key in self.values else 0

    def scan_iter(self, match):
        prefix = match.rstrip("*")
        return [k for k in self.values if k.startswith(prefix)]


@pytest.fixture()
def redis_store(monkeypatch):
    from app.shared.kv import redis_store as redis_store_module

    fake = _FakeRedisClient()
    store = redis_store_module.RedisKeyValueStore("redis://test:6379/0")
    monkeypatch.setattr(store, "_redis_client", lambda: fake)
    return store, fake


def test_redis_store_set_with_ttl_maps_to_setex(redis_store):
    store, fake = redis_store
    store.set("k", "v", ttl_seconds=60)
    assert fake.calls == [("setex", "k", 60, "v")]
    assert store.get("k") == "v"


def test_redis_store_lock_maps_to_set_nx_ex(redis_store):
    store, fake = redis_store
    assert store.acquire_lock("lock:x", ttl_seconds=30) is True
    assert store.acquire_lock("lock:x", ttl_seconds=30) is False
    assert ("set", "lock:x", "1", True, 30) in fake.calls


def test_redis_store_scan_keys(redis_store):
    store, fake = redis_store
    store.set("app:a", "1")
    store.set("zz:b", "2")
    assert store.scan_keys("app:") == ["app:a"]
