"""
Contract test A1 â€” dispatch policy-aware in FileSourceService.

Copre Â§6.3 del design A1: `_dispatch_blob_storage` deve ramificare sulla
"""

from __future__ import annotations

import io

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class _StubStorage:
    def __init__(self):
        self.uploads: list[tuple[bytes, str]] = []

    def upload_bytes(self, data, key):
        self.uploads.append((data, key))


class _FailingStorage:
    def __init__(self, message="object storage simulated down"):
        self.message = message
        self.calls = 0

    def upload_bytes(self, data, key):
        self.calls += 1
        raise RuntimeError(self.message)


@pytest.fixture
def stub_storage(monkeypatch):
    stub = _StubStorage()
    from app.shared import cloud_backends
    monkeypatch.setattr(cloud_backends, "get_runtime_object_storage", lambda: stub)
    return stub


@pytest.fixture
def mock_db(monkeypatch):
    """Fake in-memory per `app.core.files.db.save_file_source`.

    Il service chiama anche altri file_sources DAL helper (es. list/get): qui ci
    interessa solo catturare gli argomenti del save, il resto lo
    stubbiamo minimale per non rompere il path.
    """
    import app.core.files.db as real_db

    store: dict[str, dict] = {}

    def save_file_source(*, file_id, group_id, name, original_filename,
                          fmt, data=None, metadata=None,
                          created_by=None, storage_key=None, size_bytes=None):
        store[file_id] = {
            "id": file_id,
            "group_id": group_id,
            "name": name,
            "original_filename": original_filename,
            "format": fmt,
            "data": data,
            "storage_key": storage_key,
            "size_bytes": size_bytes,
            "metadata": metadata,
            "created_by": created_by,
        }

    monkeypatch.setattr(real_db, "save_file_source", save_file_source)
    return store


@pytest.fixture(autouse=True)
def _clean_policy_env(monkeypatch):
    """Rimuove le env coinvolte: conftest.py setta STORAGE_POLICY=db di
    default per la session, qui ogni test dichiara esplicitamente il
    proprio regime."""
    for name in (
        "STORAGE_POLICY",
        "FILE_SOURCE_S3_THRESHOLD_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


# policy = db


def test_db_policy_small_payload_stays_in_bytea(mock_db, stub_storage, monkeypatch):
    """policy=db + payload minuscolo -> DB BYTEA, nessun upload."""
    from app.core.files.service import FileSourceService

    monkeypatch.setenv("STORAGE_POLICY", "db")

    svc = FileSourceService()
    payload = b"col1,col2\nfoo,bar\n"
    result = svc.create_raw_from_upload(
        file_content=io.BytesIO(payload),
        filename="tiny.csv",
        name="tiny",
        group_id="G1",
    )

    stored = mock_db[result.id]
    assert stored["data"] == payload
    assert stored["storage_key"] is None
    assert stub_storage.uploads == []


def test_db_policy_ignores_threshold(mock_db, stub_storage, monkeypatch):
    """policy=db + threshold basso: ignora la soglia, va comunque in DB.
    Dimostra che la policy vince sul threshold (il contrario di hybrid)."""
    from app.core.files.service import FileSourceService

    monkeypatch.setenv("STORAGE_POLICY", "db")
    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", "100")

    svc = FileSourceService()
    payload = b"x" * 2048  # > threshold 100
    result = svc.create_raw_from_upload(
        file_content=io.BytesIO(payload),
        filename="big.bin",
        name="big",
        group_id="G1",
    )

    stored = mock_db[result.id]
    assert stored["data"] == payload
    assert stored["storage_key"] is None
    assert stub_storage.uploads == []


# policy = object_storage


def test_object_storage_policy_small_payload_uploads(mock_db, stub_storage, monkeypatch):
    """policy=object_storage + payload piccolo -> upload comunque,
    niente BYTEA. Dimostra che la policy vince sulla soglia."""
    from app.core.files.service import FileSourceService

    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", str(10 * 1024 * 1024))

    svc = FileSourceService()
    payload = b"tiny"
    result = svc.create_raw_from_upload(
        file_content=io.BytesIO(payload),
        filename="tiny.bin",
        name="tiny",
        group_id="G1",
    )

    stored = mock_db[result.id]
    assert stored["data"] is None
    assert stored["storage_key"] == f"file-sources/{result.id}/tiny.bin"
    assert len(stub_storage.uploads) == 1
    uploaded_data, uploaded_key = stub_storage.uploads[0]
    assert uploaded_data == payload
    assert uploaded_key == stored["storage_key"]


def test_object_storage_policy_upload_failure_no_db_row(mock_db, monkeypatch):
    """policy=object_storage + upload exc -> nessuna row DB (orphan guard).
    Stesso contratto del ramo hybrid: fail loud, niente DB row orfana."""
    from app.core.files.service import FileSourceService
    from app.shared import cloud_backends

    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    failing = _FailingStorage()
    monkeypatch.setattr(cloud_backends, "get_runtime_object_storage", lambda: failing)

    svc = FileSourceService()
    payload = b"anything"
    with pytest.raises(RuntimeError, match="object storage simulated down"):
        svc.create_raw_from_upload(
            file_content=io.BytesIO(payload),
            filename="x.bin",
            name="x",
            group_id="G1",
        )

    assert failing.calls == 1
    assert mock_db == {}


# policy = hybrid (comportamento attuale: threshold-based)


def test_hybrid_below_threshold_stays_in_db(mock_db, stub_storage, monkeypatch):
    """hybrid + sotto soglia -> DB BYTEA (comportamento attuale default).
    Duplicato testuale con test_file_service_db_contract ma vive qui per
    tenere il contratto policy-aware leggibile in un singolo file."""
    from app.core.files.service import FileSourceService

    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", str(10 * 1024))

    svc = FileSourceService()
    payload = b"x" * 512  # < 10 KB
    result = svc.create_raw_from_upload(
        file_content=io.BytesIO(payload),
        filename="small.bin",
        name="s",
        group_id="G1",
    )

    stored = mock_db[result.id]
    assert stored["data"] == payload
    assert stored["storage_key"] is None
    assert stub_storage.uploads == []


def test_hybrid_above_threshold_uploads(mock_db, stub_storage, monkeypatch):
    """hybrid + sopra soglia -> upload object storage (comportamento
    attuale default)."""
    from app.core.files.service import FileSourceService

    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", "100")

    svc = FileSourceService()
    payload = b"y" * 1024  # > 100 threshold
    result = svc.create_raw_from_upload(
        file_content=io.BytesIO(payload),
        filename="big.bin",
        name="b",
        group_id="G1",
    )

    stored = mock_db[result.id]
    assert stored["data"] is None
    assert stored["storage_key"] == f"file-sources/{result.id}/big.bin"
    assert len(stub_storage.uploads) == 1
    assert stub_storage.uploads[0][0] == payload
