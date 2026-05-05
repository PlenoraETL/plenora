"""Smoke contract - STRUCT-001 Seam D-D: blob/temp materialization.

Verifica le invarianti del modulo
`app.services.file_blob_materialization`:

  get_blob_from_db:
    1. row con `data` BYTEA -> ritorna i bytes direttamente.
    2. row con `storage_key` -> chiama get_object_storage().download().
    3. row None (file non trovato) -> ritorna None.
    4. assert_visible solleva PermissionError -> propaga (fail-closed N1).
    5. db_get_file_source solleva -> logger.error + ritorna None (best-effort).

  temp_file_for:
    6. CSV: scrive blob in temp file con suffisso originale.
    7. SHP zip ben formato: estrae + ritorna path .shp.
    8. SHP zip malformato (no .shp dentro): cleanup tmp_dir + ritorna None.
    9. blob None -> ritorna None.
    10. Wrapper FileSourceService._get_blob_from_db / _temp_file_for
        delegano correttamente coi callback del service.

Mock dei collaborator: nessun DB/S3 reale, niente fiona/zip_safe reale
(per i test unzip uso zipfile reale + tmp_path pytest).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture
def fake_logger():
    log = MagicMock()
    log.info = MagicMock()
    log.warning = MagicMock()
    log.debug = MagicMock()
    log.error = MagicMock()
    return log


@pytest.fixture
def assert_visible_ok():
    """assert_visible che passa sempre (utente autorizzato)."""
    return MagicMock(return_value=None)


@pytest.fixture
def get_storage_mock():
    """Object storage mock con `.download(key, path)` (per
    `temp_file_for` legacy path) e `.get_object_stream(key)` (per
    `get_blob_from_db` post-RESOURCE-ENGINE-006)."""
    storage = MagicMock()
    storage.download = MagicMock()
    storage.get_object_stream = MagicMock(return_value={
        "body": iter([b""]),
        "size": 0,
        "content_type": "application/octet-stream",
    })
    return MagicMock(return_value=storage)


# =============================================================================
# get_blob_from_db
# =============================================================================


def test_get_blob_returns_bytea_from_row_data(
    fake_logger, assert_visible_ok, get_storage_mock, tmp_path,
):
    from app.services.file_blob_materialization import get_blob_from_db

    db_get = MagicMock(return_value={"data": b"bytea-content", "storage_key": None})
    out = get_blob_from_db(
        "file_a", "grp1",
        assert_visible=assert_visible_ok,
        db_get_file_source=db_get,
        get_object_storage=get_storage_mock,
        temp_dir=str(tmp_path),
        logger=fake_logger,
    )
    assert out == b"bytea-content"
    assert_visible_ok.assert_called_once_with("file_a", "grp1")
    db_get.assert_called_once_with("file_a", include_data=True)
    # storage NON chiamato (path BYTEA)
    get_storage_mock.assert_not_called()


def test_get_blob_downloads_from_storage_key_when_no_data(
    fake_logger, assert_visible_ok, tmp_path,
):
    """BE-GO-READY-RESOURCE-ENGINE-006: storage_key branch usa
    `get_object_stream(key)` + concatenazione bytes invece del
    tempfile roundtrip pre-batch (`download(key, tmp_path)` +
    `read_bytes()` + `unlink`).
    """
    from app.services.file_blob_materialization import get_blob_from_db

    db_get = MagicMock(return_value={"data": None, "storage_key": "file-sources/x/y.csv"})

    storage = MagicMock()
    storage.get_object_stream = MagicMock(return_value={
        "body": iter([b"down", b"loaded-", b"bytes"]),
        "size": 16,
        "content_type": "text/csv",
    })
    # download MUST NOT be called any more on this path
    storage.download = MagicMock()
    get_storage = MagicMock(return_value=storage)

    out = get_blob_from_db(
        "file_b", "grp1",
        assert_visible=assert_visible_ok,
        db_get_file_source=db_get,
        get_object_storage=get_storage,
        temp_dir=str(tmp_path),
        logger=fake_logger,
    )
    assert out == b"downloaded-bytes"
    storage.get_object_stream.assert_called_once_with("file-sources/x/y.csv")
    storage.download.assert_not_called()


def test_get_blob_storage_key_branch_does_not_create_tempfile(
    fake_logger, assert_visible_ok, tmp_path,
):
    """BE-GO-READY-RESOURCE-ENGINE-006 (R-005.1): regression guard.
    Il branch storage_key NON deve creare alcun file dentro
    `temp_dir`. Pre-batch scriveva un NamedTemporaryFile e
    leggeva-poi-unlink-ava; post-batch e' bytes-first puro.
    """
    from app.services.file_blob_materialization import get_blob_from_db

    db_get = MagicMock(return_value={"data": None, "storage_key": "file-sources/a/b.bin"})
    storage = MagicMock()
    storage.get_object_stream = MagicMock(return_value={
        "body": iter([b"payload-bytes"]),
        "size": 13,
        "content_type": "application/octet-stream",
    })
    storage.download = MagicMock()
    get_storage = MagicMock(return_value=storage)

    # Snapshot of `temp_dir` content pre-call.
    pre_files = sorted(p.name for p in Path(tmp_path).iterdir())

    out = get_blob_from_db(
        "file_z", "grp1",
        assert_visible=assert_visible_ok,
        db_get_file_source=db_get,
        get_object_storage=get_storage,
        temp_dir=str(tmp_path),
        logger=fake_logger,
    )
    assert out == b"payload-bytes"

    # No new files were created in temp_dir.
    post_files = sorted(p.name for p in Path(tmp_path).iterdir())
    assert post_files == pre_files, (
        f"Regressione R-005.1: il branch storage_key ha creato "
        f"file in temp_dir ({set(post_files) - set(pre_files)}). "
        f"Il path bytes-first NON deve materializzare tempfile."
    )


def test_get_blob_returns_none_and_logs_when_get_object_stream_fails(
    fake_logger, assert_visible_ok, tmp_path,
):
    """Object storage failure (`ObjectStorageDisabledError` o errore
    network/auth durante `get_object_stream`) -> log error + return
    None. Best-effort identico al pre-batch contract.
    """
    from app.services.file_blob_materialization import get_blob_from_db

    db_get = MagicMock(return_value={"data": None, "storage_key": "file-sources/x/y.csv"})
    storage = MagicMock()
    storage.get_object_stream = MagicMock(
        side_effect=RuntimeError("S3 down")
    )
    get_storage = MagicMock(return_value=storage)

    out = get_blob_from_db(
        "file_q", "grp1",
        assert_visible=assert_visible_ok,
        db_get_file_source=db_get,
        get_object_storage=get_storage,
        temp_dir=str(tmp_path),
        logger=fake_logger,
    )
    assert out is None
    fake_logger.error.assert_called_once()


def test_get_blob_returns_none_when_row_missing(
    fake_logger, assert_visible_ok, get_storage_mock, tmp_path,
):
    from app.services.file_blob_materialization import get_blob_from_db

    db_get = MagicMock(return_value=None)
    out = get_blob_from_db(
        "ghost", "grp1",
        assert_visible=assert_visible_ok,
        db_get_file_source=db_get,
        get_object_storage=get_storage_mock,
        temp_dir=str(tmp_path),
        logger=fake_logger,
    )
    assert out is None


def test_get_blob_propagates_assert_visible_permission_error(
    fake_logger, get_storage_mock, tmp_path,
):
    """Fail-closed N1: assert_visible solleva -> il caller riceve la
    PermissionError, NON viene catturata silenziosamente."""
    from app.services.file_blob_materialization import get_blob_from_db

    fail_visible = MagicMock(side_effect=PermissionError("cross-tenant"))
    db_get = MagicMock()  # non deve essere chiamato

    with pytest.raises(PermissionError, match="cross-tenant"):
        get_blob_from_db(
            "file_x", "wrong_grp",
            assert_visible=fail_visible,
            db_get_file_source=db_get,
            get_object_storage=get_storage_mock,
            temp_dir=str(tmp_path),
            logger=fake_logger,
        )
    db_get.assert_not_called()


def test_get_blob_returns_none_and_logs_on_db_exception(
    fake_logger, assert_visible_ok, get_storage_mock, tmp_path,
):
    """Best-effort: db_get_file_source solleva -> logger.error + ritorno None."""
    from app.services.file_blob_materialization import get_blob_from_db

    db_get = MagicMock(side_effect=RuntimeError("db down"))
    out = get_blob_from_db(
        "file_x", "grp1",
        assert_visible=assert_visible_ok,
        db_get_file_source=db_get,
        get_object_storage=get_storage_mock,
        temp_dir=str(tmp_path),
        logger=fake_logger,
    )
    assert out is None
    fake_logger.error.assert_called_once()


# =============================================================================
# temp_file_for
# =============================================================================


def test_temp_file_for_csv_writes_blob_with_original_suffix(
    fake_logger, assert_visible_ok, get_storage_mock, tmp_path,
):
    from app.services.file_blob_materialization import temp_file_for

    db_get = MagicMock(return_value={
        "data": b"id,name\n1,a\n",
        "storage_key": None,
        "format": "csv",
        "original_filename": "users.csv",
    })

    out = temp_file_for(
        "file_csv", "grp1",
        assert_visible=assert_visible_ok,
        db_get_file_source=db_get,
        get_object_storage=get_storage_mock,
        temp_dir=str(tmp_path),
        safe_extractall=MagicMock(),
        cleanup=MagicMock(),
        logger=fake_logger,
    )
    assert out is not None
    assert out.suffix == ".csv"
    assert out.read_bytes() == b"id,name\n1,a\n"
    # cleanup di sicurezza
    out.unlink(missing_ok=True)


def test_temp_file_for_shp_zip_well_formed_returns_shp_path(
    fake_logger, assert_visible_ok, get_storage_mock, tmp_path,
):
    """SHP zip valido: safe_extractall popola tmp_dir con un .shp,
    la funzione lo trova via rglob e lo ritorna."""
    from app.services.file_blob_materialization import temp_file_for

    # Costruisci un blob ZIP reale in memoria (con un finto .shp dentro)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("layer.shp", b"fake shp content")
        z.writestr("layer.shx", b"fake shx")
        z.writestr("layer.dbf", b"fake dbf")
    blob_bytes = buf.getvalue()

    db_get = MagicMock(return_value={
        "data": blob_bytes,
        "storage_key": None,
        "format": "shp",
        "original_filename": "layer.zip",
    })

    # safe_extractall reale: estrae il contenuto in tmp_dir
    def real_extract(z, dest):
        z.extractall(dest)

    cleanup_mock = MagicMock()
    out = temp_file_for(
        "file_shp", "grp1",
        assert_visible=assert_visible_ok,
        db_get_file_source=db_get,
        get_object_storage=get_storage_mock,
        temp_dir=str(tmp_path),
        safe_extractall=real_extract,
        cleanup=cleanup_mock,
        logger=fake_logger,
    )
    assert out is not None
    assert out.name.endswith(".shp")
    assert out.exists()
    # Cleanup NON chiamato (success path lascia tmp_dir al caller)
    cleanup_mock.assert_not_called()


def test_temp_file_for_shp_zip_malformed_cleans_up_and_returns_none(
    fake_logger, assert_visible_ok, get_storage_mock, tmp_path,
):
    """SHP zip valido ma SENZA .shp dentro: cleanup tmp_dir +
    ritorno None (no orphan dir nei worker)."""
    from app.services.file_blob_materialization import temp_file_for

    # ZIP valido ma senza .shp: solo file random
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", b"no shp here")
    blob_bytes = buf.getvalue()

    db_get = MagicMock(return_value={
        "data": blob_bytes,
        "storage_key": None,
        "format": "shp",
        "original_filename": "broken.zip",
    })

    def real_extract(z, dest):
        z.extractall(dest)

    cleanup_mock = MagicMock()
    out = temp_file_for(
        "file_bad", "grp1",
        assert_visible=assert_visible_ok,
        db_get_file_source=db_get,
        get_object_storage=get_storage_mock,
        temp_dir=str(tmp_path),
        safe_extractall=real_extract,
        cleanup=cleanup_mock,
        logger=fake_logger,
    )
    assert out is None
    # Cleanup tmp_dir CHIAMATO (path errore: zip valido ma senza .shp)
    cleanup_mock.assert_called_once()


def test_temp_file_for_returns_none_when_blob_missing(
    fake_logger, assert_visible_ok, get_storage_mock, tmp_path,
):
    from app.services.file_blob_materialization import temp_file_for

    # Row meta ok ma blob None (es. row mancante in DB)
    db_get = MagicMock(side_effect=[
        {"format": "csv", "original_filename": "x.csv"},  # metadata
        None,  # data fetch
    ])

    out = temp_file_for(
        "file_z", "grp1",
        assert_visible=assert_visible_ok,
        db_get_file_source=db_get,
        get_object_storage=get_storage_mock,
        temp_dir=str(tmp_path),
        safe_extractall=MagicMock(),
        cleanup=MagicMock(),
        logger=fake_logger,
    )
    assert out is None


# =============================================================================
# Wrapper FileSourceService._get_blob_from_db / _temp_file_for delegano
# =============================================================================


def test_file_service_get_blob_wrapper_delegates(tmp_path):
    """`FileSourceService._get_blob_from_db` thin wrapper:
    passa assert_visible/db_get/storage/temp_dir/logger del service."""
    from app.services.file_service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._assert_visible = MagicMock()
    svc._logger = MagicMock()

    with patch(
        "app.services.file_blob_materialization.get_blob_from_db",
        return_value=b"fake",
    ) as mock_shared:
        out = svc._get_blob_from_db("file_a", "grp1")

    assert out == b"fake"
    mock_shared.assert_called_once()
    kwargs = mock_shared.call_args.kwargs
    assert kwargs["assert_visible"] is svc._assert_visible
    assert kwargs["logger"] is svc._logger


def test_file_service_temp_file_for_wrapper_delegates(tmp_path):
    """`FileSourceService._temp_file_for` thin wrapper: passa tutti i 7
    collaborator (assert_visible/db_get/storage/temp_dir/safe_extractall/
    cleanup/logger)."""
    from app.services.file_service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._assert_visible = MagicMock()
    svc._logger = MagicMock()

    fake_path = tmp_path / "fake.csv"
    fake_path.write_text("x")
    with patch(
        "app.services.file_blob_materialization.temp_file_for",
        return_value=fake_path,
    ) as mock_shared:
        out = svc._temp_file_for("file_a", "grp1")

    assert out is fake_path
    mock_shared.assert_called_once()
    kwargs = mock_shared.call_args.kwargs
    assert kwargs["assert_visible"] is svc._assert_visible
    assert kwargs["logger"] is svc._logger
    assert callable(kwargs["safe_extractall"])
    assert callable(kwargs["cleanup"])
