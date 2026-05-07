"""
Tests the scheduler router behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from pathlib import Path
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
         patch("app.db.config.schedules.get_schedule", return_value={"id": schedule_id, "group_id": "grp-1"}), \
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
         patch("app.db.config.schedules.get_schedule", return_value={"id": schedule_id, "group_id": "grp-1"}):
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
         patch("app.db.config.schedules.get_schedule", return_value={"id": schedule_id, "group_id": "grp-1"}), \
         patch("app.config.settings", new=SimpleNamespace(RESULTS_DIR=tmp_path)):
        response = client.get(
            f"/api/schedules/{schedule_id}/download",
            params={"run": "20260402_060000"},
        )

    assert response.status_code == 200
    content_disposition = response.headers.get("content-disposition", "")
    assert "requested.csv" in content_disposition


def test_list_runs_returns_archived_runs_with_sizes(current_user, tmp_path):
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
         patch("app.db.config.schedules.get_schedule", return_value={"id": schedule_id, "group_id": "grp-1"}), \
         patch("app.config.settings", new=SimpleNamespace(RESULTS_DIR=tmp_path)):
        response = client.get(f"/api/schedules/{schedule_id}/runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runs_count"] == 1
    assert payload["runs"][0]["timestamp"] == "20260402_060000"
    assert payload["runs"][0]["files"][0]["name"] == "output.csv"


def test_list_runs_returns_empty_payload_when_no_archived_runs(current_user):
    client = _build_client(current_user)
    schedule_id = "schedule_123456789abc"
    project_id = "proj-1"
    schedule_cfg = SimpleNamespace(pipeline=SimpleNamespace(project_id=project_id))

    with patch("app.core.scheduler.schedule_service.ScheduleService.get_schedule", return_value=schedule_cfg), \
         patch("app.db.config.schedules.get_schedule", return_value={"id": schedule_id, "group_id": "grp-1"}):
        response = client.get(f"/api/schedules/{schedule_id}/runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schedule_id"] == schedule_id
    assert payload["project_id"] == project_id
    assert payload["runs_count"] == 0
    assert payload["runs"] == []
