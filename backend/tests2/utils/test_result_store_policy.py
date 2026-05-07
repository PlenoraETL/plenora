"""
Contract test A1 — dispatch policy-aware in result_store.

Copre §6.3 del design A1: save_result_blob / save_result_blob_from_bytes
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.utils.result_store import (
    ResultSaveError,
    save_result_blob_from_bytes,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


class _StubStorage:
    def __init__(self):
        self.uploads: list[tuple[bytes, str]] = []

    def upload_bytes(self, data, key):
        self.uploads.append((data, key))

    def upload(self, local_path, key):
        self.uploads.append((local_path.read_bytes(), key))

    def download(self, key, local_path): pass
    def get_object_stream(self, key): return {"body": iter([b""]), "size": 0}
    def delete(self, key): pass
    def list_objects(self, prefix): return []


@pytest.fixture
def stub_storage(monkeypatch):
    stub = _StubStorage()
    from app.shared import cloud_backends
    monkeypatch.setattr(cloud_backends, "get_runtime_object_storage", lambda: stub)
    return stub


@pytest.fixture
def mock_db(monkeypatch):
    import app.db.db_service as db_service
    import app.utils.result_store as result_store

    saved: list[dict] = []

    monkeypatch.setattr(result_store, "uuid",
                        SimpleNamespace(uuid4=lambda: "res-pol"))
    monkeypatch.setattr(db_service, "get_project",
                        lambda pid: {"id": pid, "group_id": "grp-1"})
    monkeypatch.setattr(db_service, "save_result",
                        lambda **kw: saved.append(kw))
    monkeypatch.setattr(result_store, "_apply_results_retention",
                        lambda pid: None)
    return saved


@pytest.fixture(autouse=True)
def _clean_policy_env(monkeypatch):
    for name in (
        "STORAGE_POLICY",
        "RESULT_S3_THRESHOLD_BYTES",
        "RESULT_DB_HARD_CAP_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


# policy = db


def test_db_policy_writes_bytea_and_never_calls_storage(mock_db, stub_storage, monkeypatch):
    monkeypatch.setenv("STORAGE_POLICY", "db")

    payload = b"x" * 10_000  # 10 KB, ampiamente sotto qualunque soglia
    rid = save_result_blob_from_bytes(
        project_id="p1", data=payload, filename="f.bin", fmt="bin",
    )

    assert rid == "res-pol"
    assert len(mock_db) == 1
    saved = mock_db[0]
    assert saved["data"] == payload
    assert saved.get("storage_key") is None
    # Nessuna chiamata al backend, anche se il payload sarebbe andato
    # su storage in policy=hybrid col default 50MB.
    assert stub_storage.uploads == []


def test_db_policy_enforces_hard_cap(mock_db, stub_storage, monkeypatch):
    """policy=db + payload > RESULT_DB_HARD_CAP_BYTES -> ResultSaveError,
    nessuna row DB. Hard cap evita BYTEA fuori controllo."""
    monkeypatch.setenv("STORAGE_POLICY", "db")
    monkeypatch.setenv("RESULT_DB_HARD_CAP_BYTES", "1024")  # 1 KB

    payload = b"x" * 2048  # 2 KB > 1 KB cap
    with pytest.raises(ResultSaveError, match="RESULT_DB_HARD_CAP_BYTES"):
        save_result_blob_from_bytes(
            project_id="p1", data=payload, filename="f.bin", fmt="bin",
        )
    assert mock_db == []
    assert stub_storage.uploads == []


# policy = object_storage


def test_object_storage_policy_always_uploads(mock_db, stub_storage, monkeypatch):
    """policy=object_storage + payload piccolissimo -> comunque su storage,
    mai in DB BYTEA."""
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")

    payload = b"tiny"
    rid = save_result_blob_from_bytes(
        project_id="p1", data=payload, filename="f.bin", fmt="bin",
    )

    assert rid == "res-pol"
    assert len(stub_storage.uploads) == 1
    uploaded_data, uploaded_key = stub_storage.uploads[0]
    assert uploaded_data == payload
    assert uploaded_key == "results/p1/res-pol.bin"

    assert len(mock_db) == 1
    saved = mock_db[0]
    assert saved["data"] is None
    assert saved["storage_key"] == "results/p1/res-pol.bin"


def test_object_storage_policy_ignores_threshold(mock_db, stub_storage, monkeypatch):
    """Threshold settato ma policy=object_storage -> ignorato, upload comunque.
    Dimostra che la policy vince sulla soglia."""
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    monkeypatch.setenv("RESULT_S3_THRESHOLD_BYTES", str(10 * 1024 * 1024))

    payload = b"x" * 100  # 100 byte, sotto threshold 10 MB
    save_result_blob_from_bytes(
        project_id="p1", data=payload, filename="f.bin", fmt="bin",
    )
    assert len(stub_storage.uploads) == 1
    assert mock_db[0]["data"] is None


# policy = hybrid (comportamento attuale + guard hard cap)


def test_hybrid_below_threshold_goes_to_db(mock_db, stub_storage, monkeypatch):
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("RESULT_S3_THRESHOLD_BYTES", str(10 * 1024))

    payload = b"x" * 512
    save_result_blob_from_bytes(
        project_id="p1", data=payload, filename="f.bin", fmt="bin",
    )
    assert mock_db[0]["data"] == payload
    assert stub_storage.uploads == []


def test_hybrid_above_threshold_goes_to_storage(mock_db, stub_storage, monkeypatch):
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("RESULT_S3_THRESHOLD_BYTES", "100")

    payload = b"x" * 500
    save_result_blob_from_bytes(
        project_id="p1", data=payload, filename="f.bin", fmt="bin",
    )
    assert mock_db[0]["data"] is None
    assert mock_db[0]["storage_key"] == "results/p1/res-pol.bin"
    assert len(stub_storage.uploads) == 1


def test_hybrid_hard_cap_guards_db_branch(mock_db, stub_storage, monkeypatch):
    """Config misconfigurata: threshold > hard_cap. In hybrid il dispatch sotto
    soglia va in DB, ma il hard cap interviene prima di inserire oltre cap.
    """
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("RESULT_S3_THRESHOLD_BYTES", str(1024 * 1024))  # 1 MB
    monkeypatch.setenv("RESULT_DB_HARD_CAP_BYTES", "500")

    payload = b"x" * 1024  # 1 KB: < threshold 1 MB ma > cap 500
    with pytest.raises(ResultSaveError, match="RESULT_DB_HARD_CAP_BYTES"):
        save_result_blob_from_bytes(
            project_id="p1", data=payload, filename="f.bin", fmt="bin",
        )
    assert mock_db == []
    assert stub_storage.uploads == []
