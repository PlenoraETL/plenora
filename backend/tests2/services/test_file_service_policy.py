"""FileSourceService always stores payloads in object storage."""

import io

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class _Storage:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.uploads = []

    def upload_bytes(self, data, key):
        if self.fail:
            raise RuntimeError("storage down")
        self.uploads.append((data, key))

    def upload_fileobj(self, stream, key, *, size_bytes):
        if self.fail:
            raise RuntimeError("storage down")
        self.uploads.append((stream.read(), key))


def _install(monkeypatch, storage):
    from app.core.files import db
    from app.core.io import object_storage
    from app.core.resources import blob_dispatch

    saved = []
    monkeypatch.setattr(db, "save_file_source", lambda **kwargs: saved.append(kwargs))
    monkeypatch.setattr(object_storage, "get_runtime_object_storage", lambda: storage)
    monkeypatch.setattr(
        blob_dispatch.reconciliation,
        "record_upload_intent",
        lambda **kwargs: "intent-1",
    )
    return saved


def test_db_env_cannot_enable_bytea_write(monkeypatch):
    from app.core.files.service import FileSourceService

    monkeypatch.setenv("STORAGE_POLICY", "db")
    storage = _Storage()
    saved = _install(monkeypatch, storage)
    result = FileSourceService().create_raw_from_upload(
        file_content=io.BytesIO(b"tiny"),
        filename="tiny.bin",
        name="tiny",
        group_id="G1",
    )

    assert len(storage.uploads) == 1
    assert len(saved) == 1
    assert "data" not in saved[0]
    assert saved[0]["storage_key"] == (
        f"tenants/G1/file-sources/{result.id}/tiny.bin"
    )


def test_upload_failure_does_not_write_catalog_row(monkeypatch):
    from app.core.files.service import FileSourceService

    storage = _Storage(fail=True)
    saved = _install(monkeypatch, storage)
    with pytest.raises(RuntimeError, match="storage down"):
        FileSourceService().create_raw_from_upload(
            file_content=io.BytesIO(b"payload"),
            filename="x.bin",
            name="x",
            group_id="G1",
        )
    assert saved == []
