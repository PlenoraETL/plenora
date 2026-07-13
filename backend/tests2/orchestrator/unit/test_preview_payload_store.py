from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from app.core.orchestrator import preview_payload_store as store


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]


class _FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.listed: list[dict] = []

    def upload_bytes(self, data: bytes, key: str) -> None:
        self.objects[key] = data

    def upload_fileobj(self, stream, key: str, *, size_bytes=None) -> None:
        position = stream.tell()
        stream.seek(0)
        data = stream.read()
        stream.seek(position)
        assert size_bytes is None or len(data) == size_bytes
        self.objects[key] = data

    def get_object_stream(self, key: str) -> dict:
        data = self.objects[key]
        return {"body": iter((data[:3], data[3:])), "size": len(data)}

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)

    def list_objects(self, prefix: str) -> list[dict]:
        return [item for item in self.listed if item["key"].startswith(prefix)]


def _patch_storage(monkeypatch) -> _FakeStorage:
    fake = _FakeStorage()
    monkeypatch.setattr(
        store.object_storage_module, "get_runtime_object_storage", lambda: fake
    )
    return fake


def test_preview_round_trip_keeps_postgres_envelope_small(monkeypatch):
    fake = _patch_storage(monkeypatch)
    payload = {
        "success": True,
        "project_id": "project-1",
        "task_id": "task_1",
        "columns": ["id", "name"],
        "rows": [{"id": 1, "name": "Ada"}],
        "preview_count": 1,
        "total_count": 10,
        "truncated": True,
    }

    envelope = store.persist_preview_payload(
        payload, project_id="project-1", task_id="task_1"
    )

    assert "rows" not in envelope
    assert envelope["preview_storage_key"] == "previews/project-1/task_1.json"
    assert json.loads(fake.objects[envelope["preview_storage_key"]]) == payload
    assert store.hydrate_preview_payload(envelope, max_bytes=1024) == payload


def test_hydrate_enforces_declared_size_cap(monkeypatch):
    fake = _patch_storage(monkeypatch)
    fake.objects["previews/p/t.json"] = b"{}"
    original = fake.get_object_stream

    def oversized(key: str) -> dict:
        value = original(key)
        value["size"] = 100
        return value

    fake.get_object_stream = oversized  # type: ignore[method-assign]
    with pytest.raises(store.PreviewPayloadError, match="supera"):
        store.hydrate_preview_payload(
            {"preview_storage_key": "previews/p/t.json"}, max_bytes=10
        )


def test_cleanup_removes_only_expired_preview_objects(monkeypatch):
    fake = _patch_storage(monkeypatch)
    now = datetime.now(timezone.utc)
    fake.listed = [
        {
            "key": "previews/p/old.json",
            "last_modified": now - timedelta(hours=2),
            "size": 2,
        },
        {
            "key": "previews/p/new.json",
            "last_modified": now - timedelta(minutes=5),
            "size": 2,
        },
    ]

    assert store.cleanup_expired_preview_payloads(ttl_seconds=3600) == 1
    assert fake.deleted == ["previews/p/old.json"]


def test_storage_key_rejects_path_traversal(monkeypatch):
    _patch_storage(monkeypatch)
    with pytest.raises(store.PreviewPayloadError, match="project_id"):
        store.persist_preview_payload({}, project_id="../other", task_id="task")
