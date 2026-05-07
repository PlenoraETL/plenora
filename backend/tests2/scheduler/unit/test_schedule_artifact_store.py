"""
Core-level test per Refactor #5 Step A: ScheduleArtifactStore.

Sostituisce la logica `_archive_output` di `ExecutionManager` come
"""

from datetime import datetime
from pathlib import Path

import pytest

from app.core.scheduler.artifact_store import ScheduleArtifactStore


pytestmark = [
    pytest.mark.unit,
    pytest.mark.scheduler,
    pytest.mark.contract,
    pytest.mark.regression,
]


@pytest.fixture
def project_layout(tmp_path):
    """Crea un layout RESULTS_DIR/{project_id}/ con qualche file."""
    results_dir = tmp_path / "results"
    project_dir = results_dir / "proj-1"
    project_dir.mkdir(parents=True)
    (project_dir / "out.csv").write_text("id\n1\n2\n", encoding="utf-8")
    (project_dir / "out.parquet").write_bytes(b"PAR1")
    return results_dir, project_dir


# DI: results_dir esplicito vs default da settings


def test_init_uses_explicit_results_dir(tmp_path):
    """Il costruttore accetta results_dir come DI, niente monkey-patch
    di settings necessario per i test."""
    custom = tmp_path / "custom"
    store = ScheduleArtifactStore(results_dir=custom)
    # Verifichiamo via comportamento (results_dir non ha getter pubblico)
    result = store.archive("nonexistent-proj", "schedule_abc")
    assert result is None  # no project dir → None


def test_init_falls_back_to_settings_when_results_dir_none(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import app.config as _cfg

    fake_results = tmp_path / "fake_results"
    fake_results.mkdir()
    monkeypatch.setattr(
        _cfg, "settings",
        SimpleNamespace(RESULTS_DIR=fake_results),
        raising=False,
    )
    store = ScheduleArtifactStore()  # no DI → fallback settings
    # Sanity: non solleva, e ritorna None su progetto inesistente
    assert store.archive("ghost", "schedule_x") is None


# Path success: archivia file in cartella timestamped


def test_archive_all_files_when_output_file_blank(project_layout):
    results_dir, project_dir = project_layout
    store = ScheduleArtifactStore(results_dir=results_dir)

    relative = store.archive("proj-1", "schedule_abcdef123")

    assert relative is not None
    archive_path = results_dir / relative
    assert archive_path.exists()
    assert archive_path.is_dir()
    # Tutti i file root archiviati
    archived_names = {f.name for f in archive_path.iterdir()}
    assert archived_names == {"out.csv", "out.parquet"}


def test_archive_specific_output_file_when_specified(project_layout):
    results_dir, project_dir = project_layout
    store = ScheduleArtifactStore(results_dir=results_dir)

    relative = store.archive("proj-1", "schedule_abc", output_file="out.csv")

    assert relative is not None
    archive_path = results_dir / relative
    archived_names = {f.name for f in archive_path.iterdir()}
    # SOLO out.csv deve essere stato archiviato
    assert archived_names == {"out.csv"}


def test_archive_path_layout(project_layout):
    """Layout deve essere {project_id}/runs/{schedule_short}/{timestamp}/."""
    results_dir, _ = project_layout
    store = ScheduleArtifactStore(results_dir=results_dir)

    relative = store.archive("proj-1", "schedule_my_long_id_here_more")

    parts = Path(relative).parts
    assert parts[0] == "proj-1"
    assert parts[1] == "runs"
    # schedule_short = id.replace("schedule_", "")[:12]
    assert parts[2] == "my_long_id_h"
    # parts[3] = timestamp YYYYMMDD_HHMMSS, formato 15 char con underscore
    assert len(parts[3]) == 15
    assert parts[3][8] == "_"


def test_archive_excludes_existing_runs_subdir(tmp_path):
    """Sanity: il loop iterdir copia solo file (non directory),
    quindi una `runs/` esistente da una run precedente NON viene
    archiviata ricorsivamente."""
    results_dir = tmp_path / "results"
    project_dir = results_dir / "proj-1"
    project_dir.mkdir(parents=True)
    (project_dir / "out.csv").write_text("x", encoding="utf-8")
    # Una runs/ pre-esistente (dalla scorsa esecuzione)
    pre_existing_runs = project_dir / "runs"
    pre_existing_runs.mkdir()
    (pre_existing_runs / "old.csv").write_text("old", encoding="utf-8")

    store = ScheduleArtifactStore(results_dir=results_dir)
    relative = store.archive("proj-1", "schedule_x")

    assert relative is not None
    archive_path = results_dir / relative
    archived_names = {f.name for f in archive_path.iterdir()}
    # Solo out.csv (la runs/ è una directory, non viene copiata)
    assert "out.csv" in archived_names
    assert "runs" not in archived_names


# Path errore: ritorna None senza propagare


def test_archive_returns_none_when_project_dir_missing(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    store = ScheduleArtifactStore(results_dir=results_dir)

    result = store.archive("ghost-project", "schedule_x")
    assert result is None


def test_archive_returns_none_when_no_files_to_copy(tmp_path):
    """Project dir esiste ma è vuota → return None, niente exception."""
    results_dir = tmp_path / "results"
    project_dir = results_dir / "proj-empty"
    project_dir.mkdir(parents=True)
    store = ScheduleArtifactStore(results_dir=results_dir)

    result = store.archive("proj-empty", "schedule_x")
    assert result is None


def test_archive_returns_none_when_output_file_does_not_exist(project_layout):
    """output_file specificato ma il file non esiste → fallback a all
    files (i.e. comportamento storico: branch `if output_file and is_file`
    è False → cade nel branch all files)."""
    results_dir, _ = project_layout
    store = ScheduleArtifactStore(results_dir=results_dir)

    relative = store.archive(
        "proj-1", "schedule_x", output_file="ghost.csv"
    )
    # Comportamento storico preservato: cade nel branch `iterdir all files`
    assert relative is not None
    archive_path = results_dir / relative
    archived_names = {f.name for f in archive_path.iterdir()}
    assert "out.csv" in archived_names


def test_archive_swallows_exceptions_and_returns_none(monkeypatch, project_layout):
    """L'archive e' best-effort: se shutil.copy2 esplode, la pipeline
    non deve fallire — return None + warning."""
    results_dir, _ = project_layout
    store = ScheduleArtifactStore(results_dir=results_dir)

    import shutil as _sh

    def _boom(src, dst):
        raise OSError("disk full simulato")

    monkeypatch.setattr(_sh, "copy2", _boom)

    result = store.archive("proj-1", "schedule_x")
    assert result is None
