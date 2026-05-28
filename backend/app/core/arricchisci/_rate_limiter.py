"""
Rate limiter thread-safe per arricchisci/enrichment.

Usa il limiter locale storico di default. Se configurato, usa Redis come
bucket distribuito per quote globali tra worker/processi, con fallback locale
salvo `ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED=True`.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Mapping, Optional

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_SCOPE = "default"
_TTL_BUFFER_SECONDS = 60

_client_lock = threading.Lock()
_client: Optional[Any] = None
_client_explicit = False
_warned_fallback = False

_GCRA_LUA = """
local key = KEYS[1]
local interval_ms = tonumber(ARGV[1])
local ttl_buffer_ms = tonumber(ARGV[2])
local now_parts = redis.call("TIME")
local now_ms = (tonumber(now_parts[1]) * 1000) + math.floor(tonumber(now_parts[2]) / 1000)
local tat = tonumber(redis.call("GET", key))
if tat == nil or tat < now_ms then
  tat = now_ms
end
local wait_ms = tat - now_ms
local next_tat = tat + interval_ms
local ttl_ms = (next_tat - now_ms) + ttl_buffer_ms
redis.call("SET", key, next_tat, "PX", ttl_ms)
return wait_ms
"""


def _setting_bool(name: str, default: bool = False) -> bool:
    try:
        return bool(settings.get(name, default))
    except Exception:
        return default


def _setting_int(name: str, default: int) -> int:
    try:
        value = int(settings.get(name, default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _setting_str(name: str, default: str) -> str:
    try:
        value = settings.get(name, default)
        return default if value is None else str(value)
    except Exception:
        return default


def _get_client() -> Optional[Any]:
    global _client
    if _client_explicit:
        return _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client_explicit:
            return _client
        if _client is not None:
            return _client
        try:
            import redis  # type: ignore
        except ImportError:
            return None
        try:
            timeout = _setting_int("ARRICCHISCI_RATE_LIMIT_REDIS_TIMEOUT_SEC", 1)
            _client = redis.from_url(
                settings.CELERY_BROKER_URL,
                socket_timeout=timeout,
                socket_connect_timeout=timeout,
                decode_responses=True,
            )
            return _client
        except Exception as e:
            logger.warning("arricchisci rate limiter Redis client init failed: %s", e)
            return None


def _warn_fallback_once(reason: str) -> None:
    global _warned_fallback
    if not _warned_fallback:
        _warned_fallback = True
        logger.warning(
            "arricchisci rate limiter: Redis non disponibile (%s); "
            "fallback locale attivo",
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
    Usato sia dall'esecuzione sequenziale che parallela per rispettare
    i limiti delle API esterne (es. Nominatim: 1 req/s).
    """

    def __init__(
        self,
        rate: float,
        *,
        scope: str = _DEFAULT_SCOPE,
        redis_enabled: Optional[bool] = None,
        redis_required: Optional[bool] = None,
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
        self.redis_enabled = (
            _setting_bool("ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED", False)
            if redis_enabled is None
            else bool(redis_enabled)
        )
        self.redis_required = (
            _setting_bool("ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED", False)
            if redis_required is None
            else bool(redis_required)
        )
        prefix = _setting_str(
            "ARRICCHISCI_RATE_LIMIT_REDIS_KEY_PREFIX",
            "plenora:arricchisci:rl",
        ).rstrip(":")
        self.redis_key = f"{prefix}:{self.scope}"

    def acquire(self):
        """Attende se necessario per rispettare il rate limit."""
        if self.interval == 0:
            return

        if self.redis_enabled:
            try:
                self._acquire_redis()
                return
            except Exception as e:
                if self.redis_required:
                    raise RuntimeError(
                        f"arricchisci Redis rate limiter unavailable: {e}"
                    ) from e
                _warn_fallback_once(str(e))

        with self.lock:
            now = time.time()
            time_since_last = now - self.last_call

            if time_since_last < self.interval:
                sleep_time = self.interval - time_since_last
                time.sleep(sleep_time)

            self.last_call = time.time()

    def _acquire_redis(self) -> None:
        client = _get_client()
        if client is None:
            raise RuntimeError("redis client unavailable")
        interval_ms = max(1, int(self.interval * 1000))
        ttl_buffer_ms = _TTL_BUFFER_SECONDS * 1000
        wait_ms = int(
            client.eval(_GCRA_LUA, 1, self.redis_key, interval_ms, ttl_buffer_ms)
        )
        if wait_ms > 0:
            time.sleep(wait_ms / 1000.0)


def _set_client_for_tests(client: Optional[Any]) -> None:
    global _client, _client_explicit
    with _client_lock:
        _client = client
        _client_explicit = True


def _reset_client_for_tests() -> None:
    global _client, _client_explicit
    with _client_lock:
        _client = None
        _client_explicit = False


def _reset_state_for_tests() -> None:
    global _warned_fallback
    _warned_fallback = False
