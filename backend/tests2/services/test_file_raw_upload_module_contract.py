"""
Smoke contract - STRUCT-001 Seam D-E: estrazione create_raw_from_upload.

Verifica le invarianti minime del refactor:
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Fixtures


@pytest.fixture
def fake_logger():
    log = MagicMock()
    log.info = MagicMock()
    log.warning = MagicMock()
    log.error = MagicMock()
    return log


@pytest.fixture
def collaborators(fake_logger):
    """Set di collaborator default per il modulo. I test specifici
    sostituiscono il singolo collaborator necessario."""
    fake_dto = MagicMock()
    fake_dto.model_dump = MagicMock(return_value={"k": "v"})
    return {
        "generate_file_id": MagicMock(return_value="file_abc123"),
        "time_now": MagicMock(return_value=datetime(2026, 5, 2, tzinfo=timezone.utc)),
        "dispatch_blob_storage": MagicMock(
            return_value={"data": b"raw", "storage_key": None, "size_bytes": 3}
        ),
        "db_save_file_source": MagicMock(),
        "default_group": "default_grp",
        "build_file_source": MagicMock(return_value=fake_dto),
        "logger": fake_logger,
        "mime_validate": MagicMock(return_value=None),  # accept all
    }


# 1. Happy path DB BYTEA


def test_happy_path_db_storage_calls_dispatch_and_db_save(collaborators):
    from app.services.file_raw_upload import create_raw_from_upload

    raw = b"hello"
    out = create_raw_from_upload(
        io.BytesIO(raw), "doc.dxf", "MyDoc",
        description="desc", group_id="grp1",
        **collaborators,
    )

    # generate_file_id chiamato col name
    collaborators["generate_file_id"].assert_called_once_with("MyDoc")
    # mime_validate chiamato coi bytes letti + filename
    collaborators["mime_validate"].assert_called_once_with(content=raw, filename="doc.dxf")
    # dispatch chiamato con (file_id, raw, filename)
    collaborators["dispatch_blob_storage"].assert_called_once_with(
        "file_abc123", raw, "doc.dxf",
    )
    # db_save chiamato con kwargs correct (incluso **blob_kwargs propagato)
    collaborators["db_save_file_source"].assert_called_once()
    call_kwargs = collaborators["db_save_file_source"].call_args.kwargs
    assert call_kwargs["file_id"] == "file_abc123"
    assert call_kwargs["group_id"] == "grp1"
    assert call_kwargs["name"] == "MyDoc"
    assert call_kwargs["original_filename"] == "doc.dxf"
    assert call_kwargs["fmt"] == "dxf"
    assert call_kwargs["metadata"] == {"k": "v"}
    assert call_kwargs["data"] == b"raw"  # da blob_kwargs
    assert call_kwargs["storage_key"] is None
    assert call_kwargs["size_bytes"] == 3
    # build_file_source chiamato con tutti i campi DTO
    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["id"] == "file_abc123"
    assert build_kwargs["columns"] == []
    assert build_kwargs["row_count"] == 0
    assert build_kwargs["file_size"] == len(raw)
    assert build_kwargs["group_id"] == "grp1"
    assert build_kwargs["layer"] is None
    assert build_kwargs["sheet"] is None
    assert build_kwargs["crs"] is None
    # ritorno: il DTO costruito
    assert out is collaborators["build_file_source"].return_value
    # log info finale
    collaborators["logger"].info.assert_called_once()


# 2. Happy path object_storage


def test_happy_path_object_storage_returns_storage_key(collaborators):
    from app.services.file_raw_upload import create_raw_from_upload

    collaborators["dispatch_blob_storage"].return_value = {
        "data": None,
        "storage_key": "file-sources/file_abc123/big.dxf",
        "size_bytes": 100,
    }

    create_raw_from_upload(
        io.BytesIO(b"x" * 100), "big.dxf", "Big",
        group_id="grp1",
        **collaborators,
    )

    db_kwargs = collaborators["db_save_file_source"].call_args.kwargs
    assert db_kwargs["data"] is None
    assert db_kwargs["storage_key"] == "file-sources/file_abc123/big.dxf"
    assert db_kwargs["size_bytes"] == 100


# 3. MIME block: ValueError, no side effect


def test_mime_block_raises_value_error_and_logs_warning(collaborators):
    """MIME validator solleva MimeMismatchError -> ValueError propagato +
    logger.warning + dispatch/db_save NON chiamati."""
    from app.services.file_raw_upload import create_raw_from_upload
    from app.utils.mime_validator import MimeMismatchError

    collaborators["mime_validate"].side_effect = MimeMismatchError(
        "executable detected"
    )

    with pytest.raises(ValueError, match="executable detected"):
        create_raw_from_upload(
            io.BytesIO(b"MZ\x90\x00 fake exe"),
            "innocent.dxf", "Trojan",
            group_id="grp1",
            **collaborators,
        )

    # MIME bloccato PRIMA di dispatch / db_save
    collaborators["dispatch_blob_storage"].assert_not_called()
    collaborators["db_save_file_source"].assert_not_called()
    collaborators["build_file_source"].assert_not_called()
    # NOTE: loggato col contesto (name, filename)
    collaborators["logger"].warning.assert_called_once()
    msg = collaborators["logger"].warning.call_args[0][0]
    assert "create_raw_from_upload bloccato (MIME)" in msg
    assert "Trojan" in msg
    assert "innocent.dxf" in msg


# 4. Orphan guard: dispatch exception -> no db_save


def test_dispatch_exception_propagates_and_db_save_not_called(collaborators):
    """Orphan guard: se dispatch_blob_storage solleva (es. S3 down),
    db_save_file_source NON viene chiamato (no row DB orfana)."""
    from app.services.file_raw_upload import create_raw_from_upload

    collaborators["dispatch_blob_storage"].side_effect = RuntimeError("S3 down")

    with pytest.raises(RuntimeError, match="S3 down"):
        create_raw_from_upload(
            io.BytesIO(b"data"), "x.dxf", "Doc",
            group_id="grp1",
            **collaborators,
        )

    # build_file_source viene chiamato PRIMA di dispatch (per il DTO)
    collaborators["build_file_source"].assert_called_once()
    # ma db_save_file_source NO (orphan guard)
    collaborators["db_save_file_source"].assert_not_called()


# 5. DB save exception propaga


def test_db_save_exception_propagates(collaborators):
    from app.services.file_raw_upload import create_raw_from_upload

    collaborators["db_save_file_source"].side_effect = RuntimeError("DB write fail")

    with pytest.raises(RuntimeError, match="DB write fail"):
        create_raw_from_upload(
            io.BytesIO(b"data"), "x.dxf", "Doc",
            group_id="grp1",
            **collaborators,
        )

    # dispatch chiamato PRIMA del fail
    collaborators["dispatch_blob_storage"].assert_called_once()


# 6. group_id None -> default_group propagato coerente


def test_default_group_used_when_group_id_is_none(collaborators):
    """group_id=None -> default_group usato sia nel DTO sia nel DB save."""
    from app.services.file_raw_upload import create_raw_from_upload

    create_raw_from_upload(
        io.BytesIO(b"data"), "x.dxf", "Doc",
        group_id=None,  # esplicito
        **collaborators,
    )

    # DTO: build_file_source riceve group_id=default_group
    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["group_id"] == "default_grp"
    # DB save: stesso valore (NO drift)
    db_kwargs = collaborators["db_save_file_source"].call_args.kwargs
    assert db_kwargs["group_id"] == "default_grp"


# 7. file_ext lowercased + fallback "bin"


@pytest.mark.parametrize(
    "filename,expected_fmt",
    [
        ("doc.DXF", "dxf"),
        ("doc.PDF", "pdf"),
        ("noext", "bin"),
        ("path/with/slashes/file.TxT", "txt"),
    ],
)
def test_file_extension_lowercased_or_bin_fallback(
    collaborators, filename, expected_fmt,
):
    from app.services.file_raw_upload import create_raw_from_upload

    create_raw_from_upload(
        io.BytesIO(b"d"), filename, "X", group_id="g1",
        **collaborators,
    )

    db_kwargs = collaborators["db_save_file_source"].call_args.kwargs
    assert db_kwargs["fmt"] == expected_fmt
    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["format"] == expected_fmt


# 8. Wrapper FileSourceService.create_raw_from_upload delega


def test_file_service_wrapper_delegates_to_module():
    """`FileSourceService.create_raw_from_upload` thin wrapper:
    invoca file_raw_upload.create_raw_from_upload con tutti i
    collaborator del service (8 kwargs)."""
    from app.services.file_service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._generate_file_id = MagicMock(return_value="file_x")
    svc._time = MagicMock()
    svc._time.now = MagicMock(return_value=datetime.now(timezone.utc))
    svc._dispatch_blob_storage = MagicMock(
        return_value={"data": b"x", "storage_key": None, "size_bytes": 1}
    )
    svc._logger = MagicMock()

    fake_dto = MagicMock()
    with patch(
        "app.services.file_raw_upload.create_raw_from_upload",
        return_value=fake_dto,
    ) as mock_impl:
        out = svc.create_raw_from_upload(
            io.BytesIO(b"x"), "doc.dxf", "Doc",
            description="d", group_id="g1",
        )

    assert out is fake_dto
    mock_impl.assert_called_once()
    args = mock_impl.call_args.args
    kwargs = mock_impl.call_args.kwargs
    # Posizionali: file_content, filename, name, description, group_id
    assert args[1] == "doc.dxf"
    assert args[2] == "Doc"
    assert args[3] == "d"
    assert args[4] == "g1"
    # Kwargs: 7 collaborator del service (mime_validate omesso = default lazy)
    assert kwargs["generate_file_id"] is svc._generate_file_id
    assert kwargs["time_now"] is svc._time.now
    assert kwargs["dispatch_blob_storage"] is svc._dispatch_blob_storage
    assert callable(kwargs["db_save_file_source"])
    assert isinstance(kwargs["default_group"], str)
    assert callable(kwargs["build_file_source"])
    assert kwargs["logger"] is svc._logger
