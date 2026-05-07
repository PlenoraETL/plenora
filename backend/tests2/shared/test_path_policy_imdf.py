"""
Contract test — app/shared/path_policy.validate_imdf_task_id (D3 commit 1).

Centralizza la validazione formato bundle_task_id + path traversal guard
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.shared.path_policy import validate_imdf_task_id


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# 1. Valid task_id → Path resolved dentro root


def test_valid_uuid_task_id_returns_resolved_path(tmp_path: Path):
    """UUID-like task_id → Path resolved, dentro root, absolute."""
    root = tmp_path / "bundles"
    root.mkdir()
    task_id = "a1b2c3d4-5678-90ab-cdef-1234567890ab"

    result = validate_imdf_task_id(task_id, root.resolve())

    assert result.is_absolute()
    # Deve essere dentro root (relative_to non solleva)
    result.relative_to(root.resolve())
    assert result.name == task_id


def test_valid_task_id_max_length_accepted(tmp_path: Path):
    """Regex ammette fino a 128 char (1 iniziale + {0,127} = 128 totali)."""
    root = tmp_path / "bundles"
    root.mkdir()
    # 128 char: 1 alfanumerico iniziale + 127 alfanumerici/_/-
    task_id = "a" + "b" * 127
    assert len(task_id) == 128

    result = validate_imdf_task_id(task_id, root.resolve())

    assert result.name == task_id


# 2. Id vuoto / None → ValueError


def test_empty_task_id_raises(tmp_path: Path):
    root = tmp_path / "bundles"
    root.mkdir()
    with pytest.raises(ValueError, match="non valido"):
        validate_imdf_task_id("", root.resolve())


def test_none_task_id_raises(tmp_path: Path):
    """None viene rifiutato dal guard `if not bundle_task_id`."""
    root = tmp_path / "bundles"
    root.mkdir()
    with pytest.raises(ValueError, match="non valido"):
        validate_imdf_task_id(None, root.resolve())  # type: ignore[arg-type]


# 3. Formato invalido (dot, slash, spazi, null bytes) → ValueError


@pytest.mark.parametrize("bad_id", [
    "..",
    "..abc",
    "a.b",
    "abc.",
    "a/b",
    "a\\b",
    "/absolute",
    "with space",
    "with\tcontrol",
    "null\x00byte",
    "uniàcode",  # non-ascii
])
def test_task_id_with_forbidden_chars_rejected(tmp_path: Path, bad_id: str):
    root = tmp_path / "bundles"
    root.mkdir()
    with pytest.raises(ValueError, match="non valido"):
        validate_imdf_task_id(bad_id, root.resolve())


# 4. Primo char non alfanumerico → ValueError


@pytest.mark.parametrize("bad_id", ["-abc", "_abc", "-", "_"])
def test_task_id_starts_with_dash_or_underscore_rejected(tmp_path: Path, bad_id: str):
    """Regex impone `[A-Za-z0-9]` primo char: '-' e '_' iniziali rifiutati."""
    root = tmp_path / "bundles"
    root.mkdir()
    with pytest.raises(ValueError, match="non valido"):
        validate_imdf_task_id(bad_id, root.resolve())


# 5. Length boundary: 129 char rifiutato


def test_task_id_exceeds_length_rejected(tmp_path: Path):
    """Regex limita a 128 char totali; 129 deve failare."""
    root = tmp_path / "bundles"
    root.mkdir()
    task_id = "a" + "b" * 128  # 129 char
    with pytest.raises(ValueError, match="non valido"):
        validate_imdf_task_id(task_id, root.resolve())


# 6. Dash/underscore validi in posizioni non-iniziali


@pytest.mark.parametrize("good_id", [
    "a-b-c",
    "a_b_c",
    "a-_-b",
    "abc123",
    "ABC_def-123",
])
def test_dash_underscore_allowed_after_first_char(tmp_path: Path, good_id: str):
    root = tmp_path / "bundles"
    root.mkdir()
    result = validate_imdf_task_id(good_id, root.resolve())
    assert result.name == good_id
