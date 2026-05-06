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


# ---------------------------------------------------------------------------
# BE-GO-READY-RESOURCE-ENGINE-011-FILE-BLOB-MATERIALIZATION-BOUNDED
# ---------------------------------------------------------------------------
#
# Locks the bounded-read invariant on the storage_key branch.
# Pre-batch the implementation used `b"".join(resp["body"])` which
# was unbounded (multi-GB objects would fully accumulate in worker
# memory; only kernel SIGKILL stopped them). Post-batch
# `read_stream_bounded` aborts when running total > cap and the
# function applies its standard fail-soft contract (log + None).


def test_get_blob_storage_key_normal_stream_returns_bytes_under_cap(
    fake_logger, assert_visible_ok, tmp_path, monkeypatch,
):
    """Normal-size payload (well below cap) returns bytes.
    Multi-chunk iterable is concatenated in order; cap reads from
    `RESOURCE_MAX_BYTES` via `app.config.settings`."""
    from app.services.file_blob_materialization import get_blob_from_db
    import app.services.file_blob_materialization as mod

    # Pin a small cap (1 KiB) for the test; multi-chunk payload
    # well under cap.
    monkeypatch.setattr(mod, "_read_blob_cap_bytes", lambda: 1024)

    db_get = MagicMock(return_value={"data": None, "storage_key": "file-sources/p/q.bin"})
    storage = MagicMock()
    storage.get_object_stream = MagicMock(return_value={
        "body": iter([b"part-1-", b"part-2-", b"part-3"]),
        "size": 20,
        "content_type": "application/octet-stream",
    })
    get_storage = MagicMock(return_value=storage)

    out = get_blob_from_db(
        "file_norm", "grp1",
        assert_visible=assert_visible_ok,
        db_get_file_source=db_get,
        get_object_storage=get_storage,
        temp_dir=str(tmp_path),
        logger=fake_logger,
    )

    assert out == b"part-1-part-2-part-3"
    # No tempfile must be created in this branch.
    assert sorted(p.name for p in Path(tmp_path).iterdir()) == []
    # No error logged on the happy path.
    fake_logger.error.assert_not_called()


def test_get_blob_storage_key_oversize_stream_returns_none_with_explicit_log(
    fake_logger, assert_visible_ok, tmp_path, monkeypatch,
):
    """Oversize payload: bounded read aborts when running total
    crosses cap. Per the existing fail-soft contract the function
    returns None and logs an error. The error message MUST mention
    'oversize' so ops can grep for OOM-prevention vs generic fetch
    failures."""
    from app.services.file_blob_materialization import get_blob_from_db
    import app.services.file_blob_materialization as mod

    # Tiny cap: 8 bytes. Stream produces 30 bytes -> aborts on 2nd chunk.
    monkeypatch.setattr(mod, "_read_blob_cap_bytes", lambda: 8)

    db_get = MagicMock(return_value={"data": None, "storage_key": "file-sources/big/x.bin"})

    # Sentinel iterator: counts how many chunks were consumed before abort.
    consumed = []

    def _gen():
        for n in range(10):
            chunk = bytes([0x41 + n]) * 5  # 5 bytes each
            consumed.append(n)
            yield chunk

    storage = MagicMock()
    storage.get_object_stream = MagicMock(return_value={
        "body": _gen(),
        "size": 50,
        "content_type": "application/octet-stream",
    })
    get_storage = MagicMock(return_value=storage)

    out = get_blob_from_db(
        "file_big", "grp1",
        assert_visible=assert_visible_ok,
        db_get_file_source=db_get,
        get_object_storage=get_storage,
        temp_dir=str(tmp_path),
        logger=fake_logger,
    )

    # Fail-soft contract: bytes are NOT returned on overrun.
    assert out is None
    # Logger.error called with the dedicated oversize message.
    fake_logger.error.assert_called_once()
    log_msg = fake_logger.error.call_args[0][0]
    assert "oversize" in log_msg.lower(), (
        f"Expected oversize signal in log message; got {log_msg!r}"
    )
    # The bounded read aborted EARLY: consumed at most 2 chunks
    # (5 bytes ok, 10 bytes >8 -> abort) — not the full 10-chunk
    # stream. This proves the cap is enforced chunk-by-chunk and
    # not retroactively after a full join.
    assert len(consumed) <= 2, (
        f"Bounded read did not abort early; consumed={len(consumed)} chunks "
        "(expected at most 2 before crossing the 8-byte cap)."
    )


def test_get_blob_storage_key_oversize_does_not_create_tempfile(
    fake_logger, assert_visible_ok, tmp_path, monkeypatch,
):
    """Defense-in-depth: even on the oversize-abort path, the
    function MUST NOT fall back to a download-to-path strategy.
    The bytes-first invariant from RESOURCE-ENGINE-006 holds in
    all branches of `get_blob_from_db`."""
    from app.services.file_blob_materialization import get_blob_from_db
    import app.services.file_blob_materialization as mod

    monkeypatch.setattr(mod, "_read_blob_cap_bytes", lambda: 4)

    db_get = MagicMock(return_value={"data": None, "storage_key": "file-sources/big/over.bin"})
    storage = MagicMock()
    storage.get_object_stream = MagicMock(return_value={
        "body": iter([b"AAAA", b"BBBB", b"CCCC"]),  # 12 bytes total > 4
        "size": 12,
        "content_type": "application/octet-stream",
    })
    storage.download = MagicMock()
    get_storage = MagicMock(return_value=storage)

    pre_files = sorted(p.name for p in Path(tmp_path).iterdir())
    out = get_blob_from_db(
        "file_big2", "grp1",
        assert_visible=assert_visible_ok,
        db_get_file_source=db_get,
        get_object_storage=get_storage,
        temp_dir=str(tmp_path),
        logger=fake_logger,
    )
    post_files = sorted(p.name for p in Path(tmp_path).iterdir())

    assert out is None
    assert post_files == pre_files, (
        "Regressione: il branch oversize ha materializzato un tempfile. "
        f"new files: {set(post_files) - set(pre_files)}. La policy "
        "bytes-first deve restare valida anche sul fail path."
    )
    storage.download.assert_not_called()


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
