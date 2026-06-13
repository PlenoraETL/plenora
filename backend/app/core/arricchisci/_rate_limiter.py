"""
Rate limiter thread-safe per arricchisci/enrichment.

Usa il limiter locale storico di default. Se configurato
(ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED), usa un bucket distribuito GCRA per
quote globali tra worker/processi, con fallback locale salvo
ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED=True.

Plenora 2.0 Fase 4: il bucket distribuito GCRA e' su PostgreSQL (tabella
rate_limit_gcra) al posto della Lua Redis. Lo stato GCRA per-scope e' un
singolo `tat_ms`; l'acquire e' un upsert atomico. Port algoritmico proprio
(non passa dal KeyValueStore: serve il read-modify-write atomico).
I nomi config con "REDIS" restano per compat dei deploy.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Mapping, MutableMapping, Optional, Protocol

from app.config import settings
from app.db.pg_connection import unit_of_work
from app.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_SCOPE = "default"
_TTL_BUFFER_SECONDS = 60

_backend_lock = threading.Lock()
_backend: Optional["_GcraBackend"] = None
_backend_explicit = False
_warned_fallback = False


class _GcraBackend(Protocol):
    """Bucket distribuito GCRA: ritorna i ms di attesa per rispettare il rate."""

    def acquire_wait_ms(self, key: str, *, interval_ms: int) -> int:
        ...


class PgGcraBackend:
    """GCRA su PostgreSQL — upsert atomico (un solo statement).

    effective_tat = GREATEST(tat_ms, now_ms); wait = effective_tat - now_ms;
    nuovo tat_ms = effective_tat + interval_ms. RETURNING il wait."""

    def acquire_wait_ms(self, key: str, *, interval_ms: int) -> int:
        ttl_sec = _TTL_BUFFER_SECONDS + max(1, interval_ms // 1000 + 1)
        with unit_of_work() as session:
            with session.cursor() as cur:
                cur.execute(
                    "INSERT INTO rate_limit_gcra (key, tat_ms, updated_at, expires_at) "
                    "VALUES (%(key)s, "
                    "(extract(epoch from now()) * 1000)::bigint + %(interval)s, "
                    "now(), now() + (%(ttl)s * interval '1 second')) "
                    "ON CONFLICT (key) DO UPDATE SET "
                    "tat_ms = GREATEST("
                    "rate_limit_gcra.tat_ms, "
                    "(extract(epoch from now()) * 1000)::bigint) + %(interval)s, "
                    "updated_at = now(), "
                    "expires_at = now() + (%(ttl)s * interval '1 second') "
                    "RETURNING tat_ms - %(interval)s "
                    "- (extract(epoch from now()) * 1000)::bigint AS wait_ms",
                    {"key": key, "interval": interval_ms, "ttl": ttl_sec},
                )
                row = cur.fetchone()
        return max(0, int(row["wait_ms"])) if row else 0


class InMemoryGcraBackend:
    """GCRA in-process: test double + semantica di riferimento."""

    def __init__(self, clock_ms: Optional[Any] = None) -> None:
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.Lock()
        self._tat: MutableMapping[str, int] = {}

    def acquire_wait_ms(self, key: str, *, interval_ms: int) -> int:
        with self._lock:
            now_ms = self._clock_ms()
            effective_tat = max(self._tat.get(key, now_ms), now_ms)
            wait_ms = effective_tat - now_ms
            self._tat[key] = effective_tat + interval_ms
            return max(0, wait_ms)


def _setting_bool(name: str, default: bool = False) -> bool:
    try:
        return bool(settings.get(name, default))
    except Exception:
        return default


def _setting_str(name: str, default: str) -> str:
    try:
        value = settings.get(name, default)
        return default if value is None else str(value)
    except Exception:
        return default


def _get_backend() -> Optional["_GcraBackend"]:
    global _backend
    if _backend_explicit:
        return _backend
    if _backend is not None:
        return _backend
    with _backend_lock:
        if _backend_explicit or _backend is not None:
            return _backend
        _backend = PgGcraBackend()
        return _backend


def _warn_fallback_once(reason: str) -> None:
    global _warned_fallback
    if not _warned_fallback:
        _warned_fallback = True
        logger.warning(
            "arricchisci rate limiter: backend distribuito non disponibile "
            "(%s); fallback locale attivo",
            reason,
        )


def rate_limit_scope_for_api_config(api_config: Mapping[str, Any]) -> str:
    endpoint = api_config.get("endpoint") or {}
    if not isinstance(endpoint, dict):
        endpoint = {}
    raw = "|".join(
        str(part or "")
        for part in (
            api_config.get("id"),
            api_config.get("api_id"),
            api_config.get("name"),
            endpoint.get("method"),
            endpoint.get("url"),
        )
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"api:{digest}"


class RateLimiter:
    """
    Rate limiter thread-safe con algoritmo token bucket.

    Garantisce che le chiamate API non superino il rate specificato.
    """

    def __init__(
        self,
        rate: float,
        *,
        scope: str = _DEFAULT_SCOPE,
        distributed_enabled: Optional[bool] = None,
        distributed_required: Optional[bool] = None,
    ):
        """
        Args:
            rate: Richieste al secondo (es. 5.0 = 5 req/s)
        """
        self.rate = rate
        self.interval = 1.0 / rate if rate > 0 else 0
        self.lock = threading.Lock()
        self.last_call = 0.0
        self.scope = scope or _DEFAULT_SCOPE
        self.distributed_enabled = (
            _setting_bool("ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED", False)
            if distributed_enabled is None
            else bool(distributed_enabled)
        )
        self.distributed_required = (
            _setting_bool("ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED", False)
            if distributed_required is None
            else bool(distributed_required)
        )
        prefix = _setting_str(
            "ARRICCHISCI_RATE_LIMIT_REDIS_KEY_PREFIX",
            "plenora:arricchisci:rl",
        ).rstrip(":")
        self.bucket_key = f"{prefix}:{self.scope}"

    def acquire(self):
        """Attende se necessario per rispettare il rate limit."""
        if self.interval == 0:
            return

        if self.distributed_enabled:
            try:
                self._acquire_distributed()
                return
            except Exception as e:
                if self.distributed_required:
                    raise RuntimeError(
                        f"arricchisci distributed rate limiter unavailable: {e}"
                    ) from e
                _warn_fallback_once(str(e))

        with self.lock:
            now = time.time()
            time_since_last = now - self.last_call

            if time_since_last < self.interval:
                sleep_time = self.interval - time_since_last
                time.sleep(sleep_time)

            self.last_call = time.time()

    def _acquire_distributed(self) -> None:
        backend = _get_backend()
        if backend is None:
            raise RuntimeError("distributed rate limiter backend unavailable")
        interval_ms = max(1, int(self.interval * 1000))
        wait_ms = backend.acquire_wait_ms(self.bucket_key, interval_ms=interval_ms)
        if wait_ms > 0:
            time.sleep(wait_ms / 1000.0)


def _set_backend_for_tests(backend: Optional["_GcraBackend"]) -> None:
    global _backend, _backend_explicit
    with _backend_lock:
        _backend = backend
        _backend_explicit = True


def _reset_backend_for_tests() -> None:
    global _backend, _backend_explicit
    with _backend_lock:
        _backend = None
        _backend_explicit = False


def _reset_state_for_tests() -> None:
    global _warned_fallback
    _warned_fallback = False
