"""
Tests the zip safe behavior (bomb guards).

Post Punto 3 cleanup (2026-05-25): `safe_extractall` / `safe_extract_zip_path` /
`ZipSlipError` rimossi. Il flow RAM-only legge member tramite
`z.read(name)` su bytes: no disk write, no zip-slip vulnerability.
Le bomb guard (`validate_zip_bomb_limits`) restano attive e DEVONO
essere chiamate prima di qualunque `z.read()`.
"""

from io import BytesIO
import zipfile

import pytest

from app.utils.zip_safe import ZipBombError, validate_zip_bomb_limits


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


def _build_zip(*entries, compression=zipfile.ZIP_STORED):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as zf:
        for name, content in entries:
            if isinstance(content, str):
                content = content.encode("utf-8")
            zf.writestr(name, content)
    buffer.seek(0)
    return zipfile.ZipFile(buffer, "r")


# Tombstone sentinel: i symbol path-based zip-slip sono fuori contratto.


def test_zip_safe_tombstones_path_based_symbols_post_punto3():
    """Punto 3 cleanup: rimossi `safe_extractall` (disk-write),
    `safe_extract_zip_path`, `ZipSlipError`. Tutti i caller RAM-only
    leggono via `z.read(name)` su bytes."""
    import app.utils.zip_safe as zs

    for legacy in ("safe_extractall", "safe_extract_zip_path",
                   "ZipSlipError", "_validate_member"):
        assert not hasattr(zs, legacy), (
            f"zip_safe.{legacy} ancora esposto: cleanup Punto 3 incompleto."
        )


# Zip bomb: total uncompressed size cap


def test_validate_rejects_total_size_over_cap(monkeypatch):
    """3 file da 200 byte ciascuno con cap 500 byte → 400+200>500 → BombError."""
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", "500")
    monkeypatch.setenv("ZIP_MAX_FILES", "100")
    payload = b"X" * 200
    with _build_zip(
        ("a.bin", payload), ("b.bin", payload), ("c.bin", payload),
    ) as zf:
        with pytest.raises(ZipBombError, match="dimensione totale"):
            validate_zip_bomb_limits(zf)


def test_validate_total_size_at_cap_succeeds(monkeypatch):
    """File totale = cap esatto: passa (check e' strict >)."""
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", "500")
    payload = b"Y" * 500
    with _build_zip(("only.bin", payload)) as zf:
        validate_zip_bomb_limits(zf)


# Zip bomb: per-single-file cap


def test_validate_rejects_single_file_over_cap(monkeypatch):
    """Un file da 600 byte con cap singolo a 500 → BombError, anche
    se il cap totale e' molto piu' alto."""
    monkeypatch.setenv("ZIP_MAX_SINGLE_FILE_BYTES", "500")
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", "10000000")
    payload = b"Z" * 600
    with _build_zip(("big.bin", payload)) as zf:
        with pytest.raises(ZipBombError, match="troppo grande"):
            validate_zip_bomb_limits(zf)


def test_validate_single_file_at_cap_succeeds(monkeypatch):
    """Cap singolo strict: file == cap passa."""
    monkeypatch.setenv("ZIP_MAX_SINGLE_FILE_BYTES", "500")
    payload = b"K" * 500
    with _build_zip(("at.bin", payload)) as zf:
        validate_zip_bomb_limits(zf)


def test_single_file_cap_default_equals_total(monkeypatch):
    """Senza ZIP_MAX_SINGLE_FILE_BYTES esplicito, eredita ZIP_MAX_UNCOMPRESSED.
    Quindi un file = totale e' OK (no constraint extra di default)."""
    monkeypatch.delenv("ZIP_MAX_SINGLE_FILE_BYTES", raising=False)
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", "1000")
    payload = b"M" * 1000
    with _build_zip(("solo.bin", payload)) as zf:
        validate_zip_bomb_limits(zf)


# Zip bomb: file count cap


def test_validate_rejects_too_many_files(monkeypatch):
    """Cap a 3 file, archivio con 5 → BombError."""
    monkeypatch.setenv("ZIP_MAX_FILES", "3")
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", "10000000")
    entries = [(f"f{i}.txt", "x") for i in range(5)]
    with _build_zip(*entries) as zf:
        with pytest.raises(ZipBombError, match="troppi file"):
            validate_zip_bomb_limits(zf)


def test_validate_file_count_at_cap_succeeds(monkeypatch):
    monkeypatch.setenv("ZIP_MAX_FILES", "3")
    entries = [(f"f{i}.txt", "x") for i in range(3)]
    with _build_zip(*entries) as zf:
        validate_zip_bomb_limits(zf)


# Zip bomb: compression ratio


def test_validate_rejects_high_compression_ratio(monkeypatch):
    """File da 2MB di zeri (uncompressed) → comprime ottimo (ratio enorme).
    Cap ratio = 10 → BombError."""
    monkeypatch.setenv("ZIP_MAX_COMPRESSION_RATIO", "10")
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", str(10 * 1024 * 1024))
    payload = b"\0" * (2 * 1024 * 1024)  # 2 MB di zero, comprime a poche centinaia di byte
    with _build_zip(
        ("bomb.bin", payload), compression=zipfile.ZIP_DEFLATED,
    ) as zf:
        with pytest.raises(ZipBombError, match="compression ratio"):
            validate_zip_bomb_limits(zf)


def test_validate_small_files_skip_ratio_check(monkeypatch):
    """File piccoli (< 1MB) saltano il ratio check anche se ratio alto.
    Un .txt di 100 byte di zeri ha ratio enorme ma non e' un bomb."""
    monkeypatch.setenv("ZIP_MAX_COMPRESSION_RATIO", "5")
    payload = b"\0" * 1000  # 1KB ben sotto la soglia 1MB
    with _build_zip(("small.bin", payload), compression=zipfile.ZIP_DEFLATED) as zf:
        validate_zip_bomb_limits(zf)


def test_validate_normal_compression_ratio_passes(monkeypatch):
    """File 2MB di dati pseudo-random: ratio prossimo a 1 → OK."""
    monkeypatch.setenv("ZIP_MAX_COMPRESSION_RATIO", "10")
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", str(10 * 1024 * 1024))
    import os as _os
    payload = _os.urandom(2 * 1024 * 1024)
    with _build_zip(
        ("data.bin", payload), compression=zipfile.ZIP_DEFLATED,
    ) as zf:
        validate_zip_bomb_limits(zf)


# Env override semantics


def test_invalid_env_value_falls_back_to_default(monkeypatch):
    """ZIP_MAX_FILES='abc' → ignorato, default 5000 si applica."""
    monkeypatch.setenv("ZIP_MAX_FILES", "abc")
    # Archivio piccolo che passerebbe il default ma non '0'/'-1'
    with _build_zip(("a.txt", "x"), ("b.txt", "y")) as zf:
        validate_zip_bomb_limits(zf)


def test_negative_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", "-100")
    with _build_zip(("a.txt", "x")) as zf:
        validate_zip_bomb_limits(zf)
