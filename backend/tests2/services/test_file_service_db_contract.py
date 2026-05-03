"""Contract test — FileSourceService usa SOLO DB come storage (FS-B1).

Blinda il contratto post-migration "nessun FS persistente per file_sources":
il blob e i metadata vivono esclusivamente in `plenora.file_sources`
(tabella DB). Il filesystem viene usato solo per file temporanei
durante il processing (fiona/GDAL path-based) e va sempre pulito.

Scope:
  - Round-trip: upload -> DB -> read restituisce gli stessi bytes
  - Zero FS leak: `tempfile.mkdtemp` monitorato per verificare cleanup
  - DI Protocol funzionante: il service gira con mock
  - Dead API gone: `files_dir` non piu' accettato
"""

from __future__ import annotations

import io
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# =============================================================================
# Helpers
# =============================================================================


def _install_fake_db(monkeypatch):
    """Patcha le funzioni di `app.db.db_service` con fake in-memory.

    Preferisce `monkeypatch.setattr` sul modulo reale (gia' importato)
    invece di sostituire il modulo in `sys.modules` — il service fa
    `import app.db.db_service as db`, che richiede che `app.db` abbia
    `db_service` come attributo, quindi sostituire l'intero modulo
    non sempre funziona.

    Ritorna lo `store` dict per inspect lato test.
    """
    import app.db.db_service as real_db

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
            "size_bytes": size_bytes if size_bytes is not None else (len(data) if data else 0),
            "metadata": metadata,
            "created_by": created_by,
        }

    def get_file_source(file_id, include_data=False):
        row = store.get(file_id)
        if not row:
            return None
        out = dict(row)
        if not include_data:
            out.pop("data", None)
        return out

    def delete_file_source(file_id):
        store.pop(file_id, None)

    def list_file_sources(group_id=None):
        return [{"id": k} for k in store if not group_id or store[k]["group_id"] == group_id]

    def update_file_source_metadata(file_id, metadata):
        if file_id in store:
            store[file_id]["metadata"] = metadata

    monkeypatch.setattr(real_db, "save_file_source", save_file_source)
    monkeypatch.setattr(real_db, "get_file_source", get_file_source)
    monkeypatch.setattr(real_db, "delete_file_source", delete_file_source)
    monkeypatch.setattr(real_db, "list_file_sources", list_file_sources)
    monkeypatch.setattr(real_db, "update_file_source_metadata", update_file_source_metadata)
    return store


# =============================================================================
# 1. Round-trip: upload -> DB -> read identical bytes
# =============================================================================


def test_upload_persists_bytes_in_db(monkeypatch):
    """create_raw_from_upload salva il blob in DB. Verifica che la call
    a save_file_source arrivi col blob intatto.
    """
    from app.services.file_service import FileSourceService

    store = _install_fake_db(monkeypatch)

    svc = FileSourceService()
    payload = b"col1,col2\nfoo,bar\n"
    result = svc.create_raw_from_upload(
        file_content=io.BytesIO(payload),
        filename="sample.unknown",
        name="test",
        description="",
        group_id="G1",
    )

    # Il service ha salvato il blob intatto nel fake DB
    assert result.id in store
    stored = store[result.id]
    assert stored["data"] == payload
    assert stored["group_id"] == "G1"
    assert stored["original_filename"] == "sample.unknown"
    # Il modello restituito ha metadata coerenti
    assert result.original_filename == "sample.unknown"
    assert result.file_size == len(payload)


def test_get_file_source_reads_from_db(monkeypatch):
    """get_file_source() legge dal DB via db_service, non dal filesystem."""
    from app.services.file_service import FileSourceService

    store = _install_fake_db(monkeypatch)

    # Prepopola il fake DB con una row (metadata serializzato come mock realistico)
    store["file_abc"] = {
        "id": "file_abc",
        "group_id": "G1",
        "name": "test",
        "original_filename": "t.csv",
        "format": "csv",
        "data": b"hello",
        "metadata": {
            "id": "file_abc",
            "name": "test",
            "description": "",
            "original_filename": "t.csv",
            "format": "csv",
            "group_id": "G1",
            "columns": [],
            "row_count": 0,
            "file_size": 5,
            "crs": None,
            "created_at": "2026-04-18T00:00:00+00:00",
            "updated_at": "2026-04-18T00:00:00+00:00",
        },
    }

    svc = FileSourceService()
    out = svc.get_file_source("file_abc")

    # Il modello e' stato costruito dai metadata DB
    assert out is not None
    assert out.id == "file_abc"
    assert out.original_filename == "t.csv"


def test_update_file_source_fallback_uses_settings_default_group(monkeypatch):
    """BE-FIX-001: se l'update metadata incrementale fallisce, il
    fallback riscrive la row completa e deve importare `settings` nel
    branch locale prima di usare `settings.DEFAULT_GROUP`.

    Il test forza `update_file_source_metadata` a sollevare e rimuove
    `group_id` dalla row letta per il fallback, cosi' il codice deve
    usare il default group configurato invece di sollevare NameError.
    """
    from app.services.file_service import FileSourceService
    from app.config import settings

    store = _install_fake_db(monkeypatch)
    monkeypatch.setattr(settings, "DEFAULT_GROUP", "default_test")

    store["file_update"] = {
        "id": "file_update",
        "group_id": "G1",
        "name": "old",
        "original_filename": "t.csv",
        "format": "csv",
        "data": b"hello",
        "storage_key": None,
        "size_bytes": 5,
        "metadata": {
            "id": "file_update",
            "name": "old",
            "description": "",
            "original_filename": "t.csv",
            "format": "csv",
            "group_id": "G1",
            "columns": [],
            "row_count": 0,
            "file_size": 5,
            "crs": None,
            "created_at": "2026-04-18T00:00:00+00:00",
            "updated_at": "2026-04-18T00:00:00+00:00",
        },
        "created_by": None,
    }

    import app.db.db_service as real_db

    saved_calls: list[dict] = []

    def failing_update_file_source_metadata(file_id, metadata):
        raise RuntimeError("metadata write failed")

    def get_file_source_for_fallback(file_id, include_data=False):
        row = store.get(file_id)
        if not row:
            return None
        out = dict(row)
        if not include_data:
            out.pop("data", None)
            # Simula una legacy/partial row senza group_id per esercitare
            # il fallback a settings.DEFAULT_GROUP.
            out.pop("group_id", None)
        return out

    def spy_save_file_source(**kwargs):
        saved_calls.append(kwargs)
        store[kwargs["file_id"]] = {
            "id": kwargs["file_id"],
            "group_id": kwargs["group_id"],
            "name": kwargs["name"],
            "original_filename": kwargs["original_filename"],
            "format": kwargs["fmt"],
            "data": kwargs.get("data"),
            "storage_key": kwargs.get("storage_key"),
            "size_bytes": kwargs.get("size_bytes"),
            "metadata": kwargs.get("metadata"),
            "created_by": kwargs.get("created_by"),
        }

    monkeypatch.setattr(
        real_db, "update_file_source_metadata",
        failing_update_file_source_metadata,
    )
    monkeypatch.setattr(real_db, "get_file_source", get_file_source_for_fallback)
    monkeypatch.setattr(real_db, "save_file_source", spy_save_file_source)

    svc = FileSourceService()
    monkeypatch.setattr(
        svc, "_get_blob_from_db",
        lambda file_id, expected_group_id=None: b"hello",
    )
    out = svc.update_file_source("file_update", {"name": "new"})

    assert out is not None
    assert out.name == "new"
    assert saved_calls
    assert saved_calls[0]["group_id"] == "default_test"
    assert saved_calls[0]["data"] == b"hello"


# =============================================================================
# 2. Zero FS leak: tempfile.mkdtemp calls are cleaned up
# =============================================================================


def test_no_temp_dir_leak_on_upload(monkeypatch):
    """Monitora `tempfile.mkdtemp` durante un upload ZIP (che triggera
    l'estrazione in tempdir). Tutte le dir create devono essere
    rimosse alla fine del create_from_upload.
    """
    from app.services.file_service import FileSourceService

    _install_fake_db(monkeypatch)

    created_dirs: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def spy_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created_dirs.append(Path(path))
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", spy_mkdtemp)

    # Costruisci un ZIP vuoto-ma-valido per triggerare il code path extract
    import zipfile as _zf
    import io as _io
    buf = _io.BytesIO()
    with _zf.ZipFile(buf, "w") as z:
        z.writestr("dummy.txt", b"hello")
    buf.seek(0)

    # create_from_upload con un ZIP "unknown content": il parser fallira'
    # sul contenuto ma il flusso di cleanup deve comunque eseguire.
    try:
        svc = FileSourceService()
        svc.create_from_upload(
            file_content=buf,
            filename="test.zip",
            name="test",
            description="",
            group_id="G1",
        )
    except Exception:
        # Accettiamo anche eventuali eccezioni downstream (es. ValueError
        # per ZIP senza contenuto riconosciuto): il contratto testato
        # qui e' il cleanup temp.
        pass

    # Nessuna tempdir creata deve sopravvivere al return della funzione
    leaked = [d for d in created_dirs if d.exists()]
    assert not leaked, f"Temp dir leak dopo upload: {leaked}"


# =============================================================================
# 3. DI Protocol: service gira con mocks (filesystem/loader)
# =============================================================================


def test_service_can_be_instantiated_with_mocks():
    """Il contratto DI dichiarato (FileSystemProtocol, DataLoaderProtocol,
    LoggerProtocol, TimeProviderProtocol) e' usabile: costruire il service
    con tutti mock non crasha e espone l'API pubblica.
    """
    from app.services.file_service import (
        FileSourceService,
        FileSystemProtocol,
        DataLoaderProtocol,
        LoggerProtocol,
        TimeProviderProtocol,
    )

    mock_fs = MagicMock(spec=FileSystemProtocol)
    mock_loader = MagicMock(spec=DataLoaderProtocol)
    mock_logger = MagicMock(spec=LoggerProtocol)
    mock_time = MagicMock(spec=TimeProviderProtocol)

    svc = FileSourceService(
        filesystem=mock_fs,
        data_loader=mock_loader,
        logger=mock_logger,
        time_provider=mock_time,
    )

    # Service attributes set correttamente
    assert svc._fs is mock_fs
    assert svc._loader is mock_loader
    assert svc._logger is mock_logger
    assert svc._time is mock_time


def test_service_no_longer_exposes_files_dir_fs_attrs():
    """Post-FS-B1: attributi `_files_dir` / `FILES_DIR` rimossi. Consumer
    legacy che tentano di leggerli devono fallire esplicitamente.
    """
    from app.services.file_service import FileSourceService
    svc = FileSourceService()
    assert not hasattr(svc, "_files_dir")
    assert not hasattr(svc, "FILES_DIR")


def test_service_init_no_longer_accepts_files_dir():
    """Post-FS-B1: parametro `files_dir` rimosso dall'`__init__`."""
    from app.services.file_service import FileSourceService

    with pytest.raises(TypeError):
        FileSourceService(files_dir=Path("/tmp/dead"))


def test_factory_no_longer_accepts_files_dir():
    """La factory `create_file_service` non accetta piu' `files_dir`."""
    from app.services.file_service import create_file_service

    with pytest.raises(TypeError):
        create_file_service(files_dir=Path("/tmp/dead"))


# =============================================================================
# 4. FS-B1-large dispatch: blob sopra threshold va su S3 (non in DB BYTEA)
# =============================================================================


def test_dispatch_below_threshold_keeps_data_in_db(monkeypatch):
    """File < threshold: `data` popolato, `storage_key=None`."""
    from app.services.file_service import FileSourceService

    store = _install_fake_db(monkeypatch)
    # Post-A1: il dispatch threshold-based e' contratto di STORAGE_POLICY=hybrid.
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    # Threshold 10 MB — test payload 1KB ci va sotto.
    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", str(10 * 1024 * 1024))

    svc = FileSourceService()
    payload = b"x" * 1024  # 1KB
    result = svc.create_raw_from_upload(
        file_content=io.BytesIO(payload),
        filename="small.bin",
        name="small",
        group_id="G1",
    )

    stored = store[result.id]
    assert stored["data"] == payload
    # storage_key non settato (o assente) perche' sotto soglia
    assert not stored.get("storage_key")


def test_dispatch_above_threshold_uploads_s3_and_nulls_data(monkeypatch):
    """File >= threshold: upload via cloud_backends, row ha `storage_key`
    valorizzato e `data=None`. Blob NON nel DB.
    """
    from app.services.file_service import FileSourceService

    store = _install_fake_db(monkeypatch)
    # Post-A1: il dispatch threshold-based e' contratto di STORAGE_POLICY=hybrid.
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    # Threshold 100 byte — test payload 1KB supera.
    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", "100")

    uploaded: dict[str, bytes] = {}

    class FakeStorage:
        def upload_bytes(self, data, key):
            uploaded[key] = data

    fake_module = MagicMock()
    fake_module.get_runtime_object_storage.return_value = FakeStorage()
    monkeypatch.setitem(sys.modules, "app.shared.cloud_backends", fake_module)

    svc = FileSourceService()
    payload = b"y" * 1024  # 1KB > 100 threshold
    result = svc.create_raw_from_upload(
        file_content=io.BytesIO(payload),
        filename="big.shp.zip",
        name="big",
        group_id="G1",
    )

    stored = store[result.id]
    # Row DB: data=None, storage_key popolato
    assert stored["data"] is None
    assert stored["storage_key"] == f"file-sources/{result.id}/big.shp.zip"
    assert stored["metadata"]["file_size"] == len(payload)
    # Upload avvenuto sul fake storage col payload intatto
    assert uploaded[stored["storage_key"]] == payload


def test_upload_failure_does_not_create_db_row(monkeypatch):
    """Orphan guard: se S3 upload solleva, `db.save_file_source` NON
    viene chiamato. Safe-by-default: meglio un upload fallito visibile
    che una row DB orfana che punta a S3 vuoto.
    """
    from app.services.file_service import FileSourceService

    store = _install_fake_db(monkeypatch)
    # Post-A1: il dispatch threshold-based e' contratto di STORAGE_POLICY=hybrid.
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", "100")

    class FakeStorage:
        def upload_bytes(self, data, key):
            raise RuntimeError("S3 simulata down")

    fake_module = MagicMock()
    fake_module.get_runtime_object_storage.return_value = FakeStorage()
    monkeypatch.setitem(sys.modules, "app.shared.cloud_backends", fake_module)

    svc = FileSourceService()
    payload = b"y" * 1024

    # create deve propagare l'exception — il caller decide la UX (es. 500 API)
    with pytest.raises(RuntimeError, match="S3 simulata down"):
        svc.create_raw_from_upload(
            file_content=io.BytesIO(payload),
            filename="big.bin",
            name="big",
            group_id="G1",
        )

    # DB rimasto vuoto: zero row orfane
    assert store == {}


# =============================================================================
# 5. Reader S3: download + cleanup temp anche su eccezione
# =============================================================================


def test_read_blob_from_s3_downloads_and_cleans_temp(monkeypatch, tmp_path):
    """`_get_blob_from_db` con `storage_key` set: estrae blob via
    storage.download in temp file, ritorna bytes, cleanup temp.
    """
    from app.services.file_service import FileSourceService

    _install_fake_db(monkeypatch)
    import app.db.db_service as real_db

    s3_payload = b"s3-blob-content-42"

    class FakeStorage:
        def download(self, key, path):
            Path(path).write_bytes(s3_payload)

    fake_module = MagicMock()
    fake_module.get_runtime_object_storage.return_value = FakeStorage()
    monkeypatch.setitem(sys.modules, "app.shared.cloud_backends", fake_module)

    # Prepopola il fake DB con una row S3-backed
    def _fake_get(file_id, include_data=False):
        return {
            "id": "file_s3",
            "group_id": "G1",
            "name": "large",
            "original_filename": "large.bin",
            "format": "bin",
            "data": None,
            "storage_key": "file-sources/file_s3/large.bin",
            "metadata": {
                "id": "file_s3",
                "name": "large",
                "original_filename": "large.bin",
                "format": "bin",
                "group_id": "G1",
                "columns": [],
                "row_count": 0,
                "file_size": len(s3_payload),
                "description": "",
                "crs": None,
                "created_at": "2026-04-18T00:00:00+00:00",
                "updated_at": "2026-04-18T00:00:00+00:00",
            },
        }

    monkeypatch.setattr(real_db, "get_file_source", _fake_get)

    # Spy su tempfile per verificare cleanup
    created_temps: list[Path] = []
    real_named = tempfile.NamedTemporaryFile

    def spy_named(*args, **kwargs):
        tmp = real_named(*args, **kwargs)
        created_temps.append(Path(tmp.name))
        return tmp

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", spy_named)

    svc = FileSourceService()
    # _assert_visible e' chiamato con il group_id del mock
    out = svc._get_blob_from_db("file_s3", expected_group_id="G1")

    assert out == s3_payload
    # Tutti i temp file creati devono essere eliminati
    leaked = [p for p in created_temps if p.exists()]
    assert not leaked, f"Temp file leak dopo S3 read: {leaked}"


def test_read_blob_from_s3_cleanup_temp_on_download_exception(monkeypatch):
    """Se `storage.download` solleva (es. timeout, 403), il temp file
    creato viene comunque eliminato dal `finally`. `_get_blob_from_db`
    ritorna None (catched dal try/except esterno del metodo).
    """
    from app.services.file_service import FileSourceService

    _install_fake_db(monkeypatch)
    import app.db.db_service as real_db

    class FakeStorage:
        def download(self, key, path):
            # Simuliamo: il temp file e' stato creato vuoto dal caller,
            # noi solleviamo durante il download effettivo.
            raise RuntimeError("S3 403 AccessDenied simulato")

    fake_module = MagicMock()
    fake_module.get_runtime_object_storage.return_value = FakeStorage()
    monkeypatch.setitem(sys.modules, "app.shared.cloud_backends", fake_module)

    def _fake_get(file_id, include_data=False):
        return {
            "id": "file_s3",
            "group_id": "G1",
            "name": "x",
            "original_filename": "x.bin",
            "format": "bin",
            "data": None,
            "storage_key": "file-sources/file_s3/x.bin",
            "metadata": {
                "id": "file_s3", "name": "x", "original_filename": "x.bin",
                "format": "bin", "group_id": "G1", "columns": [], "row_count": 0,
                "file_size": 100, "description": "", "crs": None,
                "created_at": "2026-04-18T00:00:00+00:00",
                "updated_at": "2026-04-18T00:00:00+00:00",
            },
        }

    monkeypatch.setattr(real_db, "get_file_source", _fake_get)

    created_temps: list[Path] = []
    real_named = tempfile.NamedTemporaryFile

    def spy_named(*args, **kwargs):
        tmp = real_named(*args, **kwargs)
        created_temps.append(Path(tmp.name))
        return tmp

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", spy_named)

    svc = FileSourceService()
    # Il metodo cattura l'eccezione internamente e ritorna None (pattern
    # [GESTITO] del service, consistente con le altre failure path).
    out = svc._get_blob_from_db("file_s3", expected_group_id="G1")
    assert out is None

    # Il temp file deve essere stato eliminato anche se download ha sollevato
    leaked = [p for p in created_temps if p.exists()]
    assert not leaked, f"Temp file leak dopo exception: {leaked}"
