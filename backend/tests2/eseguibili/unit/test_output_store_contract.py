"""Contract tests for eseguibili task-scoped output_store."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from tests2.shared._ast_identifiers import uses_attribute


pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_STORE = _BACKEND_ROOT / "app" / "core" / "eseguibili" / "output_store.py"


class _FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.list_response: list[dict[str, object]] = []
        self.tags: dict[str, dict[str, str]] = {}

    def upload_bytes(self, data: bytes, key: str) -> None:
        self.objects[key] = data

    def set_object_tags(self, key: str, tags: dict[str, str]) -> None:
        self.tags[key] = dict(tags)

    def get_object_stream(self, key: str) -> dict:
        if key not in self.objects:
            raise KeyError(key)
        return {"body": BytesIO(self.objects[key]), "size": len(self.objects[key])}

    def list_objects(self, prefix: str) -> list[dict[str, object]]:
        return [item for item in self.list_response if str(item["key"]).startswith(prefix)]

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
        group_id="g1",
        outputs_dir=tmp_path,
    )

    key = "tenants/g1/eseguibili-outputs/task-1/__owner.json"
    assert key in fake_storage.objects
    assert not (tmp_path / ".owners" / "task-1.json").exists()

    owner = output_store.load_task_owner(
        "task-1", group_id="g1", outputs_dir=tmp_path,
    )

    assert owner is not None
    assert owner["task_id"] == "task-1"
    assert owner["eseguibile_id"] == "exe-1"
    assert owner["user_id"] == "user-1"


def test_save_task_owner_fails_closed_when_object_storage_unavailable(
    monkeypatch,
    tmp_path,
):
    from app.core.eseguibili import output_store
    from app.core.eseguibili import storage

    monkeypatch.setattr(
        storage,
        "save_bytes",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("object storage down")
        ),
    )

    with pytest.raises(RuntimeError, match="object storage down"):
        output_store.save_task_owner(
            "task-1",
            "exe-1",
            "user-1",
            group_id="g1",
            outputs_dir=tmp_path,
        )

    assert not (tmp_path / ".owners" / "task-1.json").exists()


def test_task_owner_store_has_no_local_read_write_fallback() -> None:
    src = _OUTPUT_STORE.read_text(encoding="utf-8")

    assert not uses_attribute(src, "path", "write_bytes")
    # RAW: compound statement pattern (return + nested calls, not a plain identifier)
    assert "return _load_owner_payload(path.read_bytes())" not in src


def test_find_output_keys_filters_owner_sidecar(fake_storage):
    from app.core.eseguibili import output_store

    fake_storage.list_response = [
        {"key": "eseguibili-outputs/task-1/__owner.json", "size": 100},
        {"key": "eseguibili-outputs/task-1/out.csv", "size": 5},
    ]

    assert output_store.find_output_keys("task-1", group_id="g1") == [
        "eseguibili-outputs/task-1/out.csv",
    ]


def test_delete_task_outputs_preserves_owner_if_storage_delete_fails(
    monkeypatch,
    fake_storage,
):
    from app.core.eseguibili import output_store
    from app.core.eseguibili import storage

    deleted_owner: list[str] = []
    fake_storage.list_response = [
        {"key": "tenants/g1/eseguibili-outputs/task-1/out.csv"},
    ]
    monkeypatch.setattr(
        storage,
        "delete_key",
        lambda key, **kwargs: (_ for _ in ()).throw(RuntimeError("storage down")),
    )
    monkeypatch.setattr(
        output_store,
        "delete_task_owner",
        lambda task_id, **kwargs: deleted_owner.append(task_id),
    )

    with pytest.raises(RuntimeError, match="Cleanup output incompleto"):
        output_store.delete_task_outputs("task-1", group_id="g1")

    assert deleted_owner == []


def test_select_output_key_uses_expected_filename_and_rejects_ambiguity():
    from app.core.eseguibili import output_store

    keys = [
        "tenants/g1/eseguibili-outputs/task-1/stale.csv",
        "tenants/g1/eseguibili-outputs/task-1/final.csv",
    ]
    assert output_store.select_output_key(
        keys,
        expected_filename="C:\\results\\final.csv",
    ).endswith("/final.csv")
    with pytest.raises(RuntimeError, match="ambiguo"):
        output_store.select_output_key(keys)


def test_delete_task_outputs_can_retain_owner_after_download(monkeypatch, fake_storage):
    from app.core.eseguibili import output_store

    fake_storage.list_response = [
        {"key": "tenants/g1/eseguibili-outputs/task-1/out.csv"},
        {"key": "tenants/g1/eseguibili-outputs/task-1/__owner.json"},
    ]
    deleted_owner: list[str] = []
    monkeypatch.setattr(
        output_store,
        "delete_task_owner",
        lambda task_id, **kwargs: deleted_owner.append(task_id),
    )

    assert output_store.delete_task_outputs(
        "task-1",
        group_id="g1",
        retain_owner=True,
    ) == 1
    assert deleted_owner == []


def test_invalid_remote_owner_metadata_fails_closed(fake_storage, tmp_path):
    from app.core.eseguibili import output_store

    fake_storage.objects["eseguibili-outputs/task-1/__owner.json"] = b"not-json"
    (tmp_path / ".owners").mkdir()
    (tmp_path / ".owners" / "task-1.json").write_text(
        '{"task_id":"task-1","eseguibile_id":"exe-1","user_id":"user-1"}',
        encoding="utf-8",
    )

    assert output_store.load_task_owner(
        "task-1", group_id="g1", outputs_dir=tmp_path,
    ) is None


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
