"""Contract test FS-4 — dispatch S3 per result > threshold.

Prima di FS-4, `save_result_blob_from_bytes` sollevava ResultSaveError
oltre MAX_RESULT_BLOB_SIZE (100MB). Ora:

  - size < RESULT_S3_THRESHOLD_BYTES (default 50MB) -> DB blob (come prima)
  - size >= threshold -> S3 upload + row con storage_key (data NULL)
  - size > MAX_RESULT_S3_SIZE (default 5GB) -> ResultSaveError (hard cap)

Reader `get_result_data` gestisce trasparente: se storage_key presente
scarica da S3, altrimenti ritorna BYTEA. Delete pulisce S3 best-effort.

Sentinel:
1. Small file (< threshold) -> data=bytes, storage_key=None
2. Large file (>= threshold) -> storage.upload_bytes chiamato, data=None
3. Reader usa S3 download quando storage_key set
4. Hard cap (> 5GB) -> ResultSaveError
5. Upload S3 fallisce -> ResultSaveError (no row DB orfana)
6. delete_result su row S3-backed triggers storage.delete
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


class _StubStorage:
    """Stub ObjectStorage per test senza AWS."""
    def __init__(self):
        self.uploads = []   # [(data, key)]
        self.deletes = []   # [key]
        self.upload_fail = False
        self._objects = {}  # key -> bytes

    def upload_bytes(self, data, key):
        if self.upload_fail:
            raise RuntimeError("S3 upload failed (stub)")
        self.uploads.append((data, key))
        self._objects[key] = data

    def get_object_stream(self, key):
        if key not in self._objects:
            raise RuntimeError(f"NoSuchKey: {key}")
        return {"body": iter([self._objects[key]]), "size": len(self._objects[key])}

    def delete(self, key):
        self.deletes.append(key)
        self._objects.pop(key, None)

    def upload(self, local_path, key): pass
    def download(self, key, local_path): pass
    def list_objects(self, prefix): return []


@pytest.fixture
def stub_storage(monkeypatch):
    stub = _StubStorage()
    from app.shared import cloud_backends
    monkeypatch.setattr(cloud_backends, "get_runtime_object_storage", lambda: stub)
    return stub


@pytest.fixture
def mock_db(monkeypatch):
    """DB mock che registra save_result calls."""
    import app.db.db_service as db_service
    import app.utils.result_store as result_store

    saved = []

    monkeypatch.setattr(result_store, "uuid",
                        SimpleNamespace(uuid4=lambda: "res-fs4"))
    monkeypatch.setattr(db_service, "get_project",
                        lambda pid: {"id": pid, "group_id": "grp-1"})
    monkeypatch.setattr(db_service, "save_result",
                        lambda **kw: saved.append(kw))
    monkeypatch.setattr(result_store, "_apply_results_retention",
                        lambda pid: None)

    return saved


# =============================================================================
# 1. Dispatch sul threshold
# =============================================================================


def test_small_file_goes_to_db_blob(mock_db, stub_storage, monkeypatch):
    """Sotto threshold -> DB blob, S3 non toccato."""
    from app.utils.result_store import save_result_blob_from_bytes
    import app.config as app_config

    # Threshold piccolo ma dato ancora piu' piccolo -> DB
    monkeypatch.setattr(app_config.settings, "get",
                        lambda k, d=None: (10 * 1024 * 1024 if "THRESHOLD" in k else d))

    payload = b"small payload"
    rid = save_result_blob_from_bytes(
        project_id="p1", data=payload, filename="f.bin", fmt="bin",
    )

    assert rid == "res-fs4"
    assert len(mock_db) == 1
    saved = mock_db[0]
    assert saved["data"] == payload
    assert saved.get("storage_key") is None
    # S3 non toccato
    assert stub_storage.uploads == []


def test_large_file_goes_to_s3(mock_db, stub_storage, monkeypatch):
    """Sopra threshold -> S3 upload + DB row con storage_key (data None)."""
    from app.utils.result_store import save_result_blob_from_bytes
    import app.config as app_config

    # Threshold basso per isolare il test
    def _fake_get(key, default=None):
        if "THRESHOLD" in key:
            return 100  # 100 byte
        return default
    monkeypatch.setattr(app_config.settings, "get", _fake_get)

    payload = b"x" * 500  # oltre 100 byte
    rid = save_result_blob_from_bytes(
        project_id="p1", data=payload, filename="big.bin", fmt="bin",
    )

    assert rid == "res-fs4"
    assert len(mock_db) == 1
    saved = mock_db[0]
    # data NULL in DB — il blob vive su S3
    assert saved["data"] is None
    assert saved["storage_key"] == "results/p1/res-fs4.bin"
    assert saved.get("size_bytes") == 500
    # S3 upload eseguito
    assert len(stub_storage.uploads) == 1
    uploaded_data, uploaded_key = stub_storage.uploads[0]
    assert uploaded_data == payload
    assert uploaded_key == "results/p1/res-fs4.bin"


def test_no_hard_cap_any_size_goes_to_s3(mock_db, stub_storage, monkeypatch):
    """Post FS-4+multipart: nessun hard cap. upload_bytes auto-chunking
    delega al provider (S3 multipart, Azure block, GCS resumable).
    Il limite reale e' RAM del processo, non artificialmente imposto."""
    from app.utils.result_store import save_result_blob_from_bytes
    import app.config as app_config

    def _fake_get(key, default=None):
        if "THRESHOLD" in key:
            return 100
        return default
    monkeypatch.setattr(app_config.settings, "get", _fake_get)

    # Payload "grande" (nel senso dei test unit: sopra threshold).
    # Lo stub storage accetta qualsiasi size; in prod boto3 multipart
    # gestirebbe il chunking.
    payload = b"x" * 10_000  # 10KB — sopra threshold 100
    rid = save_result_blob_from_bytes(
        project_id="p1", data=payload, filename="big.bin", fmt="bin",
    )
    assert rid == "res-fs4"
    # Upload su S3, niente ResultSaveError
    assert len(stub_storage.uploads) == 1
    uploaded_data, _ = stub_storage.uploads[0]
    assert uploaded_data == payload
    # DB row con storage_key
    assert len(mock_db) == 1
    assert mock_db[0]["data"] is None
    assert mock_db[0]["storage_key"] == "results/p1/res-fs4.bin"


# =============================================================================
# 2. Fault handling
# =============================================================================


def test_s3_upload_failure_no_db_row_orphan(mock_db, stub_storage, monkeypatch):
    """Se S3 upload solleva, non crea row DB orfana."""
    from app.utils.result_store import save_result_blob_from_bytes, ResultSaveError
    import app.config as app_config

    def _fake_get(key, default=None):
        if "THRESHOLD" in key:
            return 100
        return default
    monkeypatch.setattr(app_config.settings, "get", _fake_get)

    stub_storage.upload_fail = True
    payload = b"x" * 500

    with pytest.raises(ResultSaveError, match=r"S3"):
        save_result_blob_from_bytes(
            project_id="p1", data=payload, filename="big.bin", fmt="bin",
        )
    # No row DB
    assert mock_db == []


# =============================================================================
# 3. Reader: trasparente su storage_key
# =============================================================================


def test_get_result_data_from_db_blob(monkeypatch):
    """storage_key=None -> ritorna data BYTEA."""
    import app.db.results_db_service as rds

    monkeypatch.setattr(rds, "fetchone",
                        lambda sql, args: {"data": b"blob content", "storage_key": None})

    result = rds.get_result_data("res-1")
    assert result == b"blob content"


def test_get_result_data_from_s3(monkeypatch, stub_storage):
    """storage_key set -> scarica da S3 via stub."""
    import app.db.results_db_service as rds

    # DB row con storage_key ma data None
    monkeypatch.setattr(
        rds, "fetchone",
        lambda sql, args: {
            "data": None,
            "storage_key": "results/p1/res-big.bin",
        },
    )
    # Pre-popola lo stub S3 con il contenuto
    stub_storage._objects["results/p1/res-big.bin"] = b"big data from s3"

    result = rds.get_result_data("res-big")
    assert result == b"big data from s3"


def test_get_result_data_s3_download_failure_returns_none(monkeypatch):
    """Se S3 download fallisce, get_result_data ritorna None + log
    (non solleva: semantica invariata col path-based su errore)."""
    import app.db.results_db_service as rds

    monkeypatch.setattr(
        rds, "fetchone",
        lambda sql, args: {"data": None, "storage_key": "results/p1/missing.bin"},
    )
    # _s3_download_bytes deve restituire None su errore
    monkeypatch.setattr(rds, "_s3_download_bytes", lambda key: None)

    assert rds.get_result_data("res-missing") is None


# =============================================================================
# 4. Delete: cleanup S3 coerente
# =============================================================================


def test_delete_result_s3_backed_triggers_s3_delete(monkeypatch, stub_storage):
    """delete_result su row S3-backed -> storage.delete eager."""
    import app.db.results_db_service as rds

    # row con storage_key
    monkeypatch.setattr(
        rds, "fetchone",
        lambda sql, args: {"storage_key": "results/p1/res-to-del.bin"},
    )
    monkeypatch.setattr(rds, "execute",
                        lambda sql, args: MagicMock(rowcount=1))

    # Il monkeypatch qui deve essere su rds._s3_delete_best_effort
    calls = []
    def _fake_s3_delete(key):
        calls.append(key)
    monkeypatch.setattr(rds, "_s3_delete_best_effort", _fake_s3_delete)

    result = rds.delete_result("res-to-del")
    assert result is True
    assert calls == ["results/p1/res-to-del.bin"]


def test_delete_result_db_backed_does_not_touch_s3(monkeypatch):
    """delete_result su row DB blob (no storage_key) -> no S3 call."""
    import app.db.results_db_service as rds

    monkeypatch.setattr(rds, "fetchone",
                        lambda sql, args: {"storage_key": None})
    monkeypatch.setattr(rds, "execute",
                        lambda sql, args: MagicMock(rowcount=1))

    s3_calls = []
    monkeypatch.setattr(rds, "_s3_delete_best_effort",
                        lambda key: s3_calls.append(key))

    rds.delete_result("res-small")
    assert s3_calls == []


# =============================================================================
# 5. save_result_blob (path-based) — stesso dispatch post FS-4b
# =============================================================================


def test_save_result_blob_path_small_goes_to_db_blob(mock_db, stub_storage, monkeypatch, tmp_path):
    """Path-based: file < threshold -> DB BYTEA, NO storage.upload."""
    from app.utils.result_store import save_result_blob
    import app.config as app_config

    def _fake_get(key, default=None):
        if "THRESHOLD" in key:
            return 10 * 1024 * 1024   # 10MB
        return default
    monkeypatch.setattr(app_config.settings, "get", _fake_get)

    # File piccolo (100 byte)
    f = tmp_path / "small.csv"
    content = b"col\n" + b"a\n" * 50
    f.write_bytes(content)
    assert f.stat().st_size < 10 * 1024 * 1024

    rid = save_result_blob("p1", f, "csv", rows_count=50)
    assert rid == "res-fs4"
    # DB blob
    assert len(mock_db) == 1
    assert mock_db[0]["data"] == content
    assert mock_db[0].get("storage_key") is None
    # S3 non toccato
    assert stub_storage.uploads == []
    # File temp unlinked post-save
    assert not f.exists()


def test_save_result_blob_path_large_goes_to_s3(mock_db, stub_storage, monkeypatch, tmp_path):
    """Path-based: file >= threshold -> S3 via storage.upload(path) (streaming)."""
    from app.utils.result_store import save_result_blob
    import app.config as app_config

    def _fake_get(key, default=None):
        if "THRESHOLD" in key:
            return 100   # 100 byte threshold
        return default
    monkeypatch.setattr(app_config.settings, "get", _fake_get)

    # Aggiungo upload(path, key) al fake storage (stub.upload_bytes gia' c'era)
    upload_calls = []
    def _fake_upload(local_path, key):
        upload_calls.append((str(local_path), key))
    stub_storage.upload = _fake_upload

    f = tmp_path / "big.csv"
    f.write_bytes(b"x" * 500)  # 500 byte > 100 threshold

    rid = save_result_blob("p1", f, "csv", rows_count=0)
    assert rid == "res-fs4"
    # S3 upload via path (non via bytes)
    assert len(upload_calls) == 1
    path_used, key_used = upload_calls[0]
    assert key_used == "results/p1/res-fs4.bin"
    # DB row con storage_key, data None
    assert len(mock_db) == 1
    assert mock_db[0]["data"] is None
    assert mock_db[0]["storage_key"] == "results/p1/res-fs4.bin"
    assert mock_db[0]["size_bytes"] == 500


def test_save_result_blob_path_s3_upload_failure_no_db_row(mock_db, stub_storage, monkeypatch, tmp_path):
    """Path-based: se S3 upload solleva, no row DB orfana; file temp resta."""
    from app.utils.result_store import save_result_blob, ResultSaveError
    import app.config as app_config

    def _fake_get(key, default=None):
        if "THRESHOLD" in key:
            return 100
        return default
    monkeypatch.setattr(app_config.settings, "get", _fake_get)

    def _failing_upload(local_path, key):
        raise RuntimeError("S3 upload fail")
    stub_storage.upload = _failing_upload

    f = tmp_path / "big.csv"
    f.write_bytes(b"x" * 500)

    with pytest.raises(ResultSaveError, match=r"S3"):
        save_result_blob("p1", f, "csv")

    # No row DB
    assert mock_db == []
    # File temp resta (retry-friendly, non cancellato)
    assert f.exists()


# =============================================================================
# 6. Delete operations (posizionati alla fine — terminate la suite)
# =============================================================================


def test_delete_results_for_project_cleans_all_s3_keys(monkeypatch):
    """delete_results_for_project pulisce tutti gli S3 keys del progetto."""
    import app.db.results_db_service as rds

    # fetchall ritorna 2 row con storage_key
    monkeypatch.setattr(
        rds, "fetchall",
        lambda sql, args: [
            {"storage_key": "results/p1/a.bin"},
            {"storage_key": "results/p1/b.bin"},
        ],
    )
    monkeypatch.setattr(rds, "execute",
                        lambda sql, args: MagicMock(rowcount=2))

    calls = []
    monkeypatch.setattr(rds, "_s3_delete_best_effort",
                        lambda key: calls.append(key))

    deleted = rds.delete_results_for_project("p1")
    assert deleted == 2
    assert set(calls) == {"results/p1/a.bin", "results/p1/b.bin"}
