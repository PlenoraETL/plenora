"""Core-level test per ScheduleRunsCatalog (post filesystem-archive removal).

Il catalog enumera le run dal `schedule_logs` event stream + result_id ->
DB blob / S3, niente piu' filesystem reads. I test coprono:
  - `_schedule_short` convention
  - `list_runs` da DB log stream
  - `get_run` / `latest_run` lookup
  - `_result_file_for_log` mapping execution_info -> result_id
"""

from unittest.mock import MagicMock

import pytest

from app.core.scheduler.run_archive_store import RunInfo
from app.core.scheduler.runs_catalog import RunFileInfo, ScheduleRunsCatalog


pytestmark = [
    pytest.mark.unit,
    pytest.mark.scheduler,
    pytest.mark.contract,
    pytest.mark.regression,
]


# _schedule_short: convenzione folder/URL token


def test_schedule_short_strips_prefix_and_truncates():
    fn = ScheduleRunsCatalog._schedule_short
    assert fn("schedule_abcdef123456789") == "abcdef123456"
    assert fn("abcdef123") == "abcdef123"
    assert fn("schedule_a") == "a"
    # Senza prefisso "schedule_" comunque tronca a 12
    assert fn("very_long_id_that_exceeds_12_chars") == "very_long_id"


def test_schedule_short_convention_owned_by_catalog():
    """Post-cleanup `ScheduleArtifactStore`: la convenzione `schedule_short`
    e' ora unica responsabilita' del catalog. ArtifactStore rimosso."""
    schedule_id = "schedule_my_long_id_xyz"
    catalog_short = ScheduleRunsCatalog._schedule_short(schedule_id)
    assert catalog_short == "my_long_id_x"
    assert ScheduleRunsCatalog._schedule_short("other_x") == "other_x"


def test_run_info_no_longer_exposes_archive_path():
    """RunInfo is DB-backed; archive filesystem path is not part of shape."""
    run = RunInfo(timestamp="20260101_000000")
    assert not hasattr(run, "path")


# list_runs (DB-only)


def test_list_runs_empty_when_no_completed_logs():
    """Nessun event PIPELINE_COMPLETED -> nessuna run."""
    log_store = MagicMock()
    log_store.list_completed_runs.return_value = []
    catalog = ScheduleRunsCatalog(log_store=log_store)
    assert catalog.list_runs("proj-1", "schedule_x") == []


def test_list_runs_skips_entry_without_timestamp():
    """Entry senza timestamp viene saltato (no RunInfo emesso)."""
    log_store = MagicMock()
    log_store.list_completed_runs.return_value = [
        {"execution_info": {}},  # no timestamp_str
    ]
    catalog = ScheduleRunsCatalog(log_store=log_store)
    assert catalog.list_runs("proj-1", "schedule_x") == []


def test_list_runs_orders_newest_first_and_dedupes_by_timestamp():
    """Multipli entry stesso timestamp: vince quello con result_id."""
    log_store = MagicMock()
    log_store.list_completed_runs.return_value = [
        {"timestamp_str": "20260101_100000", "execution_info": {}},
        {
            "timestamp_str": "20260102_120000",
            "execution_info": {"result_id": "res-a"},
        },
        {"timestamp_str": "20260103_140000", "execution_info": {}},
        # Duplicate timestamp con result_id: vince
        {
            "timestamp_str": "20260101_100000",
            "execution_info": {"result_id": "res-b"},
        },
    ]
    catalog = ScheduleRunsCatalog(log_store=log_store)
    runs = catalog.list_runs("proj-1", "schedule_x")
    timestamps = [r.timestamp for r in runs]
    assert timestamps == [
        "20260103_140000", "20260102_120000", "20260101_100000",
    ]
    # Il dedupe ha tenuto la versione con result_id
    by_ts = {r.timestamp: r for r in runs}
    assert by_ts["20260101_100000"].result_id == "res-b"


def test_get_run_returns_matching_timestamp():
    log_store = MagicMock()
    log_store.list_completed_runs.return_value = [
        {
            "timestamp_str": "20260102_120000",
            "execution_info": {"result_id": "res-1"},
        },
    ]
    catalog = ScheduleRunsCatalog(log_store=log_store)
    run = catalog.get_run("proj-1", "schedule_x", "20260102_120000")
    assert run.timestamp == "20260102_120000"
    assert run.result_id == "res-1"


def test_get_run_raises_on_missing_timestamp():
    log_store = MagicMock()
    log_store.list_completed_runs.return_value = []
    catalog = ScheduleRunsCatalog(log_store=log_store)
    with pytest.raises(FileNotFoundError, match="non trovata"):
        catalog.get_run("proj-1", "schedule_x", "20260101_000000")


def test_get_run_rejects_empty_token():
    catalog = ScheduleRunsCatalog(log_store=MagicMock())
    with pytest.raises(ValueError, match="run_token mancante"):
        catalog.get_run("proj-1", "schedule_x", "")


def test_latest_run_returns_first_of_list_runs():
    log_store = MagicMock()
    log_store.list_completed_runs.return_value = [
        {"timestamp_str": "20260103_140000", "execution_info": {}},
        {"timestamp_str": "20260101_100000", "execution_info": {}},
    ]
    catalog = ScheduleRunsCatalog(log_store=log_store)
    latest = catalog.latest_run("proj-1", "schedule_x")
    assert latest is not None
    assert latest.timestamp == "20260103_140000"


def test_latest_run_none_when_no_runs():
    log_store = MagicMock()
    log_store.list_completed_runs.return_value = []
    catalog = ScheduleRunsCatalog(log_store=log_store)
    assert catalog.latest_run("proj-1", "schedule_x") is None


# _result_file_for_log: execution_info -> RunFileInfo


def test_result_file_for_log_parses_json_string():
    """execution_info puo' arrivare come JSON-string dal DB."""
    from unittest.mock import patch

    log_store = MagicMock()
    catalog = ScheduleRunsCatalog(log_store=log_store)

    row = MagicMock()
    row.project_id = "proj-1"
    row.filename = "out.csv"
    row.size_bytes = 100

    with patch(
        "app.core.results.store.get_result_row",
        return_value=row,
    ):
        result_id, file_info = catalog._result_file_for_log(
            {"execution_info": '{"result_id": "res-1"}'},
            project_id="proj-1",
        )

    assert result_id == "res-1"
    assert isinstance(file_info, RunFileInfo)
    assert file_info.name == "out.csv"
    assert file_info.size_bytes == 100
    assert file_info.result_id == "res-1"


def test_result_file_for_log_returns_none_when_cross_project():
    """row.project_id != expected project_id: file_info None (anti
    cross-tenant leak), ma result_id ritornato per debug."""
    from unittest.mock import patch

    log_store = MagicMock()
    catalog = ScheduleRunsCatalog(log_store=log_store)

    row = MagicMock()
    row.project_id = "OTHER-proj"
    row.filename = "out.csv"
    row.size_bytes = 100

    with patch(
        "app.core.results.store.get_result_row",
        return_value=row,
    ):
        result_id, file_info = catalog._result_file_for_log(
            {"execution_info": {"result_id": "res-1"}},
            project_id="proj-1",
        )

    assert result_id == "res-1"
    assert file_info is None


def test_result_file_for_log_returns_none_when_no_result_id():
    log_store = MagicMock()
    catalog = ScheduleRunsCatalog(log_store=log_store)
    result_id, file_info = catalog._result_file_for_log(
        {"execution_info": {}},
        project_id="proj-1",
    )
    assert result_id is None
    assert file_info is None
