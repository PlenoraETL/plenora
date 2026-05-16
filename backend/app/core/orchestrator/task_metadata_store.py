"""Distributed metadata store for pending orchestrator tasks."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Protocol

from app.utils.logger import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "plenora:orchestrator:task_metadata:"
_DEFAULT_TTL_SECONDS = 24 * 60 * 60
_DEFAULT_TIMEOUT_SECONDS = 1
_LOCAL_FALLBACK_ENVS = {"dev", "development", "local", "test", "testing"}


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


def _redis_module() -> Any:
    try:
        import redis
    except Exception as exc:
        raise TaskMetadataStoreUnavailable(str(exc)) from exc
    return redis


def _is_local_fallback_allowed() -> bool:
    from app.config import settings

    env_name = str(
        settings.get("ENVIRONMENT", settings.get("ENV", "development"))
        or "development"
    ).lower()
    return env_name in _LOCAL_FALLBACK_ENVS or bool(settings.get("PYTEST_CURRENT_TEST"))


def _key(task_id: str) -> str:
    return f"{_KEY_PREFIX}{task_id}"


class RedisTaskMetadataStore:
    """Redis-backed store using the configured Celery broker URL."""

    def __init__(
        self,
        broker_url: str,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._broker_url = broker_url
        self._ttl_seconds = _normal_ttl(ttl_seconds)
        self._timeout_seconds = timeout_seconds if timeout_seconds > 0 else _DEFAULT_TIMEOUT_SECONDS
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
                self._broker_url,
                socket_timeout=self._timeout_seconds,
                socket_connect_timeout=self._timeout_seconds,
                decode_responses=True,
            )
            return self._client

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
            self._redis_client().setex(_key(metadata.task_id), self._ttl_seconds, payload)
        except Exception as exc:
            raise TaskMetadataStoreUnavailable(str(exc)) from exc

    def get(self, task_id: str) -> PendingTaskMetadata | None:
        try:
            raw_payload = self._redis_client().get(_key(task_id))
        except Exception as exc:
            logger.warning("Redis pending task metadata lookup failed task_id=%s: %s", task_id, exc)
            return None
        if not raw_payload:
            return None
        if isinstance(raw_payload, bytes):
            raw_payload = raw_payload.decode("utf-8")
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
    """Process-local fallback for development and unit tests only."""

    def __init__(self) -> None:
        self._metadata_by_task_id: dict[str, PendingTaskMetadata] = {}
        self._lock = threading.Lock()

    def put(self, metadata: PendingTaskMetadata) -> None:
        with self._lock:
            self._metadata_by_task_id[metadata.task_id] = metadata

    def get(self, task_id: str) -> PendingTaskMetadata | None:
        with self._lock:
            return self._metadata_by_task_id.get(task_id)


class TaskMetadataStoreWithDevFallback:
    """Prefer Redis and fall back locally only in dev/test environments."""

    def __init__(
        self,
        primary: TaskMetadataStore,
        fallback: TaskMetadataStore | None = None,
        *,
        fallback_allowed: bool | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback or InMemoryTaskMetadataStore()
        self._fallback_allowed = (
            _is_local_fallback_allowed() if fallback_allowed is None else fallback_allowed
        )

    def put(self, metadata: PendingTaskMetadata) -> None:
        try:
            self._primary.put(metadata)
            return
        except TaskMetadataStoreUnavailable:
            if not self._fallback_allowed:
                raise
            logger.warning(
                "Redis pending task metadata unavailable; using dev/test local fallback"
            )
        self._fallback.put(metadata)

    def get(self, task_id: str) -> PendingTaskMetadata | None:
        metadata = self._primary.get(task_id)
        if metadata is not None:
            return metadata
        if self._fallback_allowed:
            return self._fallback.get(task_id)
        return None


def default_task_metadata_store() -> TaskMetadataStore:
    """Build the default pending task metadata store from application settings."""
    from app.config import settings

    return TaskMetadataStoreWithDevFallback(
        RedisTaskMetadataStore(
            settings.CELERY_BROKER_URL,
            ttl_seconds=settings.CELERY_RESULT_EXPIRES,
            timeout_seconds=int(settings.get("READINESS_REDIS_TIMEOUT_SECONDS", 2) or 2),
        )
    )
