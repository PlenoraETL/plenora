"""Distributed metadata store for pending orchestrator tasks.

Plenora 2.0 (Fase 0): primo consumer pilota del protocol condiviso
``app.shared.kv.KeyValueStore`` — questo modulo non possiede piu' un
client Redis proprio; il backend (oggi Redis, in Fase 4 PostgreSQL) e'
iniettabile e il mapping JSON/TTL resta qui.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Optional, Protocol

from app.shared.kv import KeyValueStore
from app.shared.kv.redis_store import RedisKeyValueStore
from app.utils.logger import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "plenora:orchestrator:task_metadata:"
_DEFAULT_TTL_SECONDS = 24 * 60 * 60
_DEFAULT_TIMEOUT_SECONDS = 1


class TaskMetadataStoreUnavailable(RuntimeError):
    """Raised when pending task metadata cannot be persisted."""


@dataclass(frozen=True)
class PendingTaskMetadata:
    """Minimal metadata needed before Celery result metadata exists."""

    task_id: str
    project_id: str
    task_type: str


class TaskMetadataStore(Protocol):
    """Persistence port for pending task metadata."""

    def put(self, metadata: PendingTaskMetadata) -> None:
        ...

    def get(self, task_id: str) -> PendingTaskMetadata | None:
        ...


def _normal_ttl(ttl_seconds: int | None) -> int:
    if ttl_seconds is None or ttl_seconds <= 0:
        return _DEFAULT_TTL_SECONDS
    return ttl_seconds


def _key(task_id: str) -> str:
    return f"{_KEY_PREFIX}{task_id}"


class RedisTaskMetadataStore:
    """Store su KeyValueStore (default: Redis via Celery broker URL).

    Il nome resta per compatibilita' (test e default runtime); il
    backend e' il protocol condiviso, iniettabile via ``kv_store``.
    """

    def __init__(
        self,
        broker_url: str,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        kv_store: Optional[KeyValueStore] = None,
    ) -> None:
        self._ttl_seconds = _normal_ttl(ttl_seconds)
        resolved_timeout = (
            timeout_seconds if timeout_seconds > 0 else _DEFAULT_TIMEOUT_SECONDS
        )
        self._kv: KeyValueStore = kv_store or RedisKeyValueStore(
            broker_url, timeout_seconds=resolved_timeout
        )

    def put(self, metadata: PendingTaskMetadata) -> None:
        payload = json.dumps(
            {
                "task_id": metadata.task_id,
                "project_id": metadata.project_id,
                "task_type": metadata.task_type,
            },
            separators=(",", ":"),
        )
        try:
            self._kv.set(
                _key(metadata.task_id), payload, ttl_seconds=self._ttl_seconds
            )
        except Exception as exc:
            raise TaskMetadataStoreUnavailable(str(exc)) from exc

    def get(self, task_id: str) -> PendingTaskMetadata | None:
        raw_payload = self._kv.get(_key(task_id))
        if not raw_payload:
            return None
        try:
            payload = json.loads(str(raw_payload))
        except json.JSONDecodeError:
            logger.warning("Invalid pending task metadata payload task_id=%s", task_id)
            return None
        if not isinstance(payload, dict):
            return None
        project_id = payload.get("project_id")
        task_type = payload.get("task_type")
        if not project_id or not task_type:
            return None
        return PendingTaskMetadata(
            task_id=str(payload.get("task_id") or task_id),
            project_id=str(project_id),
            task_type=str(task_type),
        )


class InMemoryTaskMetadataStore:
    """Process-local test double; never used by the default runtime store."""

    def __init__(self) -> None:
        self._metadata_by_task_id: dict[str, PendingTaskMetadata] = {}
        self._lock = threading.Lock()

    def put(self, metadata: PendingTaskMetadata) -> None:
        with self._lock:
            self._metadata_by_task_id[metadata.task_id] = metadata

    def get(self, task_id: str) -> PendingTaskMetadata | None:
        with self._lock:
            return self._metadata_by_task_id.get(task_id)


def default_task_metadata_store() -> TaskMetadataStore:
    """Build the default pending task metadata store from application settings."""
    from app.config import settings

    return RedisTaskMetadataStore(
        settings.CELERY_BROKER_URL,
        ttl_seconds=settings.CELERY_RESULT_EXPIRES,
        timeout_seconds=int(settings.get("READINESS_REDIS_TIMEOUT_SECONDS", 2) or 2),
    )
