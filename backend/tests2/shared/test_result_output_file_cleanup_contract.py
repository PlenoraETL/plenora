"""Contract tests for project result output-file cleanup boundary."""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_delete_project_output_file_is_project_scoped(tmp_path):
    from app.core.results import output_file_cleanup

    project_dir = tmp_path / "p1"
    project_dir.mkdir()
    target = project_dir / "out.csv"
    target.write_text("x", encoding="utf-8")

    assert output_file_cleanup.delete_project_output_file(
        "p1",
        "out.csv",
        root=tmp_path,
    )
    assert not target.exists()


def test_delete_project_output_file_sanitizes_filename(tmp_path):
    from app.core.results import output_file_cleanup

    project_dir = tmp_path / "p1"
    project_dir.mkdir()
    target = project_dir / "out.csv"
    target.write_text("x", encoding="utf-8")

    assert output_file_cleanup.delete_project_output_file(
        "p1",
        "../out.csv",
        root=tmp_path,
    )
    assert not target.exists()


def test_legacy_result_file_store_module_is_removed():
    import importlib.util

    assert importlib.util.find_spec("app.core.results.legacy_file_store") is None
