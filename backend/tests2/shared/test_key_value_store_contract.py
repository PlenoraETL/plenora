"""
Contratto KeyValueStore (Plenora 2.0, Fase 0 piano unificazione stack).

Blinda la semantica del protocol per OGNI implementazione: la stessa
batteria gira sull'in-memory (sempre) e sul backend PostgreSQL (forma SQL,
lane no-DB; comportamento reale verificato e2e). Plenora 2.0 Fase 5: il
backend Redis non esiste piu'.
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


def test_owner_safe_release_keeps_lock_held_by_another_owner(store, clock):
    # A acquisisce con owner; dopo scadenza B lo riprende. La release tardiva
    # di A (owner-safe) NON deve cancellare il lock di B.
    assert store.acquire_lock("lock:x", ttl_seconds=10, owner="A") is True
    clock.advance(11)  # il lock di A scade
    assert store.acquire_lock("lock:x", ttl_seconds=10, owner="B") is True
    store.release_lock("lock:x", owner="A")  # A tardivo: non e' piu' owner
    # Il lock di B e' ancora attivo.
    assert store.acquire_lock("lock:x", ttl_seconds=10, owner="C") is False
    store.release_lock("lock:x", owner="B")
    assert store.acquire_lock("lock:x", ttl_seconds=10, owner="C") is True


def test_release_without_owner_is_unconditional(store):
    assert store.acquire_lock("lock:y", ttl_seconds=30, owner="A") is True
    store.release_lock("lock:y")  # nessun owner -> delete incondizionato
    assert store.acquire_lock("lock:y", ttl_seconds=30, owner="B") is True


# ---------------------------------------------------------------------------
# Backend PostgreSQL (Plenora 2.0, Fase 4): semantica identica garantita dal
# contract in-memory; qui si blinda la forma SQL (lane no-DB). Il comportamento
# reale e' verificato e2e su Postgres.
# ---------------------------------------------------------------------------


def test_pg_store_satisfies_protocol():
    from app.shared.kv import PgKeyValueStore

    assert isinstance(PgKeyValueStore(), KeyValueStore)


def test_pg_store_reads_filter_expired_rows():
    import inspect

    from app.shared.kv.pg_store import PgKeyValueStore

    for method in (PgKeyValueStore.get, PgKeyValueStore.exists, PgKeyValueStore.scan_keys):
        src = inspect.getsource(method)
        assert "expires_at IS NULL OR expires_at > now()" in src, method.__name__


def test_pg_store_set_is_ttl_upsert():
    import inspect

    from app.shared.kv.pg_store import PgKeyValueStore

    src = inspect.getsource(PgKeyValueStore.set)
    assert "INSERT INTO kv_store" in src
    assert "ON CONFLICT (key) DO UPDATE" in src
    assert "interval '1 second'" in src
    # set senza ttl azzera la scadenza (expires_at NULL).
    assert "expires_at = NULL" in src


def test_pg_store_acquire_lock_is_atomic_set_if_not_exists():
    import inspect

    from app.shared.kv.pg_store import PgKeyValueStore

    src = inspect.getsource(PgKeyValueStore.acquire_lock)
    assert "ON CONFLICT (key) DO UPDATE" in src
    # Riacquisisce SOLO se scaduto (lock vivo di un altro -> non acquisito).
    assert "kv_store.expires_at <= now()" in src
    assert "RETURNING key" in src


def test_pg_store_release_lock_is_owner_safe():
    import inspect

    from app.shared.kv.pg_store import PgKeyValueStore

    src = inspect.getsource(PgKeyValueStore.release_lock)
    # Con owner: compare-and-delete (DELETE ... AND value = owner).
    assert "DELETE FROM kv_store WHERE key = %s AND value = %s" in src


def test_pg_store_scan_escapes_like_metacharacters():
    from app.shared.kv.pg_store import _like_prefix

    assert _like_prefix("a%b_c") == "a\\%b\\_c%"


def test_pg_store_module_has_no_redis_dependency():
    from pathlib import Path

    from app.shared.kv import pg_store

    src = Path(pg_store.__file__).read_text(encoding="utf-8").lower()
    assert "import redis" not in src
