"""Annullamento task cooperativo su PostgreSQL.

L'annullamento e' un flag su PG; il task lo controlla ai checkpoint di progress
(vedi ``task_progress.raise_if_task_revoked``) e aborta sollevando
``TaskRevoked``.

Scritture via ``pg_connection.execute`` (stesso path runtime dei task,
no-oppato dall'autouse della lane → unit DB-free); letture via
``unit_of_work``. Stesso pattern di ``task_result_store``.
"""

from __future__ import annotations

import threading
from typing import Optional, Protocol, runtime_checkable

from app.db import pg_connection
from app.db.pg_connection import unit_of_work
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Il flag sopravvive abbastanza da coprire la vita del task; oltre, lo sweep
# lo rimuove. Default generoso: un task lungo non deve "de-revocarsi".
_DEFAULT_TTL_SECONDS = 24 * 60 * 60


@runtime_checkable
class TaskRevocationStore(Protocol):
    """Porta di persistenza per i flag di annullamento task."""

    def revoke(self, task_id: str, *, ttl_seconds: Optional[int] = None) -> None:
        ...

    def is_revoked(self, task_id: str) -> bool:
        ...

    def clear(self, task_id: str) -> None:
        ...

    def cleanup_expired(self) -> int:
        ...


class PgTaskRevocationStore:
    """Backend PostgreSQL (default runtime)."""

    def __init__(self, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._ttl_seconds = (
            ttl_seconds if ttl_seconds and ttl_seconds > 0 else _DEFAULT_TTL_SECONDS
        )

    def revoke(self, task_id: str, *, ttl_seconds: Optional[int] = None) -> None:
        ttl = ttl_seconds if ttl_seconds and ttl_seconds > 0 else self._ttl_seconds
        pg_connection.execute(
            """
            INSERT INTO task_revocations (task_id, revoked_at, expires_at)
            VALUES (%s, now(), now() + (%s * interval '1 second'))
            ON CONFLICT (task_id) DO UPDATE
            SET revoked_at = now(),
                expires_at = EXCLUDED.expires_at
            """,
            (task_id, ttl),
        )

    def is_revoked(self, task_id: str) -> bool:
        # Fail-soft: un errore di lettura NON deve far credere "revocato"
        # (cancellerebbe un task valido). Sul dubbio, non revocato.
        try:
            with unit_of_work() as session:
                row = session.fetchone(
                    "SELECT 1 FROM task_revocations "
                    "WHERE task_id = %s "
                    "AND (expires_at IS NULL OR expires_at > now())",
                    (task_id,),
                )
            return row is not None
        except Exception:
            logger.warning(
                "is_revoked fallita task_id=%s (fail-soft: non revocato)",
                task_id,
                exc_info=True,
            )
            return False

    def clear(self, task_id: str) -> None:
        pg_connection.execute(
            "DELETE FROM task_revocations WHERE task_id = %s", (task_id,)
        )

    def cleanup_expired(self) -> int:
        cur = pg_connection.execute(
            "DELETE FROM task_revocations "
            "WHERE expires_at IS NOT NULL AND expires_at <= now()"
        )
        return int(getattr(cur, "rowcount", 0) or 0)


class InMemoryTaskRevocationStore:
    """Test double process-local (lane no-DB)."""

    def __init__(self) -> None:
        self._revoked: set[str] = set()
        self._lock = threading.Lock()

    def revoke(self, task_id: str, *, ttl_seconds: Optional[int] = None) -> None:
        with self._lock:
            self._revoked.add(task_id)

    def is_revoked(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._revoked

    def clear(self, task_id: str) -> None:
        with self._lock:
            self._revoked.discard(task_id)

    def cleanup_expired(self) -> int:
        return 0


def default_task_revocation_store() -> TaskRevocationStore:
    """Costruisce lo store di default (PostgreSQL) dalle settings."""
    from app.config import settings

    return PgTaskRevocationStore(ttl_seconds=settings.TASK_RESULT_EXPIRES)


__all__ = [
    "InMemoryTaskRevocationStore",
    "PgTaskRevocationStore",
    "TaskRevocationStore",
    "default_task_revocation_store",
]
