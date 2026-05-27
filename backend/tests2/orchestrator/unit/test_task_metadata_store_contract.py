"""Pending task metadata store contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


class _FakeRedisClient:
    def __init__(self) -> None:
        self.setex_calls = []
        self.values = {}

    def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)


def test_redis_task_metadata_store_uses_broker_url_ttl_and_json(monkeypatch):
    fake_client = _FakeRedisClient()
    from_url_calls = []

    def fake_from_url(url, **kwargs):
        from_url_calls.append((url, kwargs))
        return fake_client

    monkeypatch.setattr(
        "app.core.orchestrator.task_metadata_store._redis_module",
        lambda: SimpleNamespace(from_url=fake_from_url),
    )

    store = RedisTaskMetadataStore(
        "redis://broker:6379/9",
        ttl_seconds=123,
        timeout_seconds=4,
    )
    metadata = PendingTaskMetadata(
        task_id="task-1",
        project_id="project-1",
        task_type="carica",
    )

    store.put(metadata)

    assert from_url_calls == [
        (
            "redis://broker:6379/9",
            {
                "socket_timeout": 4,
                "socket_connect_timeout": 4,
                "decode_responses": True,
            },
        )
    ]
    assert fake_client.setex_calls == [
        (
            "plenora:orchestrator:task_metadata:task-1",
            123,
            '{"task_id":"task-1","project_id":"project-1","task_type":"carica"}',
        )
    ]
    assert store.get("task-1") == metadata


def test_task_metadata_store_default_runtime_has_no_local_fallback_wrapper():
    import app.core.orchestrator.task_metadata_store as task_metadata_store

    source = Path(task_metadata_store.__file__).read_text(encoding="utf-8")

    assert "_LOCAL_FALLBACK_ENVS" not in source
    assert "TaskMetadataStoreWithDevFallback" not in source
    assert "default_task_metadata_store" in source
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
