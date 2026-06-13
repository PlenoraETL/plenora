"""RedisKeyValueStore — implementazione Redis del KeyValueStore.

Backend di transizione: e' l'implementazione corrente (stack 1.x) che i
consumer usano attraverso il protocol, cosi' in Fase 4 lo scambio con il
backend PostgreSQL non li tocca. Client lazy con timeout corti, come gli
attuali moduli KV (decode_responses=True: valori str).
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from app.shared.kv.store import KeyValueStoreUnavailable


def _redis_module() -> Any:
    try:
        import redis
    except Exception as exc:  # pragma: no cover - ambiente senza redis-py
        raise KeyValueStoreUnavailable(str(exc)) from exc
    return redis


class RedisKeyValueStore:
    """KeyValueStore su Redis (from_url, client lazy, timeout corti)."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: int = 2,
    ) -> None:
        self._url = url
        self._timeout_seconds = timeout_seconds if timeout_seconds > 0 else 2
        self._client: Any | None = None
        self._client_lock = threading.Lock()

    def _redis_client(self) -> Any:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            redis = _redis_module()
            self._client = redis.from_url(
                self._url,
                socket_timeout=self._timeout_seconds,
                socket_connect_timeout=self._timeout_seconds,
                decode_responses=True,
            )
            return self._client

    # NB: i catch broad di questo modulo sono intenzionali — boundary
    # infrastrutturale: letture/cleanup fail-soft (None/False/no-op),
    # scritture fail-loud via KeyValueStoreUnavailable. redis-py e'
    # dipendenza opzionale: narrowing a RedisError imporrebbe import eager.

    def get(self, key: str) -> Optional[str]:
        try:
            value = self._redis_client().get(key)
        except Exception:
            return None
        if value is None:
            return None
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    def set(self, key: str, value: str, *, ttl_seconds: Optional[int] = None) -> None:
        try:
            if ttl_seconds is None:
                self._redis_client().set(key, value)
            else:
                self._redis_client().setex(key, ttl_seconds, value)
        except Exception as exc:
            raise KeyValueStoreUnavailable(str(exc)) from exc

    def delete(self, key: str) -> bool:
        try:
            return bool(self._redis_client().delete(key))
        except Exception:
            return False

    def exists(self, key: str) -> bool:
        try:
            return bool(self._redis_client().exists(key))
        except Exception:
            return False

    def scan_keys(self, prefix: str) -> list[str]:
        try:
            client = self._redis_client()
            keys = [
                k.decode("utf-8") if isinstance(k, bytes) else str(k)
                for k in client.scan_iter(match=f"{prefix}*")
            ]
            return sorted(keys)
        except Exception:
            return []

    def acquire_lock(
        self, key: str, *, ttl_seconds: int, owner: Optional[str] = None
    ) -> bool:
        try:
            return bool(
                self._redis_client().set(
                    key, owner or "1", nx=True, ex=ttl_seconds
                )
            )
        except Exception as exc:
            raise KeyValueStoreUnavailable(str(exc)) from exc

    def release_lock(self, key: str, *, owner: Optional[str] = None) -> None:
        try:
            client = self._redis_client()
            if owner is None:
                client.delete(key)
                return
            # Owner-safe: cancella solo se il valore e' ancora il nostro owner.
            current = client.get(key)
            if isinstance(current, bytes):
                current = current.decode("utf-8")
            if current == owner:
                client.delete(key)
        except Exception:
            pass


__all__ = ["RedisKeyValueStore"]
