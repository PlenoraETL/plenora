"""InMemoryKeyValueStore — implementazione in-process del KeyValueStore.

Doppio ruolo:
- fake nei test (semantica identica al backend reale, zero rete);
- livello di cache davanti a PostgreSQL per strutture ad alta frequenza
  come la blacklist JWT. PG resta la sorgente di verita'.

Thread-safe; le scadenze usano `time.monotonic` (iniettabile nei test).
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class InMemoryKeyValueStore:
    """KeyValueStore in-process con TTL, thread-safe."""

    def __init__(self, clock: Optional[Callable[[], float]] = None) -> None:
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        # key -> (value, expires_at_or_None)
        self._data: dict[str, tuple[str, Optional[float]]] = {}

    def _purge_expired_locked(self) -> None:
        now = self._clock()
        expired = [
            key
            for key, (_, expires_at) in self._data.items()
            if expires_at is not None and now >= expires_at
        ]
        for key in expired:
            del self._data[key]

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            self._purge_expired_locked()
            entry = self._data.get(key)
            return entry[0] if entry else None

    def set(self, key: str, value: str, *, ttl_seconds: Optional[int] = None) -> None:
        expires_at = (
            self._clock() + ttl_seconds if ttl_seconds is not None else None
        )
        with self._lock:
            self._data[key] = (value, expires_at)

    def delete(self, key: str) -> bool:
        with self._lock:
            self._purge_expired_locked()
            return self._data.pop(key, None) is not None

    def exists(self, key: str) -> bool:
        with self._lock:
            self._purge_expired_locked()
            return key in self._data

    def scan_keys(self, prefix: str) -> list[str]:
        with self._lock:
            self._purge_expired_locked()
            return sorted(k for k in self._data if k.startswith(prefix))

    def acquire_lock(
        self, key: str, *, ttl_seconds: int, owner: Optional[str] = None
    ) -> bool:
        with self._lock:
            self._purge_expired_locked()
            if key in self._data:
                return False
            self._data[key] = (owner or "1", self._clock() + ttl_seconds)
            return True

    def release_lock(self, key: str, *, owner: Optional[str] = None) -> None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return
            # Owner-safe: con owner valorizzato cancella solo se sei tu il
            # proprietario corrente (no release del lock altrui dopo takeover).
            if owner is None or entry[0] == owner:
                self._data.pop(key, None)


__all__ = ["InMemoryKeyValueStore"]
