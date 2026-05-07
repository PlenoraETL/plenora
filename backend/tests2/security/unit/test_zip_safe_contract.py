"""
Tests the zip safe behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from io import BytesIO
import zipfile

import pytest

from app.utils.zip_safe import ZipBombError, ZipSlipError, safe_extractall


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


# Zip slip (regression)


def test_safe_extractall_rejects_parent_traversal(tmp_path):
    with _build_zip(("../evil.txt", "boom")) as zf:
        with pytest.raises(ZipSlipError, match="esce da"):
            safe_extractall(zf, tmp_path)


def test_safe_extractall_rejects_windows_drive_paths(tmp_path):
    with _build_zip(("C:/windows/system32.txt", "boom")) as zf:
        with pytest.raises(ZipSlipError, match="drive letter"):
            safe_extractall(zf, tmp_path)


def test_safe_extractall_extracts_regular_members(tmp_path):
    with _build_zip(("nested/data.txt", "ok")) as zf:
        safe_extractall(zf, tmp_path)

    assert (tmp_path / "nested" / "data.txt").read_text(encoding="utf-8") == "ok"


# Zip bomb: total uncompressed size cap


def test_safe_extractall_rejects_total_size_over_cap(tmp_path, monkeypatch):
    """3 file da 200 byte ciascuno con cap 500 byte → 400+200>500 → BombError."""
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", "500")
    monkeypatch.setenv("ZIP_MAX_FILES", "100")
    payload = b"X" * 200
    with _build_zip(
        ("a.bin", payload), ("b.bin", payload), ("c.bin", payload),
    ) as zf:
        with pytest.raises(ZipBombError, match="dimensione totale"):
            safe_extractall(zf, tmp_path)
    # Disco non toccato (i 3 file non devono esserci)
    assert not list(tmp_path.iterdir())


def test_safe_extractall_total_size_at_cap_succeeds(tmp_path, monkeypatch):
    """File totale = cap esatto: passa (check e' strict >)."""
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", "500")
    payload = b"Y" * 500
    with _build_zip(("only.bin", payload)) as zf:
        safe_extractall(zf, tmp_path)
    assert (tmp_path / "only.bin").stat().st_size == 500


# Zip bomb: per-single-file cap


def test_safe_extractall_rejects_single_file_over_cap(tmp_path, monkeypatch):
    """Un file da 600 byte con cap singolo a 500 → BombError, anche
    se il cap totale e' molto piu' alto."""
    monkeypatch.setenv("ZIP_MAX_SINGLE_FILE_BYTES", "500")
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", "10000000")
    payload = b"Z" * 600
    with _build_zip(("big.bin", payload)) as zf:
        with pytest.raises(ZipBombError, match="troppo grande"):
            safe_extractall(zf, tmp_path)
    assert not list(tmp_path.iterdir())


def test_safe_extractall_single_file_at_cap_succeeds(tmp_path, monkeypatch):
    """Cap singolo strict: file == cap passa."""
    monkeypatch.setenv("ZIP_MAX_SINGLE_FILE_BYTES", "500")
    payload = b"K" * 500
    with _build_zip(("at.bin", payload)) as zf:
        safe_extractall(zf, tmp_path)
    assert (tmp_path / "at.bin").stat().st_size == 500


def test_single_file_cap_default_equals_total(tmp_path, monkeypatch):
    """Senza ZIP_MAX_SINGLE_FILE_BYTES esplicito, eredita ZIP_MAX_UNCOMPRESSED.
    Quindi un file = totale e' OK (no constraint extra di default)."""
    monkeypatch.delenv("ZIP_MAX_SINGLE_FILE_BYTES", raising=False)
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", "1000")
    payload = b"M" * 1000
    with _build_zip(("solo.bin", payload)) as zf:
        safe_extractall(zf, tmp_path)
    assert (tmp_path / "solo.bin").stat().st_size == 1000


# Zip bomb: file count cap


def test_safe_extractall_rejects_too_many_files(tmp_path, monkeypatch):
    """Cap a 3 file, archivio con 5 → BombError."""
    monkeypatch.setenv("ZIP_MAX_FILES", "3")
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", "10000000")
    entries = [(f"f{i}.txt", "x") for i in range(5)]
    with _build_zip(*entries) as zf:
        with pytest.raises(ZipBombError, match="troppi file"):
            safe_extractall(zf, tmp_path)
    assert not list(tmp_path.iterdir())


def test_safe_extractall_file_count_at_cap_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIP_MAX_FILES", "3")
    entries = [(f"f{i}.txt", "x") for i in range(3)]
    with _build_zip(*entries) as zf:
        safe_extractall(zf, tmp_path)
    assert len(list(tmp_path.iterdir())) == 3


# Zip bomb: compression ratio


def test_safe_extractall_rejects_high_compression_ratio(tmp_path, monkeypatch):
    """File da 2MB di zeri (uncompressed) → comprime ottimo (ratio enorme).
    Cap ratio = 10 → BombError."""
    monkeypatch.setenv("ZIP_MAX_COMPRESSION_RATIO", "10")
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", str(10 * 1024 * 1024))
    payload = b"\0" * (2 * 1024 * 1024)  # 2 MB di zero, comprime a poche centinaia di byte
    with _build_zip(
        ("bomb.bin", payload), compression=zipfile.ZIP_DEFLATED,
    ) as zf:
        with pytest.raises(ZipBombError, match="compression ratio"):
            safe_extractall(zf, tmp_path)


def test_safe_extractall_small_files_skip_ratio_check(tmp_path, monkeypatch):
    """File piccoli (< 1MB) saltano il ratio check anche se ratio alto.
    Un .txt di 100 byte di zeri ha ratio enorme ma non e' un bomb."""
    monkeypatch.setenv("ZIP_MAX_COMPRESSION_RATIO", "5")
    payload = b"\0" * 1000  # 1KB ben sotto la soglia 1MB
    with _build_zip(("small.bin", payload), compression=zipfile.ZIP_DEFLATED) as zf:
        safe_extractall(zf, tmp_path)
    assert (tmp_path / "small.bin").stat().st_size == 1000


def test_safe_extractall_normal_compression_ratio_passes(tmp_path, monkeypatch):
    """File 2MB di dati pseudo-random: ratio prossimo a 1 → OK."""
    monkeypatch.setenv("ZIP_MAX_COMPRESSION_RATIO", "10")
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", str(10 * 1024 * 1024))
    import os as _os
    payload = _os.urandom(2 * 1024 * 1024)
    with _build_zip(
        ("data.bin", payload), compression=zipfile.ZIP_DEFLATED,
    ) as zf:
        safe_extractall(zf, tmp_path)
    assert (tmp_path / "data.bin").stat().st_size == 2 * 1024 * 1024


# Env override semantics


def test_invalid_env_value_falls_back_to_default(tmp_path, monkeypatch):
    """ZIP_MAX_FILES='abc' → ignorato, default 5000 si applica."""
    monkeypatch.setenv("ZIP_MAX_FILES", "abc")
    # Archivio piccolo che passerebbe il default ma non '0'/'-1'
    with _build_zip(("a.txt", "x"), ("b.txt", "y")) as zf:
        safe_extractall(zf, tmp_path)
    assert (tmp_path / "a.txt").exists()


def test_negative_env_value_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIP_MAX_UNCOMPRESSED_BYTES", "-100")
    with _build_zip(("a.txt", "x")) as zf:
        safe_extractall(zf, tmp_path)
    assert (tmp_path / "a.txt").exists()


# Bomb checks pre-extract: nessun file scritto se cap violato


def test_bomb_check_runs_before_any_disk_write(tmp_path, monkeypatch):
    """Anche se l'archivio ha file 'innocui' come primi member, il bomb
    check deve scoprire la violazione PRIMA di scrivere alcunche'."""
    monkeypatch.setenv("ZIP_MAX_FILES", "2")
    # 3 member: il primo 'innocuo', poi 2 che fanno superare il cap (3>2)
    with _build_zip(
        ("first.txt", "ok"), ("second.txt", "ok"), ("third.txt", "ok"),
    ) as zf:
        with pytest.raises(ZipBombError):
            safe_extractall(zf, tmp_path)
    # Nessuno dei 3 file scritto
    assert not (tmp_path / "first.txt").exists()
    assert not (tmp_path / "second.txt").exists()
    assert not (tmp_path / "third.txt").exists()
