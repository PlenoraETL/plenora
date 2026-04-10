"""
tests/orchestrator/unit/test_orchestrator_pipeline.py
=====================================================
Unit test per endpoint pipeline del router Orchestrator:
  - POST /projects/{id}/execute
  - GET  /projects/{id}/preview
  - GET  /projects/{id}/download
  - GET  /projects/{id}/output-info
  - _mime_for_filename helper
  - _human_size helper
"""

import pytest
import logging
from unittest.mock import MagicMock, patch
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def silence_logs(caplog):
    with caplog.at_level(logging.CRITICAL, logger="app.core.orchestrator.router"):
        yield


from app.core.orchestrator.router import (
    pipeline_router,
    init_orchestrator,
    _mime_for_filename,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_client(orchestrator=None):
    """Crea TestClient con orchestrator iniettato."""
    app = FastAPI()
    app.include_router(pipeline_router, prefix="/api")
    if orchestrator is None:
        orchestrator = MagicMock()
    init_orchestrator(orchestrator)
    return TestClient(app, raise_server_exceptions=False)


def _mock_task_info(**overrides):
    info = MagicMock()
    d = {
        "task_id": "task-001",
        "task_type": "execute",
        "project_id": "proj-1",
        "status": "pending",
        "progress": 0,
        "message": "",
        "result": None,
        "error": None,
        "created_at": "2026-01-01T00:00:00",
        "started_at": None,
        "completed_at": None,
    }
    d.update(overrides)
    info.to_dict.return_value = d
    return info


# =============================================================================
# _mime_for_filename
# =============================================================================

class TestMimeForFilename:

    def test_csv(self):
        assert _mime_for_filename("output.csv") == "text/csv"

    def test_xlsx(self):
        assert _mime_for_filename("report.xlsx") == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_json(self):
        assert _mime_for_filename("data.json") == "application/json"

    def test_geojson(self):
        assert _mime_for_filename("layer.geojson") == "application/geo+json"

    def test_gpkg(self):
        assert _mime_for_filename("db.gpkg") == "application/geopackage+sqlite3"

    def test_shp_zip(self):
        assert _mime_for_filename("layer.shp") == "application/zip"

    def test_unknown_extension(self):
        assert _mime_for_filename("file.xyz") == "application/octet-stream"

    def test_no_extension(self):
        assert _mime_for_filename("noext") == "application/octet-stream"

    def test_case_insensitive(self):
        assert _mime_for_filename("DATA.CSV") == "text/csv"

    def test_parquet(self):
        assert _mime_for_filename("data.parquet") == "application/octet-stream"


# =============================================================================
# POST /projects/{id}/execute
# =============================================================================

class TestExecutePipeline:

    def test_execute_success(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.create_pipeline_task.return_value = _mock_task_info()
            res = client.post("/api/projects/proj-1/execute", json={"force_recalculate": False})
        assert res.status_code == 200
        data = res.json()
        assert data["async"] is True
        assert data["task_id"] == "task-001"

    def test_execute_with_force_recalculate(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.create_pipeline_task.return_value = _mock_task_info()
            res = client.post("/api/projects/proj-1/execute", json={"force_recalculate": True})
        assert res.status_code == 200
        mock_ts.create_pipeline_task.assert_called_once_with(
            project_id="proj-1", force_recalculate=True
        )

    def test_execute_no_body(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.create_pipeline_task.return_value = _mock_task_info()
            res = client.post("/api/projects/proj-1/execute")
        assert res.status_code == 200

    def test_execute_service_error_500(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.create_pipeline_task.side_effect = RuntimeError("Celery down")
            res = client.post("/api/projects/proj-1/execute")
        assert res.status_code == 500

    def test_execute_orchestrator_not_initialized_503(self):
        client = make_client()
        init_orchestrator(None)
        res = client.post("/api/projects/proj-1/execute")
        assert res.status_code == 503


# =============================================================================
# GET /projects/{id}/preview
# =============================================================================

class TestPreviewProject:

    def test_preview_success(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.create_preview_task.return_value = _mock_task_info(task_type="preview")
            res = client.get("/api/projects/proj-1/preview")
        assert res.status_code == 200
        data = res.json()
        assert data["async"] is True

    def test_preview_custom_limit(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.create_preview_task.return_value = _mock_task_info()
            res = client.get("/api/projects/proj-1/preview?limit=50")
        assert res.status_code == 200
        mock_ts.create_preview_task.assert_called_once_with(project_id="proj-1", limit=50)

    def test_preview_error_500(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.create_preview_task.side_effect = RuntimeError("fail")
            res = client.get("/api/projects/proj-1/preview")
        assert res.status_code == 500


# =============================================================================
# GET /projects/{id}/download
# =============================================================================

class TestDownloadProjectResult:

    def test_download_no_results_dir_404(self, tmp_path):
        client = make_client()
        with patch("app.core.orchestrator.router.settings") as mock_s:
            mock_s.RESULTS_DIR = tmp_path / "results"
            res = client.get("/api/projects/proj-1/download")
        assert res.status_code == 404

    def test_download_latest_file(self, tmp_path):
        results_dir = tmp_path / "results" / "proj-1"
        results_dir.mkdir(parents=True)
        f = results_dir / "output.csv"
        f.write_text("a,b\n1,2")
        client = make_client()
        with patch("app.core.orchestrator.router.settings") as mock_s:
            mock_s.RESULTS_DIR = tmp_path / "results"
            res = client.get("/api/projects/proj-1/download")
        assert res.status_code == 200

    def test_download_specific_file(self, tmp_path):
        results_dir = tmp_path / "results" / "proj-1"
        results_dir.mkdir(parents=True)
        (results_dir / "errors.csv").write_text("err\nfoo")
        client = make_client()
        with patch("app.core.orchestrator.router.settings") as mock_s:
            mock_s.RESULTS_DIR = tmp_path / "results"
            res = client.get("/api/projects/proj-1/download?file=errors.csv")
        assert res.status_code == 200

    def test_download_specific_file_not_found_404(self, tmp_path):
        results_dir = tmp_path / "results" / "proj-1"
        results_dir.mkdir(parents=True)
        (results_dir / "other.csv").write_text("x")
        client = make_client()
        with patch("app.core.orchestrator.router.settings") as mock_s:
            mock_s.RESULTS_DIR = tmp_path / "results"
            res = client.get("/api/projects/proj-1/download?file=missing.csv")
        assert res.status_code == 404

    def test_download_path_traversal_blocked(self, tmp_path):
        results_dir = tmp_path / "results" / "proj-1"
        results_dir.mkdir(parents=True)
        (results_dir / "ok.csv").write_text("data")
        client = make_client()
        with patch("app.core.orchestrator.router.settings") as mock_s:
            mock_s.RESULTS_DIR = tmp_path / "results"
            res = client.get("/api/projects/proj-1/download?file=../../../etc/passwd")
        # Path.name strips traversal, so it becomes "passwd" which doesn't exist -> 404
        assert res.status_code in (403, 404)

    def test_download_empty_dir_404(self, tmp_path):
        results_dir = tmp_path / "results" / "proj-1"
        results_dir.mkdir(parents=True)
        client = make_client()
        with patch("app.core.orchestrator.router.settings") as mock_s:
            mock_s.RESULTS_DIR = tmp_path / "results"
            res = client.get("/api/projects/proj-1/download")
        assert res.status_code == 404


