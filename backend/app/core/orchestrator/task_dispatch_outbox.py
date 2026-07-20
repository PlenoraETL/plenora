"""Transactional PostgreSQL outbox for Dramatiq task submissions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, Protocol

from app.db.pg_connection import unit_of_work
from app.utils.logger import get_logger

from .task_metadata_store import PendingTaskMetadata

logger = get_logger(__name__)

_METADATA_PREFIX = "plenora:orchestrator:task_metadata:"


@dataclass(frozen=True)
class PendingDispatch:
    task_id: str
    actor_name: str
    options: dict[str, object]
    attempts: int


class TaskDispatchOutbox(Protocol):
    def prepare(
        self,
        metadata: PendingTaskMetadata,
        *,
        actor_name: str,
        options: Mapping[str, object],
    ) -> None: ...

    def mark_published(self, task_id: str) -> None: ...

    def mark_retry(self, task_id: str, error: BaseException) -> None: ...


class PgTaskDispatchOutbox:
    """Atomically persist pending metadata and a broker dispatch intent."""

    def __init__(self, *, metadata_ttl_seconds: int) -> None:
        self._metadata_ttl_seconds = max(1, int(metadata_ttl_seconds))

    def prepare(
        self,
        metadata: PendingTaskMetadata,
        *,
        actor_name: str,
        options: Mapping[str, object],
    ) -> None:
        metadata_payload = json.dumps(
            {
                "task_id": metadata.task_id,
                "project_id": metadata.project_id,
                "task_type": metadata.task_type,
                "subject_kind": metadata.subject_kind,
                "subject_id": metadata.subject_id,
                "group_id": metadata.group_id,
                "owner_user_id": metadata.owner_user_id,
            },
            separators=(",", ":"),
        )
        options_payload = json.dumps(dict(options), separators=(",", ":"))
        with unit_of_work() as session:
            session.execute(
                "INSERT INTO kv_store (key, value, expires_at, updated_at) "
                "VALUES (%s, %s, now() + (%s * interval '1 second'), now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                "expires_at = EXCLUDED.expires_at, updated_at = now()",
                (
                    f"{_METADATA_PREFIX}{metadata.task_id}",
                    metadata_payload,
                    self._metadata_ttl_seconds,
                ),
            )
            session.execute(
                "INSERT INTO task_dispatch_outbox "
                "(task_id, actor_name, options, next_attempt_at) "
                "VALUES (%s, %s, %s::jsonb, now() + interval '5 seconds')",
                (metadata.task_id, actor_name, options_payload),
            )

    def mark_published(self, task_id: str) -> None:
        with unit_of_work() as session:
            session.execute(
                "DELETE FROM task_dispatch_outbox WHERE task_id = %s",
                (task_id,),
            )

    def mark_retry(self, task_id: str, error: BaseException) -> None:
        # Le eccezioni broker possono includere l'URL AMQP con credenziali.
        safe_error = type(error).__name__
        with unit_of_work() as session:
            session.execute(
                "UPDATE task_dispatch_outbox SET next_attempt_at = now(), "
                "last_error = %s WHERE task_id = %s",
                (safe_error, task_id),
            )

    def claim_due(self, *, limit: int = 100) -> list[PendingDispatch]:
        with unit_of_work() as session:
            # Un task oltre la stessa retention del metadata non e' piu'
            # osservabile dal client: rimuovi l'intento per evitare crescita
            # infinita in caso di actor eliminato o broker fermo a lungo.
            session.execute(
                "DELETE FROM task_dispatch_outbox "
                "WHERE created_at <= now() - (%s * interval '1 second')",
                (self._metadata_ttl_seconds,),
            )
            rows = session.fetchall(
                "WITH due AS ("
                " SELECT task_id FROM task_dispatch_outbox"
                " WHERE next_attempt_at <= now()"
                " ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT %s"
                ") UPDATE task_dispatch_outbox AS o"
                " SET attempts = o.attempts + 1,"
                " next_attempt_at = now() + interval '60 seconds'"
                " FROM due WHERE o.task_id = due.task_id"
                " RETURNING o.task_id, o.actor_name, o.options, o.attempts",
                (max(1, min(int(limit), 1000)),),
            )
        pending: list[PendingDispatch] = []
        for row in rows:
            raw_options = row["options"]
            options = (
                json.loads(raw_options)
                if isinstance(raw_options, str)
                else dict(raw_options)
            )
            pending.append(
                PendingDispatch(
                    task_id=str(row["task_id"]),
                    actor_name=str(row["actor_name"]),
                    options=options,
                    attempts=int(row["attempts"]),
                )
            )
        return pending


def default_task_dispatch_outbox() -> PgTaskDispatchOutbox:
    from app.config import settings

    return PgTaskDispatchOutbox(metadata_ttl_seconds=settings.TASK_RESULT_EXPIRES)


def drain_task_dispatch_outbox(*, limit: int = 100) -> tuple[int, int]:
    """Publish due intents. Returns ``(published, failed)``."""
    from app.core.task_runtime.dispatch import dispatch_task_by_name

    outbox = default_task_dispatch_outbox()
    published = 0
    failed = 0
    for item in outbox.claim_due(limit=limit):
        try:
            result = dispatch_task_by_name(
                item.actor_name,
                task_id=item.task_id,
                **item.options,
            )
            if result.id != item.task_id:
                raise RuntimeError("message_id differente dall'outbox task_id")
            outbox.mark_published(item.task_id)
            published += 1
        except Exception as exc:
            failed += 1
            outbox.mark_retry(item.task_id, exc)
            logger.warning(
                "Outbox dispatch fallito task_id=%s attempt=%s error=%s",
                item.task_id,
                item.attempts,
                type(exc).__name__,
            )
    return published, failed


__all__ = [
    "PendingDispatch",
    "PgTaskDispatchOutbox",
    "TaskDispatchOutbox",
    "default_task_dispatch_outbox",
    "drain_task_dispatch_outbox",
]
