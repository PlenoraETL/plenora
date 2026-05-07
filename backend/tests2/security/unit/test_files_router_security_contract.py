"""
Tests the files router security behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.core.files.router import _assert_path_within, _sanitize_upload_filename


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


def test_sanitize_upload_filename_rejects_reserved_parent_name():
    with pytest.raises(HTTPException) as exc:
        _sanitize_upload_filename("..")

    assert exc.value.status_code == 400


def test_sanitize_upload_filename_reduces_to_safe_basename():
    sanitized = _sanitize_upload_filename("..\\nested\\report.csv")

    assert sanitized == "report.csv"


def test_assert_path_within_rejects_resolved_escape(tmp_path):
    parent = tmp_path / "uploads"
    parent.mkdir()
    child = parent / ".." / "outside.txt"

    with pytest.raises(HTTPException) as exc:
        _assert_path_within(child, parent, label="Upload")

    assert exc.value.status_code == 403


def test_assert_path_within_accepts_child_inside_parent(tmp_path):
    parent = tmp_path / "uploads"
    parent.mkdir()
    child = parent / "safe.csv"

    resolved = _assert_path_within(child, parent, label="Upload")

    assert isinstance(resolved, Path)
    assert resolved.name == "safe.csv"


def test_assert_path_within_maps_generic_validation_error_to_400(tmp_path):
    parent = tmp_path / "uploads"
    parent.mkdir()
    child = parent / "safe.csv"

    with patch(
        "app.core.files.router._core_assert",
        side_effect=ValueError("path non valido: nul byte"),
    ):
        with pytest.raises(HTTPException) as exc:
            _assert_path_within(child, parent, label="Upload")

    assert exc.value.status_code == 400
    assert "Upload non valido" in exc.value.detail
