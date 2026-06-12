"""Pending task metadata store contracts."""

from __future__ import annotations

from pathlib import Path
from tests2.shared._ast_identifiers import all_identifiers

import pytest

from app.core.orchestrator.task_metadata_store import (
    InMemoryTaskMetadataStore,
    PendingTaskMetadata,
    RedisTaskMetadataStore,
    TaskMetadataStoreUnavailable,
    default_task_metadata_store,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.orchestrator,
    pytest.mark.contract,
    pytest.mark.regression,
]


# Plenora 2.0 (Fase 0): il store consuma il protocol condiviso
# app.shared.kv.KeyValueStore — il contratto si sposta sul mapping
# chiave/TTL/JSON verso il KV iniettato e sul default Redis-backed.


class _FakeKeyValueStore:
    def __init__(self) -> None:
        self.set_calls = []
        self.values = {}

    def set(self, key, value, *, ttl_seconds=None):
        self.set_calls.append((key, value, ttl_seconds))
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)


def test_store_maps_key_ttl_and_json_onto_kv_store():
    fake_kv = _FakeKeyValueStore()
    store = RedisTaskMetadataStore(
        "redis://broker:6379/9",
        ttl_seconds=123,
        timeout_seconds=4,
        kv_store=fake_kv,
    )
    metadata = PendingTaskMetadata(
        task_id="task-1",
        project_id="project-1",
        task_type="carica",
    )

    store.put(metadata)

    assert fake_kv.set_calls == [
        (
            "plenora:orchestrator:task_metadata:task-1",
            '{"task_id":"task-1","project_id":"project-1","task_type":"carica"}',
            123,
        )
    ]
    assert store.get("task-1") == metadata


def test_default_kv_backend_is_redis_with_broker_url_and_timeout():
    from app.shared.kv.redis_store import RedisKeyValueStore

    store = RedisTaskMetadataStore(
        "redis://broker:6379/9",
        ttl_seconds=123,
        timeout_seconds=4,
    )

    assert isinstance(store._kv, RedisKeyValueStore)
    assert store._kv._url == "redis://broker:6379/9"
    assert store._kv._timeout_seconds == 4


def test_task_metadata_store_default_runtime_has_no_local_fallback_wrapper():
    import app.core.orchestrator.task_metadata_store as task_metadata_store

    source = Path(task_metadata_store.__file__).read_text(encoding="utf-8")

    assert "_LOCAL_FALLBACK_ENVS" not in all_identifiers(source)
    assert "TaskMetadataStoreWithDevFallback" not in all_identifiers(source)
    assert "default_task_metadata_store" in all_identifiers(source)
    # RAW: statement pattern (multi-token return expression, not a plain identifier)
    assert "return RedisTaskMetadataStore(" in source


def test_default_task_metadata_store_is_redis_only(monkeypatch):
    from app.core.orchestrator import task_metadata_store as module

    created = []

    class FakeRedisStore:
        def __init__(self, broker_url, *, ttl_seconds, timeout_seconds):
            created.append((broker_url, ttl_seconds, timeout_seconds))

    monkeypatch.setattr(module, "RedisTaskMetadataStore", FakeRedisStore)
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://broker:6379/0")
    monkeypatch.setenv("CELERY_RESULT_EXPIRES", "456")
    monkeypatch.setenv("READINESS_REDIS_TIMEOUT_SECONDS", "7")

    store = default_task_metadata_store()

    assert isinstance(store, FakeRedisStore)
    assert created == [("redis://broker:6379/0", 456, 7)]


def test_redis_task_metadata_store_put_fails_loud_when_unavailable():
    class BrokenStore:
        def put(self, metadata):
            raise TaskMetadataStoreUnavailable("redis down")

    metadata = PendingTaskMetadata(
        task_id="task-1",
        project_id="project-1",
        task_type="execute",
    )
    with pytest.raises(TaskMetadataStoreUnavailable):
        BrokenStore().put(metadata)


def test_in_memory_task_metadata_store_is_explicit_test_double_only():
    metadata = PendingTaskMetadata(
        task_id="task-1",
        project_id="project-1",
        task_type="execute",
    )
    store = InMemoryTaskMetadataStore()

    store.put(metadata)

    assert store.get("task-1") == metadata
