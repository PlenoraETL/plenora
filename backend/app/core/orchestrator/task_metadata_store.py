"""Distributed metadata store for pending orchestrator tasks.

Consumer del protocol condiviso ``app.shared.kv.KeyValueStore``: il modulo
non possiede un client proprio, il backend e' iniettabile e il mapping
JSON/TTL resta qui. Il default e' PgKeyValueStore (PostgreSQL).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Optional, Protocol

from app.shared.kv import KeyValueStore, KeyValueStoreUnavailable, PgKeyValueStore
from app.utils.logger import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "plenora:orchestrator:task_metadata:"
_DEFAULT_TTL_SECONDS = 24 * 60 * 60


class TaskMetadataStoreUnavailable(RuntimeError):
    """Raised when pending task metadata cannot be persisted."""


@dataclass(frozen=True)
class PendingTaskMetadata:
    """Authorization and routing metadata retained for the task lifetime."""

    task_id: str
    project_id: str
    task_type: str
    subject_kind: str = "project"
    subject_id: Optional[str] = None
    group_id: Optional[str] = None
    owner_user_id: Optional[str] = None


class TaskMetadataStore(Protocol):
    """Persistence port for pending task metadata."""

    def put(self, metadata: PendingTaskMetadata) -> None:
        ...

    def get(self, task_id: str) -> PendingTaskMetadata | None:
        ...

    def delete(self, task_id: str) -> bool:
        ...

    def list_for_project(self, project_id: str) -> list[PendingTaskMetadata]:
        ...


def _normal_ttl(ttl_seconds: int | None) -> int:
    if ttl_seconds is None or ttl_seconds <= 0:
        return _DEFAULT_TTL_SECONDS
    return ttl_seconds


def _key(task_id: str) -> str:
    return f"{_KEY_PREFIX}{task_id}"


class KvTaskMetadataStore:
    """Store sul protocol condiviso KeyValueStore (default: PostgreSQL).

    Il backend e' iniettabile via ``kv_store`` (test double in-memory);
    il default runtime usa ``PgKeyValueStore``.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        kv_store: Optional[KeyValueStore] = None,
    ) -> None:
        self._ttl_seconds = _normal_ttl(ttl_seconds)
        self._kv: KeyValueStore = kv_store or PgKeyValueStore()

    def put(self, metadata: PendingTaskMetadata) -> None:
        payload = json.dumps(
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
        try:
            self._kv.set(
                _key(metadata.task_id), payload, ttl_seconds=self._ttl_seconds
            )
        except KeyValueStoreUnavailable as exc:
            raise TaskMetadataStoreUnavailable(str(exc)) from exc

    def get(self, task_id: str) -> PendingTaskMetadata | None:
        try:
            raw_payload = self._kv.get(_key(task_id))
        except KeyValueStoreUnavailable as exc:
            raise TaskMetadataStoreUnavailable(str(exc)) from exc
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
            subject_kind=str(payload.get("subject_kind") or "project"),
            subject_id=(
                str(payload["subject_id"])
                if payload.get("subject_id") is not None
                else (None if "subject_id" in payload else str(project_id))
            ),
            group_id=(
                str(payload["group_id"])
                if payload.get("group_id") is not None
                else None
            ),
            owner_user_id=(
                str(payload["owner_user_id"])
                if payload.get("owner_user_id") is not None
                else None
            ),
        )

    def delete(self, task_id: str) -> bool:
        try:
            return self._kv.delete(_key(task_id))
        except KeyValueStoreUnavailable as exc:
            raise TaskMetadataStoreUnavailable(str(exc)) from exc

    def list_for_project(self, project_id: str) -> list[PendingTaskMetadata]:
        matches: list[PendingTaskMetadata] = []
        try:
            keys = self._kv.scan_keys(_KEY_PREFIX)
            for key in keys:
                metadata = self.get(key.removeprefix(_KEY_PREFIX))
                if metadata is not None and metadata.project_id == project_id:
                    matches.append(metadata)
        except TaskMetadataStoreUnavailable:
            raise
        except KeyValueStoreUnavailable as exc:
            raise TaskMetadataStoreUnavailable(str(exc)) from exc
        return matches


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

    def delete(self, task_id: str) -> bool:
        with self._lock:
            return self._metadata_by_task_id.pop(task_id, None) is not None

    def list_for_project(self, project_id: str) -> list[PendingTaskMetadata]:
        with self._lock:
            return [
                metadata
                for metadata in self._metadata_by_task_id.values()
                if metadata.project_id == project_id
            ]


def default_task_metadata_store() -> TaskMetadataStore:
    """Build the default pending task metadata store (PostgreSQL-backed)."""
    from app.config import settings

    return KvTaskMetadataStore(
        ttl_seconds=settings.TASK_RESULT_EXPIRES,
        kv_store=PgKeyValueStore(),
    )
