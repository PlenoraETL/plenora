"""
tests/orchestrator/unit/test_orchestrator_tasks.py
==================================================
Unit test per endpoint task CRUD del router Orchestrator:
  - POST /tasks (create enrichment, carica, pipeline, preview, missing data)
  - GET  /tasks/{id} (success, 404, sanitize Decimal/NaN)
  - DELETE /tasks/{id} (cancel)
  - GET  /tasks/{id}/result
  - GET  /tasks/{id}/download
  - GET  /tasks (list all)
  - GET  /tasks/active/list
"""

import pytest
import logging
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from decimal import Decimal

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def silence_logs(caplog):
    with caplog.at_level(logging.CRITICAL, logger="app.core.orchestrator.router"):
        yield


from app.core.orchestrator.router import tasks_router
from app.services.task_service import TaskType, TaskStatus


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_client():
    app = FastAPI()
    app.include_router(tasks_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def _mock_task_info(**overrides):
    info = MagicMock()
    d = {
        "task_id": "task-001",
        "task_type": "enrichment",
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
    info.status = TaskStatus(d["status"]) if d["status"] in [s.value for s in TaskStatus] else MagicMock(value=d["status"])
    info.result = d.get("result")
    info.task_id = d["task_id"]
    return info


# =============================================================================
# POST /tasks — create task
# =============================================================================

class TestCreateTask:

    def test_create_enrichment_task(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.create_enrichment_task.return_value = _mock_task_info()
            res = client.post("/api/tasks", json={
                "task_type": "enrichment",
                "project_id": "proj-1",
                "canvas": {"nodes": [], "edges": []},
                "output_format": "csv",
            })
        assert res.status_code == 200
        assert res.json()["task_id"] == "task-001"

    def test_create_carica_task(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.create_carica_task.return_value = _mock_task_info(task_type="carica")
            res = client.post("/api/tasks", json={
                "task_type": "carica",
                "project_id": "proj-1",
                "canvas": {"nodes": []},
                "carica_node_data": {"connection_id": "conn-1", "table_name": "t1"},
            })
        assert res.status_code == 200

    def test_create_carica_missing_node_data_400(self):
        client = make_client()
        res = client.post("/api/tasks", json={
            "task_type": "carica",
            "project_id": "proj-1",
            "canvas": {"nodes": []},
        })
        assert res.status_code == 400

    def test_create_unsupported_type_400(self):
        """Task types that exist in enum but unsupported in create_task endpoint."""
        client = make_client()
        with patch("app.services.task_service.task_service"):
            res = client.post("/api/tasks", json={
                "task_type": "execute",
                "project_id": "proj-1",
                "canvas": {},
            })
        assert res.status_code == 400

    def test_create_invalid_type_422(self):
        client = make_client()
        res = client.post("/api/tasks", json={
            "task_type": "nonexistent",
            "project_id": "proj-1",
            "canvas": {},
        })
        assert res.status_code == 422

    def test_create_missing_fields_422(self):
        client = make_client()
        res = client.post("/api/tasks", json={})
        assert res.status_code == 422

    def test_create_service_crash_500(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.create_enrichment_task.side_effect = RuntimeError("boom")
            res = client.post("/api/tasks", json={
                "task_type": "enrichment",
                "project_id": "proj-1",
                "canvas": {},
            })
        assert res.status_code == 500


# =============================================================================
# GET /tasks/{id} — task status
# =============================================================================

class TestGetTaskStatus:

    def test_get_status_success(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.get_task_status.return_value = _mock_task_info(status="success")
            res = client.get("/api/tasks/task-001")
        assert res.status_code == 200
        assert res.json()["status"] == "success"

    def test_get_status_pending_no_created_at_404(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.get_task_status.return_value = _mock_task_info(
                status="pending", created_at=None
            )
            res = client.get("/api/tasks/nonexistent")
        assert res.status_code == 404

    def test_get_status_sanitizes_decimal(self):
        client = make_client()
        result_with_decimal = {
            "value": Decimal("3.14"),
            "count": Decimal("10"),
        }
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.get_task_status.return_value = _mock_task_info(
                status="success",
                result=result_with_decimal,
            )
            res = client.get("/api/tasks/task-001")
        assert res.status_code == 200
        data = res.json()
        result = data.get("result", {})
        # Decimal("3.14") -> 3.14, Decimal("10") -> 10
        assert result["value"] == 3.14
        assert result["count"] == 10

    def test_get_status_sanitizes_nan(self):
        client = make_client()
        import math
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.get_task_status.return_value = _mock_task_info(
                status="success",
                result={"val": float("nan"), "inf_val": float("inf")},
            )
            res = client.get("/api/tasks/task-001")
        assert res.status_code == 200
        data = res.json()
        result = data.get("result", {})
        assert result["val"] is None
        assert result["inf_val"] is None

    def test_get_status_service_exception_404(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.get_task_status.side_effect = RuntimeError("Celery error")
            res = client.get("/api/tasks/task-bad")
        assert res.status_code == 404

    def test_get_status_progress_coerced_to_int(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.get_task_status.return_value = _mock_task_info(
                status="progress", progress=55.0
            )
            res = client.get("/api/tasks/task-001")
        assert res.status_code == 200
        assert res.json()["progress"] == 55


# =============================================================================
# DELETE /tasks/{id} — cancel
# =============================================================================

class TestCancelTask:

    def test_cancel_success(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.cancel_task.return_value = True
            res = client.delete("/api/tasks/task-001")
        assert res.status_code == 200
        assert res.json()["status"] == "cancelled"

    def test_cancel_failure_500(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.cancel_task.return_value = False
            res = client.delete("/api/tasks/task-001")
        assert res.status_code == 500

    def test_cancel_exception_500(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.cancel_task.side_effect = RuntimeError("fail")
            res = client.delete("/api/tasks/task-001")
        assert res.status_code == 500


# =============================================================================
# GET /tasks/{id}/download
# =============================================================================

class TestDownloadTaskResult:

    def test_download_success(self):
        client = make_client()
        task_info = _mock_task_info(
            status="success",
            result={"project_id": "proj-1"},
        )
        db_row = {"id": "res-1", "filename": "output.csv"}
        with patch("app.services.task_service.task_service") as mock_ts, \
             patch("app.db.db_service.get_latest_result", return_value=db_row), \
             patch("app.db.db_service.get_result_data", return_value=b"a,b\n1,2"):
            mock_ts.get_task_status.return_value = task_info
            res = client.get("/api/tasks/task-001/download")
        assert res.status_code == 200
        assert "attachment" in res.headers.get("content-disposition", "")

    def test_download_not_completed_400(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.get_task_status.return_value = _mock_task_info(status="started")
            res = client.get("/api/tasks/task-001/download")
        assert res.status_code == 400

    def test_download_no_project_id_404(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.get_task_status.return_value = _mock_task_info(
                status="success", result={}
            )
            res = client.get("/api/tasks/task-001/download")
        assert res.status_code == 404

    def test_download_no_db_row_404(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts, \
             patch("app.db.db_service.get_latest_result", return_value=None):
            mock_ts.get_task_status.return_value = _mock_task_info(
                status="success", result={"project_id": "proj-1"}
            )
            res = client.get("/api/tasks/task-001/download")
        assert res.status_code == 404

    def test_download_no_data_404(self):
        client = make_client()
        with patch("app.services.task_service.task_service") as mock_ts, \
             patch("app.db.db_service.get_latest_result", return_value={"id": "r1", "filename": "x.csv"}), \
             patch("app.db.db_service.get_result_data", return_value=None):
            mock_ts.get_task_status.return_value = _mock_task_info(
                status="success", result={"project_id": "proj-1"}
            )
            res = client.get("/api/tasks/task-001/download")
        assert res.status_code == 404


