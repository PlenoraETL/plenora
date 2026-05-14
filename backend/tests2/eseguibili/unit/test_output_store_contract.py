"""Contract tests for eseguibili task-scoped output_store."""

from __future__ import annotations

from io import BytesIO

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]


class _FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.list_response: list[dict[str, object]] = []

    def upload_bytes(self, data: bytes, key: str) -> None:
        self.objects[key] = data

    def get_object_stream(self, key: str) -> dict:
        if key not in self.objects:
            raise KeyError(key)
        return {"body": BytesIO(self.objects[key]), "size": len(self.objects[key])}

    def list_objects(self, prefix: str) -> list[dict[str, object]]:
        return list(self.list_response)

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


@pytest.fixture
def fake_storage(monkeypatch):
    fake = _FakeStorage()
    from app.core.eseguibili import storage

    monkeypatch.setattr(storage, "_storage", lambda: fake)
    return fake


def test_save_and_load_task_owner_uses_object_storage_sidecar(fake_storage, tmp_path):
    from app.core.eseguibili import output_store

    output_store.save_task_owner(
        "task-1",
        "exe-1",
        "user-1",
        outputs_dir=tmp_path,
    )

    key = "eseguibili-outputs/task-1/__owner.json"
    assert key in fake_storage.objects
    assert (tmp_path / ".owners" / "task-1.json").exists()

    owner = output_store.load_task_owner("task-1", outputs_dir=tmp_path)

    assert owner is not None
    assert owner["task_id"] == "task-1"
    assert owner["eseguibile_id"] == "exe-1"
    assert owner["user_id"] == "user-1"


def test_find_output_keys_filters_owner_sidecar(fake_storage):
    from app.core.eseguibili import output_store

    fake_storage.list_response = [
        {"key": "eseguibili-outputs/task-1/__owner.json", "size": 100},
        {"key": "eseguibili-outputs/task-1/out.csv", "size": 5},
    ]

    assert output_store.find_output_keys("task-1") == [
        "eseguibili-outputs/task-1/out.csv",
    ]


def test_delete_task_outputs_cleans_owner_even_if_storage_delete_fails(
    monkeypatch,
    fake_storage,
):
    from app.core.eseguibili import output_store
    from app.core.eseguibili import storage

    deleted_owner: list[str] = []
    monkeypatch.setattr(
        storage,
        "delete_by_task",
        lambda task_id: (_ for _ in ()).throw(RuntimeError("storage down")),
    )
    monkeypatch.setattr(
        output_store,
        "delete_task_owner",
        lambda task_id: deleted_owner.append(task_id),
    )

    with pytest.raises(RuntimeError, match="storage down"):
        output_store.delete_task_outputs("task-1")

    assert deleted_owner == ["task-1"]


def test_invalid_remote_owner_metadata_fails_closed(fake_storage, tmp_path):
    from app.core.eseguibili import output_store

    fake_storage.objects["eseguibili-outputs/task-1/__owner.json"] = b"not-json"
    (tmp_path / ".owners").mkdir()
    (tmp_path / ".owners" / "task-1.json").write_text(
        '{"task_id":"task-1","eseguibile_id":"exe-1","user_id":"user-1"}',
        encoding="utf-8",
    )

    assert output_store.load_task_owner("task-1", outputs_dir=tmp_path) is None


def test_purge_expired_local_outputs_removes_only_expired(fake_storage, tmp_path):
    import os
    from app.core.eseguibili import output_store

    now = 2_000_000.0
    fresh = tmp_path / "fresh"
    expired = tmp_path / "expired"
    owners = tmp_path / ".owners"
    fresh.mkdir()
    expired.mkdir()
    owners.mkdir()
    (owners / "expired.json").write_text("{}", encoding="utf-8")

    os.utime(fresh, (now - 60, now - 60))
    os.utime(expired, (now - 2 * 3600 - 10, now - 2 * 3600 - 10))

    removed = output_store.purge_expired_local_outputs(
        outputs_dir=tmp_path,
        now_ts=now,
        ttl_seconds=2 * 3600,
    )

    assert removed == 1
    assert fresh.exists()
    assert not expired.exists()
    assert not (owners / "expired.json").exists()
    assert owners.exists()
