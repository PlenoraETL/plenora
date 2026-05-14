"""Contracts for the scheduled-run filesystem archive boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.scheduler.run_archive_store import (
    ScheduleRunArchiveStore,
    schedule_short,
)


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def test_schedule_short_is_shared_archive_token() -> None:
    assert schedule_short("schedule_abcdef123456789") == "abcdef123456"
    assert schedule_short("plain-id") == "plain-id"


def test_archive_store_owns_runs_root_layout(tmp_path: Path) -> None:
    store = ScheduleRunArchiveStore(results_dir=tmp_path)

    assert store.runs_root_for("proj-1", "schedule_abcdef123456789") == (
        tmp_path / "proj-1" / "runs" / "abcdef123456"
    ).resolve()


def test_archive_store_lists_file_metadata_and_excludes_subdirs(tmp_path: Path) -> None:
    run_dir = tmp_path / "proj-1" / "runs" / "abc" / "20260101_100000"
    run_dir.mkdir(parents=True)
    (run_dir / "out.csv").write_text("id\n1\n", encoding="utf-8")
    (run_dir / "nested").mkdir()

    store = ScheduleRunArchiveStore(results_dir=tmp_path)
    runs = store.list_runs("proj-1", "schedule_abc")

    assert [run.timestamp for run in runs] == ["20260101_100000"]
    assert [file.name for file in runs[0].files] == ["out.csv"]


def test_archive_store_get_run_dir_uses_path_policy(monkeypatch, tmp_path: Path) -> None:
    store = ScheduleRunArchiveStore(results_dir=tmp_path)

    with pytest.raises(ValueError, match="run_token non valido"):
        monkeypatch.setattr(
            "app.core.scheduler.run_archive_store.assert_path_within",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("escape")),
        )
        store.get_run_dir("proj-1", "schedule_abc", "20260101_100000")
