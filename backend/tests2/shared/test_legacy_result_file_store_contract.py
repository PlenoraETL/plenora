"""Contract tests for legacy filesystem result boundary."""

from __future__ import annotations

import os

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_resolve_specific_file_sanitizes_filename(tmp_path):
    from app.core.results import legacy_file_store

    project_dir = tmp_path / "p1"
    project_dir.mkdir()
    target = project_dir / "out.csv"
    target.write_text("x", encoding="utf-8")

    assert legacy_file_store.resolve_specific_file(
        "p1",
        "../out.csv",
        root=tmp_path,
    ) == target


def test_resolve_latest_file_returns_newest_file(tmp_path):
    from app.core.results import legacy_file_store

    project_dir = tmp_path / "p1"
    project_dir.mkdir()
    old = project_dir / "old.csv"
    new = project_dir / "new.csv"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))

    assert legacy_file_store.resolve_latest_file("p1", root=tmp_path) == new


def test_delete_output_file_is_project_scoped(tmp_path):
    from app.core.results import legacy_file_store

    project_dir = tmp_path / "p1"
    project_dir.mkdir()
    target = project_dir / "out.csv"
    target.write_text("x", encoding="utf-8")

    assert legacy_file_store.delete_output_file("p1", "out.csv", root=tmp_path)
    assert not target.exists()
