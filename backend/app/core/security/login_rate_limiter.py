"""
Login rate limiter — sliding window su PostgreSQL con fallback in-memory.

Plenora 2.0 Fase 4: il backend distribuito e' PostgreSQL (tabella
login_attempts) al posto della ZSET Redis. Port algoritmico proprio (non
passa dal KeyValueStore): ogni tentativo fallito e' una riga (ip, attempt_at),
il check conta le righe nella finestra. Su backend giu' -> fallback in-memory
single-node (come oggi), o fail-closed se LOGIN_RL_DISTRIBUTED_REQUIRED.
"""

from __future__ import annotations

import threading
import time
from typing import Optional, Callable, Protocol, Tuple

from fastapi import HTTPException, status

from app.db.pg_connection import unit_of_work
from app.utils.logger import get_logger

logger = get_logger(__name__)

_TTL_BUFFER_SECONDS = 60

_store_lock = threading.Lock()
_store: Optional["_AttemptsStore"] = None
_store_explicit: bool = False
# emit-once: evita rumore su ogni request quando il backend e' giu'.
_warned_fallback: bool = False


class _AttemptsStore(Protocol):
    """Backend della sliding window dei tentativi di login."""

    def check(
        self, ip: str, *, max_attempts: int, window_s: int, lockout_s: int
    ) -> Tuple[bool, int]:
        ...

    def record(self, ip: str, *, window_s: int) -> None:
        ...

    def clear(self, ip: str) -> None:
        ...


class PgLoginAttemptsStore:
    """Sliding window su PostgreSQL (tabella login_attempts)."""

    def check(
        self, ip: str, *, max_attempts: int, window_s: int, lockout_s: int
    ) -> Tuple[bool, int]:
        with unit_of_work() as session:
            row = session.fetchone(
                "SELECT count(*) AS n, "
                "extract(epoch FROM min(attempt_at)) AS oldest "
                "FROM login_attempts "
                "WHERE ip = %s AND attempt_at > now() - (%s * interval '1 second')",
                (ip, window_s),
            )
        count = int(row["n"]) if row else 0
        if count >= max_attempts:
            oldest = (
                float(row["oldest"]) if row and row["oldest"] is not None else None
            )
            if oldest is not None:
                retry_after = int(lockout_s - (time.time() - oldest))
            else:
                retry_after = lockout_s
            return True, max(retry_after, 1)
        return False, 0

    def record(self, ip: str, *, window_s: int) -> None:
        with unit_of_work() as session:
            session.execute(
                "INSERT INTO login_attempts (ip, attempt_at) VALUES (%s, now())",
                (ip,),
            )
            # Cleanup opportunistico delle righe fuori finestra per questo ip.
            session.execute(
                "DELETE FROM login_attempts "
                "WHERE ip = %s AND attempt_at <= now() - (%s * interval '1 second')",
                (ip, window_s + _TTL_BUFFER_SECONDS),
            )

    def clear(self, ip: str) -> None:
        with unit_of_work() as session:
            session.execute("DELETE FROM login_attempts WHERE ip = %s", (ip,))


class InMemoryLoginAttemptsStore:
    """Sliding window in-process (test double + semantica di riferimento)."""

    def __init__(self, clock: Optional[Callable[[], float]] = None) -> None:
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}

    def check(
        self, ip: str, *, max_attempts: int, window_s: int, lockout_s: int
    ) -> Tuple[bool, int]:
        with self._lock:
            now = self._clock()
            cutoff = now - window_s
            attempts = [t for t in self._attempts.get(ip, []) if t > cutoff]
            self._attempts[ip] = attempts
            if len(attempts) >= max_attempts:
                oldest = min(attempts)
                return True, max(int(lockout_s - (now - oldest)), 1)
            return False, 0

    def record(self, ip: str, *, window_s: int) -> None:
        with self._lock:
            self._attempts.setdefault(ip, []).append(self._clock())

    def clear(self, ip: str) -> None:
        with self._lock:
            self._attempts.pop(ip, None)


def _required() -> bool:
    try:
        from app.config import settings

        return bool(settings.get("LOGIN_RL_DISTRIBUTED_REQUIRED", False))
    except Exception:
        return False


def _get_store() -> Optional["_AttemptsStore"]:
    global _store
    if _store_explicit:
        return _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store_explicit or _store is not None:
            return _store
        _store = PgLoginAttemptsStore()
        return _store


def _warn_fallback_once(reason: str) -> None:
    global _warned_fallback
    if not _warned_fallback:
        _warned_fallback = True
        logger.warning(
            "login rate limiter: backend non disponibile (%s) — "
            "attivo fallback in-memory (comportamento single-node preservato)",
            reason,
        )


def _fail_closed(operation: str, reason: str) -> None:
    logger.error(
        "login rate limiter: backend required, %s denied (%s)",
        operation,
        reason,
    )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Login rate limiter non disponibile.",
    )


def check_rate_limit(
    ip: str,
    *,
    max_attempts: int,
    window_seconds: int,
    lockout_seconds: int,
    fallback: Callable[[str], None],
) -> None:
    """Solleva HTTPException(429) se `ip` ha superato il limite.

    Tenta il backend PG; in caso di errore/unavailable delega a `fallback(ip)`
    (in-memory single-node), o fail-closed se LOGIN_RL_DISTRIBUTED_REQUIRED.
    """
    try:
        store = _get_store()
        if store is None:
            raise RuntimeError("attempts store unavailable")
        limited, retry_after = store.check(
            ip,
            max_attempts=max_attempts,
            window_s=window_seconds,
            lockout_s=lockout_seconds,
        )
    except Exception as e:
        if _required():
            _fail_closed("check", str(e))
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
        store = _get_store()
        if store is None:
            raise RuntimeError("attempts store unavailable")
        store.record(ip, window_s=window_seconds)
    except Exception as e:
        if _required():
            _fail_closed("record", str(e))
        _warn_fallback_once(str(e))
        fallback(ip)


def clear_failed_logins(
    ip: str,
    *,
    fallback: Callable[[str], None],
) -> None:
    try:
        store = _get_store()
        if store is None:
            raise RuntimeError("attempts store unavailable")
        store.clear(ip)
    except Exception as e:
        if _required():
            _fail_closed("clear", str(e))
        _warn_fallback_once(str(e))
        fallback(ip)


def _set_store_for_tests(store: Optional["_AttemptsStore"]) -> None:
    """Inietta un attempts store (es. InMemoryLoginAttemptsStore) o None per
    simulare il backend giu'. Setta il flag explicit cosi' il lazy-init non
    sovrascrive l'iniezione."""
    global _store, _store_explicit
    with _store_lock:
        _store = store
        _store_explicit = True


def _reset_store_for_tests() -> None:
    """Torna al lazy-init reale al prossimo `_get_store()`."""
    global _store, _store_explicit
    with _store_lock:
        _store = None
        _store_explicit = False


def _reset_state_for_tests() -> None:
    """Azzera il flag warning emit-once per re-testare il fallback."""
    global _warned_fallback
    _warned_fallback = False
