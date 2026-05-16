"""
Core-level test per Refactor #5 Step B: ScheduleRunsCatalog.

Sostituisce la logica di lookup runs sparsa fra `download_schedule_output`
"""

from pathlib import Path

import pytest

from app.core.scheduler.runs_catalog import RunFileInfo, ScheduleRunsCatalog


pytestmark = [
    pytest.mark.unit,
    pytest.mark.scheduler,
    pytest.mark.contract,
    pytest.mark.regression,
]


# Fixtures


@pytest.fixture
def runs_layout(tmp_path):
    """Layout completo con 3 runs archiviate per schedule_short = 'abcdef123456'."""
    results = tmp_path / "results"
    runs_dir = results / "proj-1" / "runs" / "abcdef123456"
    runs_dir.mkdir(parents=True)

    # 3 runs in ordine cronologico
    timestamps = ["20260101_100000", "20260102_120000", "20260103_140000"]
    for ts in timestamps:
        run_dir = runs_dir / ts
        run_dir.mkdir()
        (run_dir / "out.csv").write_text(f"data-{ts}", encoding="utf-8")
        (run_dir / "out.parquet").write_bytes(b"PAR1" * 10)

    return results, runs_dir, timestamps


# _schedule_short: convenzione condivisa


def test_schedule_short_strips_prefix_and_truncates():
    fn = ScheduleRunsCatalog._schedule_short
    assert fn("schedule_abcdef123456789") == "abcdef123456"
    assert fn("abcdef123") == "abcdef123"
    assert fn("schedule_a") == "a"
    # Senza prefisso "schedule_" comunque tronca a 12
    assert fn("very_long_id_that_exceeds_12_chars") == "very_long_id"


def test_schedule_short_matches_artifact_store_convention():
    """Convenzione condivisa con `ScheduleArtifactStore._build_archive_dir`.
    Se questi due sono fuori sync, le runs scritte dall'archive non
    verranno trovate dal catalog → bug silente."""
    from app.core.scheduler.artifact_store import ScheduleArtifactStore

    schedule_id = "schedule_my_long_id_xyz"
    catalog_short = ScheduleRunsCatalog._schedule_short(schedule_id)

    # Controlla che ArtifactStore costruisca lo stesso path
    store = ScheduleArtifactStore(results_dir=Path("/tmp/whatever"))
    archive_dir = store._build_archive_dir(
        Path("/tmp/whatever/proj-1"), schedule_id
    )
    # archive_dir = .../proj-1/runs/{short}/{ts}
    assert archive_dir.parent.name == catalog_short


# runs_root_for


def test_runs_root_for_layout(tmp_path):
    catalog = ScheduleRunsCatalog(results_dir=tmp_path)
    root = catalog.runs_root_for("proj-1", "schedule_abc12345xyz")

    expected = (tmp_path / "proj-1" / "runs" / "abc12345xyz").resolve()
    assert root == expected


def test_runs_root_for_returns_resolved_even_if_not_existing(tmp_path):
    """Non solleva se la cartella non esiste: il caller decide cosa fare."""
    catalog = ScheduleRunsCatalog(results_dir=tmp_path)
    root = catalog.runs_root_for("ghost-proj", "schedule_x")
    assert isinstance(root, Path)
    assert root.is_absolute()


# list_runs


def test_list_runs_empty_when_no_runs_dir(tmp_path):
    catalog = ScheduleRunsCatalog(results_dir=tmp_path)
    runs = catalog.list_runs("proj-1", "schedule_x")
    assert runs == []


def test_list_runs_orders_newest_first(runs_layout):
    results, runs_dir, timestamps = runs_layout
    catalog = ScheduleRunsCatalog(results_dir=results)

    runs = catalog.list_runs("proj-1", "schedule_abcdef123456")
    assert len(runs) == 3
    # Ordine reverse: 20260103 → 20260102 → 20260101
    assert [r.timestamp for r in runs] == [
        "20260103_140000",
        "20260102_120000",
        "20260101_100000",
    ]


def test_list_runs_includes_file_metadata(runs_layout):
    results, _, _ = runs_layout
    catalog = ScheduleRunsCatalog(results_dir=results)

    runs = catalog.list_runs("proj-1", "schedule_abcdef123456")
    first_run = runs[0]
    file_names = {f.name for f in first_run.files}
    assert file_names == {"out.csv", "out.parquet"}
    assert all(isinstance(f, RunFileInfo) for f in first_run.files)
    assert first_run.total_size_bytes > 0


def test_list_runs_includes_db_result_backed_runs(monkeypatch, tmp_path):
    """DB/result-backed runs must be listed even when RESULTS_DIR is empty."""
    result_row = type(
        "ResultRow",
        (),
        {
            "project_id": "proj-1",
            "filename": "blob.csv",
            "size_bytes": 12,
        },
    )()
    monkeypatch.setattr(
        "app.core.results.store.get_result_row",
        lambda result_id: result_row if result_id == "res-1" else None,
    )

    log_store = type(
        "LogStore",
        (),
        {
            "list_completed_runs": lambda self, schedule_id: [
                {
                    "timestamp_str": "20260402_060000",
                    "execution_info": {"result_id": "res-1"},
                }
            ],
        },
    )()
    catalog = ScheduleRunsCatalog(results_dir=tmp_path, log_store=log_store)

    runs = catalog.list_runs("proj-1", "schedule_abc")

    assert len(runs) == 1
    assert runs[0].timestamp == "20260402_060000"
    assert runs[0].result_id == "res-1"
    assert runs[0].files == (
        RunFileInfo(name="blob.csv", size_bytes=12, result_id="res-1"),
    )


def test_list_runs_prefers_db_result_over_legacy_filesystem_run(
    monkeypatch,
    tmp_path,
):
    """A result-backed log is the canonical archive when timestamps collide."""
    results = tmp_path / "results"
    legacy_run_dir = results / "proj-1" / "runs" / "abc" / "20260402_060000"
    legacy_run_dir.mkdir(parents=True)
    (legacy_run_dir / "legacy.csv").write_text("old", encoding="utf-8")

    result_row = type(
        "ResultRow",
        (),
        {
            "project_id": "proj-1",
            "filename": "blob.csv",
            "size_bytes": 12,
        },
    )()
    monkeypatch.setattr(
        "app.core.results.store.get_result_row",
        lambda result_id: result_row if result_id == "res-1" else None,
    )
    log_store = type(
        "LogStore",
        (),
        {
            "list_completed_runs": lambda self, schedule_id: [
                {
                    "timestamp_str": "20260402_060000",
                    "execution_info": {"result_id": "res-1"},
                }
            ],
        },
    )()
    catalog = ScheduleRunsCatalog(results_dir=results, log_store=log_store)

    runs = catalog.list_runs("proj-1", "schedule_abc")

    assert len(runs) == 1
    assert runs[0].result_id == "res-1"
    assert runs[0].files == (
        RunFileInfo(name="blob.csv", size_bytes=12, result_id="res-1"),
    )


def test_get_run_returns_db_result_backed_run(monkeypatch, tmp_path):
    result_row = type(
        "ResultRow",
        (),
        {
            "project_id": "proj-1",
            "filename": "blob.csv",
            "size_bytes": 12,
        },
    )()
    monkeypatch.setattr(
        "app.core.results.store.get_result_row",
        lambda result_id: result_row,
    )
    log_store = type(
        "LogStore",
        (),
        {
            "list_completed_runs": lambda self, schedule_id: [
                {
                    "timestamp_str": "20260402_060000",
                    "execution_info": {"result_id": "res-1"},
                }
            ],
        },
    )()
    catalog = ScheduleRunsCatalog(results_dir=tmp_path, log_store=log_store)

    run = catalog.get_run("proj-1", "schedule_abc", "20260402_060000")

    assert run.result_id == "res-1"
    assert run.files[0].result_id == "res-1"


def test_list_runs_excludes_non_directories(tmp_path):
    """Un file casuale dentro runs_dir non deve essere elencato come run."""
    results = tmp_path / "results"
    # NB: schedule_id "schedule_abc" → schedule_short = "abc" (prefix strip)
    runs_dir = results / "proj-1" / "runs" / "abc"
    runs_dir.mkdir(parents=True)
    (runs_dir / "20260101_100000").mkdir()  # subdir valida
    (runs_dir / "stray.txt").write_text("not a run", encoding="utf-8")

    catalog = ScheduleRunsCatalog(results_dir=results)
    runs = catalog.list_runs("proj-1", "schedule_abc")
    assert len(runs) == 1
    assert runs[0].timestamp == "20260101_100000"


def test_list_runs_excludes_subdirectories_inside_run(runs_layout):
    """File metadata di una run deve escludere subdir (coerente con
    ScheduleArtifactStore che archivia solo file root)."""
    results, runs_dir, _ = runs_layout
    # Aggiungo una subdir spuria dentro una run
    (runs_dir / "20260103_140000" / "garbage").mkdir()

    catalog = ScheduleRunsCatalog(results_dir=results)
    runs = catalog.list_runs("proj-1", "schedule_abcdef123456")
    latest = runs[0]
    # Solo i 2 file originali, niente "garbage"
    file_names = {f.name for f in latest.files}
    assert file_names == {"out.csv", "out.parquet"}


# get_run_dir: sanitize + validation


@pytest.mark.parametrize(
    "bad_token",
    [
        "",
        "../../../etc/passwd",
        "20260101 100000",  # spazi
        "20260101/etc",
        "..",
        "x" * 50,  # troppo lungo
        "../proj-2",
    ],
)
def test_get_run_dir_rejects_malformed_tokens(runs_layout, bad_token):
    results, _, _ = runs_layout
    catalog = ScheduleRunsCatalog(results_dir=results)
    with pytest.raises(ValueError, match="run_token"):
        catalog.get_run_dir("proj-1", "schedule_abcdef123456", bad_token)


def test_get_run_dir_returns_existing_directory(runs_layout):
    results, runs_dir, _ = runs_layout
    catalog = ScheduleRunsCatalog(results_dir=results)
    run_dir = catalog.get_run_dir(
        "proj-1", "schedule_abcdef123456", "20260102_120000"
    )
    assert run_dir.exists()
    assert run_dir.is_dir()
    assert run_dir.name == "20260102_120000"


def test_get_run_dir_raises_when_not_found(runs_layout):
    results, _, _ = runs_layout
    catalog = ScheduleRunsCatalog(results_dir=results)
    with pytest.raises(FileNotFoundError):
        catalog.get_run_dir(
            "proj-1", "schedule_abcdef123456", "20300101_999999"
        )


def test_get_run_dir_rejects_path_policy_escape_even_with_regex_safe_token(runs_layout):
    """Se il path policy centrale esplode, il catalog deve comunque
    re-raise come ValueError sul token, non propagare il dettaglio
    del containment check."""
    results, _, _ = runs_layout
    catalog = ScheduleRunsCatalog(results_dir=results)

    with pytest.raises(ValueError, match="run_token non valido"), \
         pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "app.core.scheduler.run_archive_store.assert_path_within",
            lambda *_a, **_k: (_ for _ in ()).throw(ValueError("fuori dai trusted roots")),
        )
        catalog.get_run_dir(
            "proj-1", "schedule_abcdef123456", "20260102_120000"
        )


# latest_run_dir


def test_latest_run_dir_picks_newest_timestamp(runs_layout):
    results, _, _ = runs_layout
    catalog = ScheduleRunsCatalog(results_dir=results)
    latest = catalog.latest_run_dir("proj-1", "schedule_abcdef123456")
    assert latest is not None
    assert latest.name == "20260103_140000"


def test_latest_run_dir_returns_none_when_no_runs(tmp_path):
    catalog = ScheduleRunsCatalog(results_dir=tmp_path)
    assert catalog.latest_run_dir("proj-1", "schedule_x") is None


def test_latest_run_dir_returns_none_when_runs_dir_empty(tmp_path):
    """runs_dir esiste ma non contiene subdir: None, niente exception."""
    results = tmp_path / "results"
    runs_dir = results / "proj-1" / "runs" / "abc"
    runs_dir.mkdir(parents=True)
    catalog = ScheduleRunsCatalog(results_dir=results)
    assert catalog.latest_run_dir("proj-1", "schedule_abc") is None


# first_file_in


def test_first_file_in_returns_file(tmp_path):
    catalog = ScheduleRunsCatalog(results_dir=tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "a.csv").write_text("x", encoding="utf-8")
    result = catalog.first_file_in(run_dir)
    assert result is not None
    assert result.name == "a.csv"


def test_first_file_in_ignores_subdirectories(tmp_path):
    """Se ci sono solo subdir, ritorna None (non esplora ricorsivamente)."""
    catalog = ScheduleRunsCatalog(results_dir=tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "subdir").mkdir()
    (run_dir / "subdir" / "a.csv").write_text("x", encoding="utf-8")
    assert catalog.first_file_in(run_dir) is None


def test_first_file_in_returns_none_for_missing_dir(tmp_path):
    catalog = ScheduleRunsCatalog(results_dir=tmp_path)
    assert catalog.first_file_in(tmp_path / "ghost") is None
