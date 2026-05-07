"""
Contract test BE-DATA-PLANE-004 — compensating cleanup orphan blob in result_store.

Coperti:
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.utils.result_store import (
    ResultSaveError,
    save_result_blob,
    save_result_blob_from_bytes,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# Stubs

class _StubStorage:
    """Storage stub configurabile per scenario.

    `delete_should_fail`: se True, `delete` solleva RuntimeError per
    simulare cleanup fallito (caso c).
    """

    def __init__(self, *, delete_should_fail: bool = False):
        self.uploads: list[tuple[bytes, str]] = []
        self.deletes: list[str] = []
        self._delete_should_fail = delete_should_fail

    def upload_bytes(self, data, key):
        self.uploads.append((data, key))

    def upload(self, local_path, key):
        self.uploads.append((local_path.read_bytes(), key))

    def download(self, key, local_path): pass
    def get_object_stream(self, key): return {"body": iter([b""]), "size": 0}

    def delete(self, key):
        self.deletes.append(key)
        if self._delete_should_fail:
            raise RuntimeError(f"S3 delete failed for {key}")

    def list_objects(self, prefix): return []


@pytest.fixture
def stub_storage_factory(monkeypatch):
    """Permette di costruire stubs con behavior configurabile (delete fail)."""

    def _make(*, delete_should_fail: bool = False) -> _StubStorage:
        stub = _StubStorage(delete_should_fail=delete_should_fail)
        from app.shared import cloud_backends
        monkeypatch.setattr(
            cloud_backends, "get_runtime_object_storage", lambda: stub
        )
        return stub

    return _make


@pytest.fixture
def mock_db_save_ok(monkeypatch):
    import app.db.db_service as db_service
    import app.utils.result_store as result_store

    saved: list[dict] = []
    monkeypatch.setattr(
        result_store, "uuid", SimpleNamespace(uuid4=lambda: "res-orph")
    )
    monkeypatch.setattr(
        db_service, "get_project", lambda pid: {"id": pid, "group_id": "grp-1"}
    )
    monkeypatch.setattr(db_service, "save_result", lambda **kw: saved.append(kw))
    monkeypatch.setattr(
        result_store, "_apply_results_retention", lambda pid: None
    )
    return saved


@pytest.fixture
def mock_db_save_fails(monkeypatch):
    """Configura `db.save_result` perche' sollevi RuntimeError simulando
    una row DB rifiutata (vincolo FK, connection, ecc.)."""
    import app.db.db_service as db_service
    import app.utils.result_store as result_store

    monkeypatch.setattr(
        result_store, "uuid", SimpleNamespace(uuid4=lambda: "res-orph")
    )
    monkeypatch.setattr(
        db_service, "get_project", lambda pid: {"id": pid, "group_id": "grp-1"}
    )

    def _raise(**_kw):
        raise RuntimeError("DB save FK violation")

    monkeypatch.setattr(db_service, "save_result", _raise)
    monkeypatch.setattr(
        result_store, "_apply_results_retention", lambda pid: None
    )


@pytest.fixture(autouse=True)
def _clean_policy_env(monkeypatch):
    for name in (
        "STORAGE_POLICY",
        "RESULT_S3_THRESHOLD_BYTES",
        "RESULT_DB_HARD_CAP_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def caplog_app(caplog):
    """`app.utils.logger.get_logger("app")` setta `propagate=False` quindi
    caplog di default non vede i log applicativi. Ci attacchiamo
    direttamente al logger del modulo per catturare i record."""
    from app.utils import result_store as _rs
    handler_logger = _rs.logger
    # Risolvi il logger stdlib sotto lo StructuredLogger wrapper.
    stdlib_logger = getattr(handler_logger, "_logger", None) or logging.getLogger(
        "app.utils.result_store"
    )
    stdlib_logger.addHandler(caplog.handler)
    prev_level = stdlib_logger.level
    stdlib_logger.setLevel(logging.DEBUG)
    yield caplog
    stdlib_logger.removeHandler(caplog.handler)
    stdlib_logger.setLevel(prev_level)


# Helpers

def _write_temp_file(tmp_path: Path, name: str, payload: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(payload)
    return p


# (a) upload OK + DB save OK -> nessun delete


def test_object_storage_save_ok_does_not_delete(
    stub_storage_factory, mock_db_save_ok, monkeypatch, tmp_path
):
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    storage = stub_storage_factory()

    src = _write_temp_file(tmp_path, "out.bin", b"payload")
    rid = save_result_blob(project_id="p1", path=src, fmt="bin")

    assert rid == "res-orph"
    assert len(storage.uploads) == 1
    assert storage.deletes == []  # no compensating delete on happy path


def test_object_storage_save_ok_bytes_path_does_not_delete(
    stub_storage_factory, mock_db_save_ok, monkeypatch
):
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    storage = stub_storage_factory()

    save_result_blob_from_bytes(
        project_id="p1", data=b"payload", filename="out.bin", fmt="bin",
    )
    assert len(storage.uploads) == 1
    assert storage.deletes == []


# (b) upload OK + DB save fails -> delete chiamato + ResultSaveError


def test_object_storage_db_save_failure_triggers_compensating_delete(
    stub_storage_factory, mock_db_save_fails, monkeypatch, tmp_path
):
    """Path-based: upload riesce, DB save fallisce -> delete best-effort
    e ResultSaveError verso il caller (chain preservata)."""
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    storage = stub_storage_factory()

    src = _write_temp_file(tmp_path, "out.bin", b"payload")
    with pytest.raises(ResultSaveError, match="Salvataggio risultato nel DB"):
        save_result_blob(project_id="p1", path=src, fmt="bin")

    assert len(storage.uploads) == 1
    uploaded_key = storage.uploads[0][1]
    # cleanup compensativo chiamato esattamente sulla stessa key
    assert storage.deletes == [uploaded_key]
    assert uploaded_key == "results/p1/res-orph.bin"


def test_object_storage_db_save_failure_bytes_path_triggers_delete(
    stub_storage_factory, mock_db_save_fails, monkeypatch
):
    """Bytes-based: stesso pattern."""
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    storage = stub_storage_factory()

    with pytest.raises(ResultSaveError, match="Salvataggio risultato in-memory"):
        save_result_blob_from_bytes(
            project_id="p1", data=b"payload", filename="out.bin", fmt="bin",
        )

    assert len(storage.uploads) == 1
    assert storage.deletes == ["results/p1/res-orph.bin"]


# (c) upload OK + DB save fails + delete fails
#     -> structured log + original DB-save error preserved


def test_db_save_failure_with_cleanup_failure_logs_orphan_event(
    stub_storage_factory, mock_db_save_fails, caplog_app, monkeypatch, tmp_path
):
    """Quando anche il cleanup delete fallisce, deve emettere log
    `event=storage_compensating_delete_failed` e propagare l'errore DB
    originale (non quello del cleanup) come ResultSaveError."""
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    storage = stub_storage_factory(delete_should_fail=True)

    src = _write_temp_file(tmp_path, "out.bin", b"payload")
    with pytest.raises(ResultSaveError, match="Salvataggio risultato nel DB"):
        save_result_blob(project_id="p1", path=src, fmt="bin")

    # Upload + delete tentativi entrambi visibili sullo stub
    assert len(storage.uploads) == 1
    assert storage.deletes == ["results/p1/res-orph.bin"]

    # Structured log strutturato presente
    messages = [r.getMessage() for r in caplog_app.records]
    assert any(
        "event=storage_compensating_delete_failed" in m
        and "storage_key=results/p1/res-orph.bin" in m
        and "domain=result" in m
        for m in messages
    ), f"missing structured log; got: {messages}"


def test_db_save_failure_with_cleanup_failure_bytes_path(
    stub_storage_factory, mock_db_save_fails, caplog_app, monkeypatch
):
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    storage = stub_storage_factory(delete_should_fail=True)

    with pytest.raises(ResultSaveError, match="Salvataggio risultato in-memory"):
        save_result_blob_from_bytes(
            project_id="p1", data=b"payload", filename="out.bin", fmt="bin",
        )

    assert len(storage.uploads) == 1
    assert storage.deletes == ["results/p1/res-orph.bin"]

    messages = [r.getMessage() for r in caplog_app.records]
    assert any(
        "event=storage_compensating_delete_failed" in m for m in messages
    ), f"missing structured log; got: {messages}"


# (c-extra) Successful cleanup also emits structured log


def test_successful_cleanup_emits_ok_event(
    stub_storage_factory, mock_db_save_fails, caplog_app, monkeypatch, tmp_path
):
    """Anche il successo del cleanup deve essere tracciato per
    osservabilita' (orphan recoveries)."""
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    stub_storage_factory()  # delete OK

    src = _write_temp_file(tmp_path, "out.bin", b"payload")
    with pytest.raises(ResultSaveError):
        save_result_blob(project_id="p1", path=src, fmt="bin")

    messages = [r.getMessage() for r in caplog_app.records]
    assert any(
        "event=storage_compensating_delete_ok" in m for m in messages
    ), f"missing ok event; got: {messages}"


# (d) policy=db -> nessun touch a object storage anche su DB save failure


def test_db_policy_db_save_failure_does_not_touch_object_storage(
    stub_storage_factory, mock_db_save_fails, monkeypatch
):
    """policy=db, DB save fallisce: il dispatch non passa MAI per
    object storage. Nessuna upload, nessuna delete."""
    monkeypatch.setenv("STORAGE_POLICY", "db")
    storage = stub_storage_factory()

    with pytest.raises(ResultSaveError):
        save_result_blob_from_bytes(
            project_id="p1", data=b"payload", filename="out.bin", fmt="bin",
        )

    assert storage.uploads == []
    assert storage.deletes == []
