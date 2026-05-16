"""Pending task metadata store contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.orchestrator.task_metadata_store import (
    InMemoryTaskMetadataStore,
    PendingTaskMetadata,
    RedisTaskMetadataStore,
    TaskMetadataStoreUnavailable,
    TaskMetadataStoreWithDevFallback,
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


def test_task_metadata_store_local_fallback_is_dev_test_only():
    class BrokenStore:
        def put(self, metadata):
            raise TaskMetadataStoreUnavailable("redis down")

        def get(self, task_id):
            return None

    metadata = PendingTaskMetadata(
        task_id="task-1",
        project_id="project-1",
        task_type="execute",
    )
    fallback = InMemoryTaskMetadataStore()
    dev_store = TaskMetadataStoreWithDevFallback(
        BrokenStore(),
        fallback,
        fallback_allowed=True,
    )

    dev_store.put(metadata)
    assert dev_store.get("task-1") == metadata

    prod_store = TaskMetadataStoreWithDevFallback(
        BrokenStore(),
        InMemoryTaskMetadataStore(),
        fallback_allowed=False,
    )
    with pytest.raises(TaskMetadataStoreUnavailable):
        prod_store.put(metadata)

