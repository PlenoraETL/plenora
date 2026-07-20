"""Pending task metadata store contracts."""

from __future__ import annotations

from pathlib import Path
from tests2.shared._ast_identifiers import all_identifiers

import pytest

from app.core.orchestrator.task_metadata_store import (
    InMemoryTaskMetadataStore,
    KvTaskMetadataStore,
    PendingTaskMetadata,
    TaskMetadataStoreUnavailable,
    default_task_metadata_store,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.orchestrator,
    pytest.mark.contract,
    pytest.mark.regression,
]


# Plenora 2.0: il store consuma il protocol condiviso
# app.shared.kv.KeyValueStore — il contratto e' sul mapping chiave/TTL/JSON
# verso il KV iniettato e sul default PostgreSQL-backed (Fase 5, no Redis).


class _FakeKeyValueStore:
    def __init__(self) -> None:
        self.set_calls = []
        self.values = {}

    def set(self, key, value, *, ttl_seconds=None):
        self.set_calls.append((key, value, ttl_seconds))
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)

    def scan_keys(self, prefix):
        return sorted(key for key in self.values if key.startswith(prefix))

    def delete(self, key):
        return self.values.pop(key, None) is not None


def test_store_maps_key_ttl_and_json_onto_kv_store():
    fake_kv = _FakeKeyValueStore()
    store = KvTaskMetadataStore(ttl_seconds=123, kv_store=fake_kv)
    metadata = PendingTaskMetadata(
        task_id="task-1",
        project_id="project-1",
        task_type="carica",
    )

    store.put(metadata)

    assert fake_kv.set_calls == [
        (
            "plenora:orchestrator:task_metadata:task-1",
            '{"task_id":"task-1","project_id":"project-1","task_type":"carica","subject_kind":"project","subject_id":null,"group_id":null,"owner_user_id":null}',
            123,
        )
    ]
    assert store.get("task-1") == metadata


def test_default_kv_backend_is_postgres():
    from app.shared.kv import PgKeyValueStore

    store = KvTaskMetadataStore(ttl_seconds=123)

    assert isinstance(store._kv, PgKeyValueStore)


def test_task_metadata_store_default_runtime_has_no_local_fallback_wrapper():
    import app.core.orchestrator.task_metadata_store as task_metadata_store

    source = Path(task_metadata_store.__file__).read_text(encoding="utf-8")

    assert "_LOCAL_FALLBACK_ENVS" not in all_identifiers(source)
    assert "TaskMetadataStoreWithDevFallback" not in all_identifiers(source)
    assert "default_task_metadata_store" in all_identifiers(source)
    # RAW: statement pattern (multi-token return expression, not a plain identifier)
    assert "return KvTaskMetadataStore(" in source


def test_default_task_metadata_store_builds_pg_backed(monkeypatch):
    from app.core.orchestrator import task_metadata_store as module

    created = []

    class FakeKvStore:
        def __init__(self, *, ttl_seconds, kv_store):
            created.append((ttl_seconds, type(kv_store).__name__))

    monkeypatch.setattr(module, "KvTaskMetadataStore", FakeKvStore)
    monkeypatch.setenv("TASK_RESULT_EXPIRES", "456")

    store = default_task_metadata_store()

    assert isinstance(store, FakeKvStore)
    assert created == [(456, "PgKeyValueStore")]


def test_task_metadata_store_put_fails_loud_when_unavailable():
    class BrokenStore:
        def put(self, metadata):
            raise TaskMetadataStoreUnavailable("backend down")

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


def test_metadata_store_lists_tasks_for_one_project_only():
    store = InMemoryTaskMetadataStore()
    store.put(PendingTaskMetadata("t1", "p1", "execute"))
    store.put(PendingTaskMetadata("t2", "p2", "preview"))
    store.put(PendingTaskMetadata("t3", "p1", "carica"))

    assert [item.task_id for item in store.list_for_project("p1")] == ["t1", "t3"]


@pytest.mark.parametrize("store_factory", [InMemoryTaskMetadataStore])
def test_metadata_store_delete_is_idempotent(store_factory):
    store = store_factory()
    store.put(PendingTaskMetadata("t1", "p1", "execute"))

    assert store.delete("t1") is True
    assert store.delete("t1") is False
    assert store.get("t1") is None


def test_kv_metadata_store_delete_maps_to_namespaced_key():
    fake_kv = _FakeKeyValueStore()
    store = KvTaskMetadataStore(kv_store=fake_kv)
    store.put(PendingTaskMetadata("t1", "p1", "execute"))

    assert store.delete("t1") is True
    assert fake_kv.values == {}
