"""
Smoke contract: estrazione rmtree_safe.

Storia: il modulo era `app/shared/resources/file_cleanup.py`. Spostato
in `app/core/io/local_cleanup.py` nel consolidamento finale del
boundary I/O (2026-05-26).
"""

from __future__ import annotations

import os
import stat
import sys
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# 1. Rimozione di un file regolare


def test_rmtree_safe_removes_regular_file(tmp_path):
    from app.core.io.local_cleanup import rmtree_safe

    target = tmp_path / "x.txt"
    target.write_text("hello")
    assert target.exists()

    rmtree_safe(target)
    assert not target.exists()


# 2. Rimozione di una directory annidata


def test_rmtree_safe_removes_directory_with_nested_content(tmp_path):
    from app.core.io.local_cleanup import rmtree_safe

    root = tmp_path / "deletable"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (root / "file_a.txt").write_text("a")
    (sub / "file_b.txt").write_text("b")

    rmtree_safe(root)
    assert not root.exists()


# 3. Best-effort: path gia' assente non solleva


def test_rmtree_safe_silent_on_missing_path(tmp_path):
    from app.core.io.local_cleanup import rmtree_safe

    ghost = tmp_path / "never_existed"
    assert not ghost.exists()

    # Nessuna exception, nessun side effect
    rmtree_safe(ghost)
    rmtree_safe(ghost, retries=1)


# 4. onerror chmod path: file read-only viene comunque rimosso


@pytest.mark.skipif(sys.platform == "win32", reason="Su Windows il bit ro su file singoli e' inconsistente; il path onerror si esercita meglio su POSIX")
def test_rmtree_safe_handles_readonly_file_in_directory(tmp_path):
    from app.core.io.local_cleanup import rmtree_safe

    root = tmp_path / "ro_dir"
    root.mkdir()
    target = root / "ro_file.txt"
    target.write_text("locked")
    # Set read-only sul file (su POSIX impedisce l'unlink se la dir
    # parent non e' writable). Forziamo il path onerror -> chmod +write.
    os.chmod(target, stat.S_IREAD)
    try:
        rmtree_safe(root)
        assert not root.exists(), "rmtree_safe doveva rimuovere ro dir + file"
    finally:
        # Pulizia di safety in caso il test fallisca
        if target.exists():
            try:
                os.chmod(target, stat.S_IWRITE)
            except Exception:
                pass


# 5. Failure persistente -> logger.debug chiamato (no raise)


def test_rmtree_safe_calls_logger_debug_on_persistent_failure(tmp_path):
    """Simula `shutil.rmtree` che fallisce sempre: dopo i retry, logger.debug
    chiamato con il path; nessuna exception propagata al chiamante."""
    from app.core.io.local_cleanup import rmtree_safe

    target = tmp_path / "stuck"
    target.mkdir()
    fake_logger = MagicMock()

    # Mock: shutil.rmtree solleva persistente, p.exists() resta True
    with patch("app.core.io.local_cleanup.shutil.rmtree",
               side_effect=PermissionError("locked")):
        # Best-effort: nessuna exception propagata
        rmtree_safe(target, retries=2, logger=fake_logger)

    # Logger.debug chiamato col messaggio di failure post-retry
    fake_logger.debug.assert_called()
    msg = fake_logger.debug.call_args[0][0]
    assert "rmtree_safe: cleanup fallito" in msg
    assert "stuck" in msg


def test_rmtree_safe_uses_module_logger_when_no_logger_passed(tmp_path):
    """Se logger=None, usa il logger del modulo come fallback (no raise)."""
    from app.core.io.local_cleanup import rmtree_safe

    target = tmp_path / "ghost"
    # Path non esistente: rmtree_safe deve essere idempotente e non sollevare,
    # qualunque sia il logger.
    rmtree_safe(target, logger=None)


# 6. I facade core usano la primitive del boundary di cleanup


def test_core_file_service_cleanup_delegates_to_local_cleanup_rmtree_safe(tmp_path):
    """`app.core.files.service._rmtree_safe(path)` deve invocare la primitive
    `rmtree_safe` con `_logger` del modulo core.

    NB: il mock va sull'attributo del modulo `service` (riferimento
    locale post `from app.core.io.local_cleanup import rmtree_safe`),
    non sulla source — Python `from X import Y` copia il symbol nel
    namespace del modulo importer."""
    from app.core.files import service as fs_mod

    target = tmp_path / "y.txt"
    target.write_text("z")

    with patch.object(fs_mod, "rmtree_safe") as mock_primitive:
        fs_mod._rmtree_safe(target)

    mock_primitive.assert_called_once()
    args, kwargs = mock_primitive.call_args
    assert args[0] == target
    assert kwargs["retries"] == 3
    assert kwargs["logger"] is fs_mod._logger


# 7. delete_local_file (strict mode) — nuovo nel consolidamento finale


def test_delete_local_file_removes_existing_file(tmp_path):
    from app.core.io.local_cleanup import delete_local_file

    target = tmp_path / "y.txt"
    target.write_text("z")
    delete_local_file(target)
    assert not target.exists()


def test_delete_local_file_raises_on_missing_default(tmp_path):
    from app.core.io.local_cleanup import delete_local_file

    ghost = tmp_path / "never"
    with pytest.raises(FileNotFoundError):
        delete_local_file(ghost)


def test_delete_local_file_missing_ok_silent(tmp_path):
    from app.core.io.local_cleanup import delete_local_file

    ghost = tmp_path / "never"
    delete_local_file(ghost, missing_ok=True)  # no-op


def test_delete_local_file_rejects_directory(tmp_path):
    from app.core.io.local_cleanup import delete_local_file

    d = tmp_path / "dir"
    d.mkdir()
    with pytest.raises(IsADirectoryError):
        delete_local_file(d)


# 8. Sentinel anti-regressione: il vecchio path NON deve essere riusato


def test_app_shared_resources_file_cleanup_tombstoned():
    """`app/shared/resources/file_cleanup.py` non deve piu' esistere come modulo.
    Sostituto canonical: `app.core.io.local_cleanup`."""
    with pytest.raises(ModuleNotFoundError):
        __import__("app.shared.resources.file_cleanup")
