"""
Tests the scheduler router behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.auth import require_not_esecutore
from app.core.scheduler.router import router


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract, pytest.mark.regression]


def _build_client(current_user):
    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: current_user
    app.include_router(router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_scheduler_heartbeat_parser_accepts_native_pg_datetime():
    from app.core.scheduler.router import _as_aware_datetime

    native_ts = datetime(2026, 5, 8, 9, tzinfo=timezone.utc)

    assert _as_aware_datetime(native_ts) is native_ts


def test_download_without_run_uses_latest_archived_schedule_run(current_user, tmp_path):
    client = _build_client(current_user)

    schedule_id = "schedule_123456789abc"
    project_id = "proj-1"
    schedule_short = schedule_id.replace("schedule_", "")[:12]
    older = tmp_path / project_id / "runs" / schedule_short / "20260401_060000"
    newer = tmp_path / project_id / "runs" / schedule_short / "20260402_060000"
    older.mkdir(parents=True, exist_ok=True)
    newer.mkdir(parents=True, exist_ok=True)
    (older / "older.csv").write_text("id\n1\n", encoding="utf-8")
    (newer / "newer.csv").write_text("id\n2\n", encoding="utf-8")

    schedule_cfg = SimpleNamespace(pipeline=SimpleNamespace(project_id=project_id))

    with patch("app.core.scheduler.schedule_service.ScheduleService.get_schedule", return_value=schedule_cfg), \
         patch("app.core.scheduler.db.get_schedule_row", return_value=SimpleNamespace(id=schedule_id, group_id="grp-1")), \
         patch("app.config.settings", new=SimpleNamespace(RESULTS_DIR=tmp_path)):
        response = client.get(f"/api/schedules/{schedule_id}/download")

    assert response.status_code == 200
    content_disposition = response.headers.get("content-disposition", "")
    assert "newer.csv" in content_disposition


def test_download_rejects_malformed_run_parameter(current_user):
    client = _build_client(current_user)
    schedule_id = "schedule_123456789abc"
    schedule_cfg = SimpleNamespace(pipeline=SimpleNamespace(project_id="proj-1"))

    with patch("app.core.scheduler.schedule_service.ScheduleService.get_schedule", return_value=schedule_cfg), \
         patch("app.core.scheduler.db.get_schedule_row", return_value=SimpleNamespace(id=schedule_id, group_id="grp-1")):
        response = client.get(f"/api/schedules/{schedule_id}/download", params={"run": "../evil"})

    assert response.status_code == 400


def test_download_specific_run_returns_requested_file(current_user, tmp_path):
    client = _build_client(current_user)

    schedule_id = "schedule_123456789abc"
    project_id = "proj-1"
    schedule_short = schedule_id.replace("schedule_", "")[:12]
    run_dir = tmp_path / project_id / "runs" / schedule_short / "20260402_060000"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "requested.csv").write_text("id\n7\n", encoding="utf-8")

    schedule_cfg = SimpleNamespace(pipeline=SimpleNamespace(project_id=project_id))

    with patch("app.core.scheduler.schedule_service.ScheduleService.get_schedule", return_value=schedule_cfg), \
         patch("app.core.scheduler.db.get_schedule_row", return_value=SimpleNamespace(id=schedule_id, group_id="grp-1")), \
         patch("app.config.settings", new=SimpleNamespace(RESULTS_DIR=tmp_path)):
        response = client.get(
            f"/api/schedules/{schedule_id}/download",
            params={"run": "20260402_060000"},
        )

    assert response.status_code == 200
    content_disposition = response.headers.get("content-disposition", "")
    assert "requested.csv" in content_disposition


def test_download_blob_only_run_uses_schedule_result_id(current_user, tmp_path):
    client = _build_client(current_user)

    schedule_id = "schedule_123456789abc"
    project_id = "proj-1"
    schedule_cfg = SimpleNamespace(pipeline=SimpleNamespace(project_id=project_id))
    result_row = SimpleNamespace(
        id="res-1",
        project_id=project_id,
        filename="blob.csv",
    )

    with patch("app.core.scheduler.schedule_service.ScheduleService.get_schedule", return_value=schedule_cfg), \
         patch("app.core.scheduler.db.get_schedule_row", return_value=SimpleNamespace(id=schedule_id, group_id="grp-1")), \
         patch("app.core.scheduler.db.list_schedule_logs", return_value=[{"execution_info": {"result_id": "res-1"}}]), \
         patch("app.core.results.store.get_result_row", return_value=result_row), \
         patch("app.core.results.store.get_result_data", return_value=b"id\n9\n"), \
         patch("app.config.settings", new=SimpleNamespace(RESULTS_DIR=tmp_path)):
        response = client.get(f"/api/schedules/{schedule_id}/download")

    assert response.status_code == 200
    assert response.content == b"id\n9\n"
    assert "blob.csv" in response.headers.get("content-disposition", "")


def test_download_specific_blob_only_run_uses_requested_result_id(
    current_user, tmp_path,
):
    client = _build_client(current_user)

    schedule_id = "schedule_123456789abc"
    project_id = "proj-1"
    schedule_cfg = SimpleNamespace(pipeline=SimpleNamespace(project_id=project_id))
    result_rows = {
        "res-old": SimpleNamespace(
            id="res-old",
            project_id=project_id,
            filename="old.csv",
            size_bytes=6,
        ),
        "res-new": SimpleNamespace(
            id="res-new",
            project_id=project_id,
            filename="new.csv",
            size_bytes=6,
        ),
    }

    def fake_get_result_row(result_id):
        return result_rows.get(result_id)

    def fake_get_result_data(result_id, group_id=None):
        assert group_id == "grp-1"
        return {"res-old": b"old\n", "res-new": b"new\n"}[result_id]

    logs = [
        {
            "timestamp_str": "20260402_060000",
            "execution_info": {"result_id": "res-new"},
        },
        {
            "timestamp_str": "20260401_060000",
            "execution_info": {"result_id": "res-old"},
        },
    ]

    with patch("app.core.scheduler.schedule_service.ScheduleService.get_schedule", return_value=schedule_cfg), \
         patch("app.core.scheduler.db.get_schedule_row", return_value=SimpleNamespace(id=schedule_id, group_id="grp-1")), \
         patch("app.core.scheduler.db.list_schedule_logs", return_value=logs), \
         patch("app.core.results.store.get_result_row", side_effect=fake_get_result_row), \
         patch("app.core.results.store.get_result_data", side_effect=fake_get_result_data), \
         patch("app.config.settings", new=SimpleNamespace(RESULTS_DIR=tmp_path)):
        response = client.get(
            f"/api/schedules/{schedule_id}/download",
            params={"run": "20260401_060000"},
        )

    assert response.status_code == 200
    assert response.content == b"old\n"
    assert "old.csv" in response.headers.get("content-disposition", "")


def test_download_blob_only_run_rejects_cross_project_result(current_user, tmp_path):
    client = _build_client(current_user)

    schedule_id = "schedule_123456789abc"
    schedule_cfg = SimpleNamespace(pipeline=SimpleNamespace(project_id="proj-1"))
    result_row = SimpleNamespace(id="res-1", project_id="other", filename="blob.csv")

    with patch("app.core.scheduler.schedule_service.ScheduleService.get_schedule", return_value=schedule_cfg), \
         patch("app.core.scheduler.db.get_schedule_row", return_value=SimpleNamespace(id=schedule_id, group_id="grp-1")), \
         patch("app.core.scheduler.db.list_schedule_logs", return_value=[{"execution_info": {"result_id": "res-1"}}]), \
         patch("app.core.results.store.get_result_row", return_value=result_row), \
         patch("app.config.settings", new=SimpleNamespace(RESULTS_DIR=tmp_path)):
        response = client.get(f"/api/schedules/{schedule_id}/download")

    assert response.status_code == 404


def test_list_runs_filesystem_archive_no_longer_supported(current_user, tmp_path):
    """Post-cleanup: il catalog non legge piu' dal filesystem archive
    (`RESULTS_DIR/{project_id}/runs/{schedule}/{ts}/`). Le run vecchie
    archived-only-on-filesystem non appaiono piu' nella lista — solo
    le run con `result_id` nei `schedule_logs` sono enumerate.

    Sentinel: anche con una run dir filesystem presente, `runs_count == 0`
    se non c'e' DB log corrispondente.
    """
    client = _build_client(current_user)
    schedule_id = "schedule_123456789abc"
    project_id = "proj-1"
    schedule_short = schedule_id.replace("schedule_", "")[:12]
    run_dir = tmp_path / project_id / "runs" / schedule_short / "20260402_060000"
    run_dir.mkdir(parents=True, exist_ok=True)
    file_path = run_dir / "output.csv"
    file_path.write_text("id\n1\n2\n", encoding="utf-8")

    schedule_cfg = SimpleNamespace(pipeline=SimpleNamespace(project_id=project_id))

    with patch("app.core.scheduler.schedule_service.ScheduleService.get_schedule", return_value=schedule_cfg), \
         patch("app.core.scheduler.db.get_schedule_row", return_value=SimpleNamespace(id=schedule_id, group_id="grp-1")), \
         patch("app.config.settings", new=SimpleNamespace(RESULTS_DIR=tmp_path)):
        response = client.get(f"/api/schedules/{schedule_id}/runs")

    assert response.status_code == 200
    payload = response.json()
    # Filesystem archive ignorato: 0 run (sarebbe 1 col vecchio behavior).
    assert payload["runs_count"] == 0


def test_list_runs_returns_blob_backed_runs_with_sizes(current_user, tmp_path):
    client = _build_client(current_user)
    schedule_id = "schedule_123456789abc"
    project_id = "proj-1"
    schedule_cfg = SimpleNamespace(pipeline=SimpleNamespace(project_id=project_id))
    result_row = SimpleNamespace(
        id="res-1",
        project_id=project_id,
        filename="blob.csv",
        size_bytes=8,
    )

    with patch("app.core.scheduler.schedule_service.ScheduleService.get_schedule", return_value=schedule_cfg), \
         patch("app.core.scheduler.db.get_schedule_row", return_value=SimpleNamespace(id=schedule_id, group_id="grp-1")), \
         patch("app.core.scheduler.db.list_schedule_logs", return_value=[{"timestamp_str": "20260402_060000", "execution_info": {"result_id": "res-1"}}]), \
         patch("app.core.results.store.get_result_row", return_value=result_row), \
         patch("app.config.settings", new=SimpleNamespace(RESULTS_DIR=tmp_path)):
        response = client.get(f"/api/schedules/{schedule_id}/runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runs_count"] == 1
    assert payload["runs"][0]["timestamp"] == "20260402_060000"
    assert payload["runs"][0]["files"][0]["name"] == "blob.csv"
    assert payload["runs"][0]["files"][0]["size_bytes"] == 8


def test_list_runs_returns_empty_payload_when_no_archived_runs(current_user):
    client = _build_client(current_user)
    schedule_id = "schedule_123456789abc"
    project_id = "proj-1"
    schedule_cfg = SimpleNamespace(pipeline=SimpleNamespace(project_id=project_id))

    with patch("app.core.scheduler.schedule_service.ScheduleService.get_schedule", return_value=schedule_cfg), \
         patch("app.core.scheduler.db.get_schedule_row", return_value=SimpleNamespace(id=schedule_id, group_id="grp-1")):
        response = client.get(f"/api/schedules/{schedule_id}/runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schedule_id"] == schedule_id
    assert payload["project_id"] == project_id
    assert payload["runs_count"] == 0
    assert payload["runs"] == []
