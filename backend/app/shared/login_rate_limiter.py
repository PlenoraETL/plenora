"""Login rate limiter — Redis-backed sliding window con fallback in-memory.

Sostituisce/affianca il limiter in-memory in `app.core.auth.auth` (R-01).

Semantica preservata 1:1 dal vecchio limiter:
  - sliding window di `LOGIN_WINDOW_SECONDS`,
  - 429 quando i tentativi nel window >= `LOGIN_MAX_ATTEMPTS`,
  - `Retry-After = LOGIN_LOCKOUT_SECONDS - (now - oldest_in_window)`,
    clampato a >=1.

Backend Redis (cluster-safe):
  Chiave: `plenora:login_rl:<ip>` — sorted set con score = epoch ts,
  member = `<ts>:<uuid4>` per evitare collisioni di score.
  Atomicità via pipeline `MULTI`. TTL = window + 60s buffer (chiave
  auto-rimossa quando inattiva).

Policy fallback (FAIL-SOFT):
  Se il client Redis non e' disponibile (redis-py non installato,
  init fallita, errore I/O su una specifica chiamata), il limiter
  delega al `fallback` callable passato dal chiamante. Il chiamante
  in `auth.py` mantiene `_failed_attempts`/`_rate_lock` come store
  in-memory di fallback: in single-node con Redis down il
  comportamento e' identico al vecchio limiter byte-per-byte.

  Rationale: indisponibilita' Redis non deve bloccare l'auth, e
  perdere completamente la difesa (fail-open puro) sarebbe peggio
  del comportamento attuale single-node.

Test hooks:
  - `_set_client_for_tests(client)`  inietta fakeredis o None
  - `_reset_client_for_tests()`      torna al lazy-init reale
  - `_reset_state_for_tests()`       azzera il warning "emit-once"
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Callable, Optional, Tuple

from fastapi import HTTPException, status

from app.utils.logger import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "plenora:login_rl:"
_TTL_BUFFER_SECONDS = 60

_client_lock = threading.Lock()
_client: Optional[Any] = None
_client_explicit: bool = False
# Warning emit-once: evita rumore su ogni request quando Redis e' giu'.
_warned_fallback: bool = False


def _get_client() -> Optional[Any]:
    """Lazy init + cache del client Redis. None se init fallisce.

    Non solleva: il caller decide cosa fare (qui: fallback in-memory).
    """
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
            # Import locale per evitare import circolare con app.shared.
            from app.config import settings
            # BE-CONFIG-001C: default tipato in `Settings.DEFAULTS`
            # (`LOGIN_RL_REDIS_TIMEOUT_SEC=1`). `_converti_tipo_env`
            # rifiuta env non numeriche tornando il default; il guard
            # `<= 0` resta come safety per valori legali ma non sensati
            # (`0`, negativi).
            try:
                timeout = int(settings.get("LOGIN_RL_REDIS_TIMEOUT_SEC"))
                if timeout <= 0:
                    timeout = 1
            except (TypeError, ValueError):
                timeout = 1
            _client = redis.from_url(
                settings.CELERY_BROKER_URL,
                socket_timeout=timeout,
                socket_connect_timeout=timeout,
                decode_responses=True,
            )
            return _client
        except Exception as e:
            logger.warning("login_rate_limiter Redis client init failed: %s", e)
            return None


def _key(ip: str) -> str:
    return f"{_KEY_PREFIX}{ip}"


def _warn_fallback_once(reason: str) -> None:
    global _warned_fallback
    if not _warned_fallback:
        _warned_fallback = True
        logger.warning(
            "login rate limiter: Redis non disponibile (%s) — "
            "attivo fallback in-memory (comportamento single-node preservato)",
            reason,
        )


# ============================================================
# Implementazione Redis
# ============================================================


def _redis_check(
    ip: str,
    *,
    max_attempts: int,
    window_s: int,
    lockout_s: int,
) -> Tuple[bool, int]:
    """Ritorna `(limited, retry_after_s)`.

    Solleva se il client e' None o I/O Redis fallisce: il caller
    cattura e fa fallback in-memory.
    """
    client = _get_client()
    if client is None:
        raise RuntimeError("redis client unavailable")
    now = time.time()
    key = _key(ip)
    cutoff = now - window_s
    pipe = client.pipeline(transaction=True)
    pipe.zremrangebyscore(key, 0, cutoff)
    pipe.zrange(key, 0, 0, withscores=True)
    pipe.zcard(key)
    pipe.expire(key, window_s + _TTL_BUFFER_SECONDS)
    results = pipe.execute()
    oldest = results[1]
    count = int(results[2])
    if count >= max_attempts:
        if oldest:
            _, oldest_score = oldest[0]
            retry_after = int(lockout_s - (now - float(oldest_score)))
        else:
            retry_after = lockout_s
        return True, max(retry_after, 1)
    return False, 0


def _redis_record(ip: str, *, window_s: int) -> None:
    client = _get_client()
    if client is None:
        raise RuntimeError("redis client unavailable")
    now = time.time()
    key = _key(ip)
    member = f"{now}:{uuid.uuid4().hex}"
    pipe = client.pipeline(transaction=True)
    pipe.zadd(key, {member: now})
    pipe.expire(key, window_s + _TTL_BUFFER_SECONDS)
    pipe.execute()


def _redis_clear(ip: str) -> None:
    client = _get_client()
    if client is None:
        raise RuntimeError("redis client unavailable")
    client.delete(_key(ip))


# ============================================================
# API pubblica usata da `app.core.auth.auth`
# ============================================================


def check_rate_limit(
    ip: str,
    *,
    max_attempts: int,
    window_seconds: int,
    lockout_seconds: int,
    fallback: Callable[[str], None],
) -> None:
    """Solleva HTTPException(429) se `ip` ha superato il limite.

    Tenta Redis; in caso di errore/unavailable delega a `fallback(ip)`,
    che e' la stessa logica in-memory pre-R-01 vivente nel modulo
    chiamante (per backward-compat con i test che resettano `_failed_attempts`).
    """
    try:
        limited, retry_after = _redis_check(
            ip,
            max_attempts=max_attempts,
            window_s=window_seconds,
            lockout_s=lockout_seconds,
        )
    except Exception as e:
        _warn_fallback_once(str(e))
        fallback(ip)
        return
    if limited:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Troppi tentativi. Riprova tra {retry_after} secondi.",
            headers={"Retry-After": str(retry_after)},
        )


def record_failed_login(
    ip: str,
    *,
    window_seconds: int,
    fallback: Callable[[str], None],
) -> None:
    try:
        _redis_record(ip, window_s=window_seconds)
    except Exception as e:
        _warn_fallback_once(str(e))
        fallback(ip)


def clear_failed_logins(
    ip: str,
    *,
    fallback: Callable[[str], None],
) -> None:
    try:
        _redis_clear(ip)
    except Exception as e:
        _warn_fallback_once(str(e))
        fallback(ip)


# ============================================================
# Hooks test-only
# ============================================================


def _set_client_for_tests(client: Optional[Any]) -> None:
    """Inietta un client custom (es. fakeredis) o `None` per simulare
    Redis down. Setta il flag explicit cosi' il lazy-init non
    sovrascrive l'iniezione.
    """
    global _client, _client_explicit
    with _client_lock:
        _client = client
        _client_explicit = True


def _reset_client_for_tests() -> None:
    """Torna al lazy-init reale al prossimo `_get_client()`."""
    global _client, _client_explicit
    with _client_lock:
        _client = None
        _client_explicit = False


def _reset_state_for_tests() -> None:
    """Azzera il flag warning emit-once per re-testare il fallback."""
    global _warned_fallback
    _warned_fallback = False
