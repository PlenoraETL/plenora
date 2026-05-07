"""Contract test — `app.utils.mime_validator.validate_upload`.

Verifica:
  - Happy path per ogni formato in allowlist
  - Adversariali: .exe rinominato in .csv/.png/.xlsx
  - Polyglot: pdf rinominato in .png, png rinominato in .csv
  - Estensione assente / non in allowlist → MimeMismatchError
  - File vuoto → MimeMismatchError
  - File testuali validi (utf-8 / latin-1 / con BOM) accettati
  - File "testuale" con null bytes (binary mascherato) rifiutato
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.utils.mime_validator import (
    MimeMismatchError,
    detect_mime,
    validate_no_executable,
    validate_upload,
)


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# Helpers


def _png_bytes() -> bytes:
    """PNG header valido + pixel minimo (8x1 1-bit)."""
    # Magic + IHDR chunk + minimal IDAT/IEND. Per il test basta il magic.
    return (
        b"\x89PNG\r\n\x1a\n"  # signature
        b"\x00\x00\x00\rIHDR"  # IHDR chunk header
        b"\x00\x00\x00\x08\x00\x00\x00\x01\x01\x00\x00\x00\x00"
        b"\x37\x6e\xf9\x24"  # CRC
        b"\x00\x00\x00\x00IEND\xaeB`\x82"  # IEND
    )


def _jpeg_bytes() -> bytes:
    """JPEG header SOI + APP0 + EOI."""
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01"
        b"\x00\x01\x00\x00\xff\xd9"
    )


def _gif_bytes() -> bytes:
    return b"GIF89a\x01\x00\x01\x00\x00\xff\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"


def _zip_bytes() -> bytes:
    """ZIP minimo con un file dentro."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", "hello")
    return buf.getvalue()


def _xlsx_bytes() -> bytes:
    """xlsx ridotto: la firma e' identica a uno zip (xlsx == zip ooxml)."""
    return _zip_bytes()


def _gpkg_bytes() -> bytes:
    """SQLite3 magic."""
    return b"SQLite format 3\x00" + b"\x00" * 100


def _exe_bytes() -> bytes:
    """Windows PE/EXE (MZ header)."""
    return b"MZ\x90\x00" + b"\x00" * 100


def _elf_bytes() -> bytes:
    """Linux ELF."""
    return b"\x7fELF\x02\x01\x01" + b"\x00" * 100


def _shebang_bytes() -> bytes:
    return b"#!/bin/bash\necho pwn\n"


def _pdf_bytes() -> bytes:
    return b"%PDF-1.4\n%test\n" + b"\x00" * 100


# Happy path per estensione


@pytest.mark.parametrize("ext,builder", [
    ("png",  _png_bytes),
    ("jpg",  _jpeg_bytes),
    ("jpeg", _jpeg_bytes),
    ("gif",  _gif_bytes),
    ("zip",  _zip_bytes),
    ("xlsx", _xlsx_bytes),
    ("gpkg", _gpkg_bytes),
])
def test_happy_path_binary_formats(ext, builder):
    validate_upload(
        content=builder(),
        filename=f"file.{ext}",
        allowed_extensions={"png", "jpg", "jpeg", "gif", "zip", "xlsx", "gpkg"},
    )


def test_happy_path_csv_text():
    validate_upload(
        content=b"id,name,value\n1,foo,42\n2,bar,99\n",
        filename="data.csv",
        allowed_extensions={"csv"},
    )


def test_happy_path_json_text():
    validate_upload(
        content=b'{"k": "v", "n": 42}',
        filename="data.json",
        allowed_extensions={"csv", "json"},
    )


def test_happy_path_geojson_text():
    validate_upload(
        content=b'{"type": "FeatureCollection", "features": []}',
        filename="map.geojson",
        allowed_extensions={"geojson"},
    )


def test_happy_path_log_text():
    validate_upload(
        content=b"2026-04-19 10:00 INFO: avvio\n2026-04-19 10:01 ERROR: oops\n",
        filename="errors.log",
        allowed_extensions={"log", "txt"},
    )


def test_happy_path_text_with_utf8_chars():
    """Testo con accenti UTF-8."""
    validate_upload(
        content="città,prezzo\nMilano,€100\n".encode("utf-8"),
        filename="data.csv",
        allowed_extensions={"csv"},
    )


def test_happy_path_text_latin1_acceptable():
    """CSV legacy in latin-1 (caratteri stampabili) accettato."""
    validate_upload(
        content="città,prezzo\n".encode("latin-1"),
        filename="data.csv",
        allowed_extensions={"csv"},
    )


# Adversarial: eseguibile rinominato


@pytest.mark.parametrize("ext", ["csv", "png", "xlsx", "log", "json"])
def test_exe_renamed_to_allowlisted_extension_is_blocked(ext):
    with pytest.raises(MimeMismatchError, match="eseguibile|firma|MZ"):
        validate_upload(
            content=_exe_bytes(),
            filename=f"trojan.{ext}",
            allowed_extensions={"csv", "png", "xlsx", "log", "json"},
        )


@pytest.mark.parametrize("ext", ["csv", "png", "xlsx"])
def test_elf_renamed_to_allowlisted_extension_is_blocked(ext):
    with pytest.raises(MimeMismatchError, match="eseguibile|firma|ELF"):
        validate_upload(
            content=_elf_bytes(),
            filename=f"binary.{ext}",
            allowed_extensions={"csv", "png", "xlsx"},
        )


def test_shebang_script_blocked():
    with pytest.raises(MimeMismatchError, match="eseguibile|firma"):
        validate_upload(
            content=_shebang_bytes(),
            filename="data.csv",
            allowed_extensions={"csv"},
        )


# Polyglot: MIME mismatch


def test_pdf_renamed_to_png_blocked():
    """PDF spacciato per PNG → riconosciuto come application/pdf, mismatch."""
    with pytest.raises(MimeMismatchError, match="png|estensione"):
        validate_upload(
            content=_pdf_bytes(),
            filename="image.png",
            allowed_extensions={"png", "jpg", "gif"},
        )


def test_png_renamed_to_csv_blocked():
    """PNG spacciato per CSV → estensione testuale ma content e' binario noto."""
    with pytest.raises(MimeMismatchError, match="csv|testuale|binario"):
        validate_upload(
            content=_png_bytes(),
            filename="data.csv",
            allowed_extensions={"csv", "json"},
        )


def test_zip_renamed_to_jpg_blocked():
    with pytest.raises(MimeMismatchError, match="jpg|jpeg"):
        validate_upload(
            content=_zip_bytes(),
            filename="photo.jpg",
            allowed_extensions={"png", "jpg"},
        )


def test_jpeg_renamed_to_xlsx_blocked():
    with pytest.raises(MimeMismatchError, match="xlsx"):
        validate_upload(
            content=_jpeg_bytes(),
            filename="report.xlsx",
            allowed_extensions={"xlsx", "csv"},
        )


# Estensione binaria con header invalido


def test_xlsx_extension_with_text_content_blocked():
    """xlsx claim ma il content e' un CSV testuale → no magic xlsx, reject."""
    with pytest.raises(MimeMismatchError, match="xlsx|magic"):
        validate_upload(
            content=b"id,name\n1,foo\n2,bar\n",
            filename="fake.xlsx",
            allowed_extensions={"xlsx", "csv"},
        )


def test_gpkg_extension_with_random_bytes_blocked():
    """gpkg richiede header SQLite, random bytes → reject."""
    with pytest.raises(MimeMismatchError):
        validate_upload(
            content=b"\x00\x01\x02\x03" * 50 + b"random",
            filename="map.gpkg",
            allowed_extensions={"gpkg"},
        )


# Estensione mancante / non in allowlist


def test_no_extension_blocked():
    with pytest.raises(MimeMismatchError, match="estensione mancante"):
        validate_upload(
            content=_png_bytes(),
            filename="bare_filename",
            allowed_extensions={"png"},
        )


def test_extension_not_in_allowlist():
    with pytest.raises(MimeMismatchError, match="non consentita"):
        validate_upload(
            content=_pdf_bytes(),
            filename="doc.pdf",
            allowed_extensions={"png", "csv", "xlsx"},
        )


def test_empty_file_blocked():
    with pytest.raises(MimeMismatchError, match="vuoto"):
        validate_upload(
            content=b"",
            filename="empty.csv",
            allowed_extensions={"csv"},
        )


# Text format edge cases


def test_text_with_null_bytes_blocked():
    """Un .txt con null bytes (binary mascherato) → reject."""
    with pytest.raises(MimeMismatchError, match="testuale|binario"):
        validate_upload(
            content=b"hello\x00world\x00binary\x00\x00",
            filename="data.txt",
            allowed_extensions={"txt", "log"},
        )


def test_text_with_high_ratio_non_printable_blocked():
    """Latin-1 decoda tutto, ma high ratio di control chars → reject."""
    bad = bytes(range(0x01, 0x09)) * 100  # ~800 byte di control chars
    with pytest.raises(MimeMismatchError):
        validate_upload(
            content=bad,
            filename="trick.csv",
            allowed_extensions={"csv"},
        )


# Case insensitive ext


def test_case_insensitive_extension():
    validate_upload(
        content=_png_bytes(),
        filename="IMAGE.PNG",
        allowed_extensions={"png"},
    )


# detect_mime helper diretto


def test_detect_mime_recognizes_png():
    assert detect_mime(_png_bytes()) == "image/png"


def test_detect_mime_returns_none_for_text():
    assert detect_mime(b"id,name\n1,foo\n") is None


def test_detect_mime_recognizes_zip():
    assert detect_mime(_zip_bytes()) == "application/zip"


# validate_no_executable (block-only mode)


@pytest.mark.parametrize("payload,filename", [
    (_png_bytes(), "image.png"),
    (_jpeg_bytes(), "photo.jpg"),
    (_pdf_bytes(), "doc.pdf"),
    (_zip_bytes(), "archive.zip"),
    (b"id,name\n1,foo\n", "data.csv"),
    (b'{"k": 1}', "config.json"),
    (b"DXF stuff\n0\nEOF\n", "drawing.dxf"),
    (b"random business doc content\n", "report.docx"),
    (b"random bytes \xab\xcd\xef\x12 not exec", "blob.bin"),
])
def test_no_executable_accepts_any_non_executable(payload, filename):
    """Block-only mode accetta qualsiasi formato che non sia eseguibile."""
    validate_no_executable(content=payload, filename=filename)


@pytest.mark.parametrize("payload", [_exe_bytes(), _elf_bytes(), _shebang_bytes()])
def test_no_executable_blocks_executables(payload):
    """Tutte le firme eseguibili note rifiutate, qualunque estensione."""
    with pytest.raises(MimeMismatchError, match="eseguibile|firma"):
        validate_no_executable(content=payload, filename="trojan.csv")


def test_no_executable_blocks_renamed_exe():
    """evil.exe rinominato in qualsiasi cosa → magic MZ → reject."""
    with pytest.raises(MimeMismatchError):
        validate_no_executable(content=_exe_bytes(), filename="harmless.docx")


def test_no_executable_blocks_empty_file():
    with pytest.raises(MimeMismatchError, match="vuoto"):
        validate_no_executable(content=b"", filename="empty.csv")


# filetype mancante → fail-loud (no fail-open silenzioso)


def test_detect_mime_raises_explicit_if_filetype_missing(monkeypatch):
    """Pre-fix: if `filetype` import fallisce, ritornava None (fail-open).
    L'effetto era che validate_upload non sniffava nulla e accettava
    qualsiasi binary su estensione testuale. Ora deve raise con messaggio
    operativo che indica come sistemare."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "filetype":
            raise ImportError("simulated missing dep")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(MimeMismatchError, match="filetype.*non installata"):
        # Qualsiasi byte: il check esplode prima di fare qualunque guess.
        from app.utils.mime_validator import detect_mime
        detect_mime(b"\x89PNG\r\n\x1a\n")
