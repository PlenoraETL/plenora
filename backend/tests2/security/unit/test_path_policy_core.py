"""
Core-level test per Refactor #4 Step A: assert_path_within.

Helper minimale che centralizza il pattern path validation duplicato
"""

from pathlib import Path

import pytest

from app.shared.path_policy import assert_path_within


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


# UNC reject (Windows network share / SMB)


@pytest.mark.parametrize(
    "unc_path",
    [
        "\\\\server\\share\\secret.txt",
        "//server/share/secret.txt",
        "\\\\evil\\admin$\\config",
    ],
)
def test_rejects_unc_paths_before_resolve(unc_path, tmp_path):
    """UNC paths sollevano PRIMA di Path.resolve(): su Windows
    resolve() puo' tentare il lookup SMB e introdurre latenze di
    rete misurabili da side-channel."""
    with pytest.raises(ValueError, match="UNC"):
        assert_path_within(unc_path, [tmp_path])


# Path traversal blocked


def test_rejects_path_traversal_outside_root(tmp_path):
    root = tmp_path / "results"
    root.mkdir()
    evil = root / ".." / "etc" / "passwd"

    with pytest.raises(ValueError, match="fuori dai trusted roots"):
        assert_path_within(str(evil), [root])


def test_rejects_path_inside_other_root(tmp_path):
    """Multi-tenant: i due root sono separati, niente cross-leak."""
    root_a = tmp_path / "tenant-a"
    root_b = tmp_path / "tenant-b"
    root_a.mkdir()
    root_b.mkdir()
    target_b = root_b / "secret.txt"
    target_b.write_text("x", encoding="utf-8")

    # Candidate in root_b ma trusted_roots ha solo root_a
    with pytest.raises(ValueError, match="fuori dai trusted roots"):
        assert_path_within(str(target_b), [root_a])


# Multi-root: passa se dentro QUALSIASI root


def test_accepts_path_in_first_trusted_root(tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    target = root_a / "x.csv"
    target.write_text("ok", encoding="utf-8")

    resolved = assert_path_within(str(target), [root_a, root_b])
    assert resolved == target.resolve()


def test_accepts_path_in_second_trusted_root(tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    target = root_b / "x.csv"
    target.write_text("ok", encoding="utf-8")

    resolved = assert_path_within(str(target), [root_a, root_b])
    assert resolved == target.resolve()


# Trusted roots vuoto = caller bug, fail-closed


def test_empty_trusted_roots_is_caller_bug():
    """Iterable vuoto non ha senso: nessun path puo' essere 'dentro'
    una lista vuota. Fail-closed esplicito invece di accettare
    silenziosamente qualsiasi path."""
    with pytest.raises(ValueError, match="trusted_roots vuoto"):
        assert_path_within("/etc/passwd", [])


# must_exist


def test_must_exist_false_accepts_nonexistent_path_inside_root(tmp_path):
    """Default `must_exist=False`: il path puo' non esistere ancora
    (es. file di output da scrivere). Solo l'ownership del root e'
    verificata."""
    root = tmp_path / "results"
    root.mkdir()
    candidate = root / "future_output.csv"
    # Non lo creiamo
    resolved = assert_path_within(str(candidate), [root])
    assert resolved == candidate.resolve()


def test_must_exist_true_rejects_nonexistent_path(tmp_path):
    root = tmp_path / "results"
    root.mkdir()
    candidate = root / "ghost.csv"
    with pytest.raises(ValueError, match="non esiste"):
        assert_path_within(str(candidate), [root], must_exist=True)


def test_must_exist_true_accepts_existing_path(tmp_path):
    root = tmp_path / "results"
    root.mkdir()
    candidate = root / "real.csv"
    candidate.write_text("ok", encoding="utf-8")
    resolved = assert_path_within(str(candidate), [root], must_exist=True)
    assert resolved == candidate.resolve()


def test_must_exist_true_accepts_existing_directory(tmp_path):
    root = tmp_path / "results"
    root.mkdir()
    subdir = root / "subdir"
    subdir.mkdir()
    resolved = assert_path_within(str(subdir), [root], must_exist=True)
    assert resolved == subdir.resolve()


# Input flexibility: stringa o Path


def test_accepts_path_object_as_candidate(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    target = root / "x.csv"
    target.write_text("ok", encoding="utf-8")

    resolved = assert_path_within(target, [root])
    assert resolved == target.resolve()


def test_accepts_string_roots(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    target = root / "x.csv"
    target.write_text("ok", encoding="utf-8")

    # roots come stringhe, non Path
    resolved = assert_path_within(str(target), [str(root)])
    assert resolved == target.resolve()


# Return value: Path resolved (utility per caller che evita doppio resolve)


def test_returns_resolved_path_not_raw_input(tmp_path):
    """Il caller riceve indietro il path RESOLVED (assoluto, simlink
    risolti) non la stringa originale. Cosi' puo' usarlo direttamente
    per leggere il file senza un secondo resolve()."""
    root = tmp_path / "r"
    root.mkdir()
    target = root / "x.csv"
    target.write_text("ok", encoding="utf-8")

    # Path relativo dentro root (es. ./x.csv via cwd)
    resolved = assert_path_within(str(target), [root])
    assert resolved.is_absolute()
    assert resolved == target.resolve()


# Path malformato → ValueError, non eccezione cruda


def test_malformed_path_raises_value_error(tmp_path):
    """Path che fa esplodere `Path.resolve()` (es. null bytes su POSIX,
    char invalidi su Windows) deve essere catturato e tradotto in
    ValueError fail-closed, non propagare l'eccezione cruda."""
    root = tmp_path / "r"
    root.mkdir()
    bad = "\x00invalid"
    with pytest.raises(ValueError):
        assert_path_within(bad, [root])
