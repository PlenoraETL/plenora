"""
Contract test â€” defense-in-depth MIME nei service entry-point.

Difesa secondaria: i 3 metodi di FileSourceService che ricevono blob
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# Helpers payload


def _png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x08\x00\x00\x00\x01\x01\x00\x00\x00\x00"
        b"\x37\x6e\xf9\x24"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _exe_bytes() -> bytes:
    return b"MZ\x90\x00" + b"\x00" * 200


def _elf_bytes() -> bytes:
    return b"\x7fELF\x02\x01\x01" + b"\x00" * 200


def _shebang() -> bytes:
    return b"#!/bin/bash\nrm -rf /\n"


def _make_service():
    """FileSourceService con dependencies leggere (NullLogger, etc.)."""
    from app.core.files.service import FileSourceService
    return FileSourceService()


# create_raw_from_upload


@pytest.mark.parametrize("payload,name", [
    (_exe_bytes(), "trojan"),
    (_elf_bytes(), "linux_payload"),
    (_shebang(), "script"),
])
def test_create_raw_from_upload_blocks_executable(payload, name, monkeypatch):
    """Anche se il router e' bypassato, il service rifiuta payload exec."""
    svc = _make_service()
    # Non vogliamo che la chiamata raggiunga mai il DB
    import app.core.files.db as db
    save_mock = MagicMock()
    monkeypatch.setattr(db, "save_file_source", save_mock)

    with pytest.raises(ValueError, match="eseguibile|firma|MZ|ELF"):
        svc.create_raw_from_upload(
            file_content=io.BytesIO(payload),
            filename=f"{name}.png",
            name=name,
            description="",
            group_id="g1",
        )

    save_mock.assert_not_called()


def test_create_raw_from_upload_accepts_legitimate_binary(monkeypatch):
    """PNG legittimo passa il MIME check e raggiunge il DB."""
    svc = _make_service()
    import app.core.files.db as db
    save_mock = MagicMock()
    monkeypatch.setattr(db, "save_file_source", save_mock)
    # get_file_source viene chiamato dopo save per il return
    monkeypatch.setattr(
        db, "get_file_source",
        lambda fid, include_data=False: {
            "id": fid, "name": "x", "original_filename": "x.png",
            "format": "png", "size_bytes": 100,
        },
    )

    svc.create_raw_from_upload(
        file_content=io.BytesIO(_png_bytes()),
        filename="legit.png",
        name="legit",
        description="",
        group_id="g1",
    )

    save_mock.assert_called_once()


# create_multi_layer_from_upload


def test_create_multi_layer_from_upload_blocks_executable(monkeypatch):
    svc = _make_service()
    import app.core.files.db as db
    save_mock = MagicMock()
    monkeypatch.setattr(db, "save_file_source", save_mock)

    with pytest.raises(ValueError, match="eseguibile|firma|MZ"):
        svc.create_multi_layer_from_upload(
            file_content=io.BytesIO(_exe_bytes()),
            filename="trojan.gpkg",
            name="trojan",
            description="",
            layers_config=[{"layer": "x"}],
            group_id="g1",
        )
    save_mock.assert_not_called()


def test_create_multi_layer_requires_layers_config_first(monkeypatch):
    """Verifica che il MIME check non venga prima di altri preconditi
    fondamentali â€” es. layers_config vuoto deve restare il primo errore
    visibile (caller deve sapere che la firma e' sbagliata, non il body)."""
    svc = _make_service()
    with pytest.raises(ValueError, match="layers_config|MIME|eseguibile"):
        svc.create_multi_layer_from_upload(
            file_content=io.BytesIO(_png_bytes()),
            filename="x.gpkg",
            name="x",
            layers_config=[],  # vuoto
            group_id="g1",
        )


# preview_file_temp


def test_preview_file_temp_blocks_executable():
    svc = _make_service()
    with pytest.raises(ValueError, match="eseguibile|firma|MZ"):
        svc.preview_file_temp(
            file_content=io.BytesIO(_exe_bytes()),
            filename="trojan.csv",
        )


def test_preview_file_temp_blocks_elf():
    svc = _make_service()
    with pytest.raises(ValueError, match="eseguibile|firma|ELF"):
        svc.preview_file_temp(
            file_content=io.BytesIO(_elf_bytes()),
            filename="binary.json",
        )
