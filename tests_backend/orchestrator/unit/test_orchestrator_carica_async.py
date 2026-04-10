"""
tests/orchestrator/unit/test_orchestrator_carica_async.py
=========================================================
Unit test per endpoint carica-async:
  - POST /projects/{id}/carica-async (success, project not found, canvas not found)
  - CaricaAsyncRequest model validation
"""

import pytest
import logging
from unittest.mock import MagicMock, patch
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
    CaricaAsyncRequest,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_client():
    app = FastAPI()
    app.include_router(pipeline_router, prefix="/api")
    init_orchestrator(MagicMock())
    return TestClient(app, raise_server_exceptions=False)


def _mock_task_info(**overrides):
    info = MagicMock()
    info.task_id = overrides.get("task_id", "task-carica-001")
    info.status = overrides.get("status", "pending")
    info.to_dict.return_value = {
        "task_id": info.task_id,
        "status": info.status,
    }
    return info


def _carica_body(**overrides):
    body = {
        "carica_node_data": {
            "connection_id": "conn-1",
            "table_name": "target_table",
            "write_mode": "append",
            "dest_type": "database",
        },
        "format": "csv",
        "force_recalculate": True,
    }
    body.update(overrides)
    return body


# =============================================================================
# CaricaAsyncRequest model validation
# =============================================================================

class TestCaricaAsyncRequestModel:

    def test_valid_request(self):
        req = CaricaAsyncRequest(
            carica_node_data={"connection_id": "c1", "table_name": "t1"},
            format="csv",
            force_recalculate=True,
        )
        assert req.carica_node_data["connection_id"] == "c1"
        assert req.format == "csv"
        assert req.force_recalculate is True

    def test_defaults(self):
        req = CaricaAsyncRequest(
            carica_node_data={"x": 1},
        )
        assert req.format == "csv"
        assert req.force_recalculate is True

    def test_missing_carica_node_data_raises(self):
        with pytest.raises(Exception):
            CaricaAsyncRequest()

    def test_custom_format(self):
        req = CaricaAsyncRequest(
            carica_node_data={"x": 1},
            format="geojson",
        )
        assert req.format == "geojson"


# =============================================================================
# POST /projects/{id}/carica-async — success
# =============================================================================

class TestExecuteCaricaAsyncSuccess:

    def test_carica_async_success(self):
        client = make_client()
        mock_canvas = MagicMock()
        mock_canvas.model_dump.return_value = {"nodes": [], "edges": []}

        with patch("app.services.project_service.ProjectService") as mock_ps_cls, \
             patch("app.services.task_service.task_service") as mock_ts:
            mock_ps = mock_ps_cls.return_value
            mock_ps.get_project_metadata.return_value = {"id": "proj-1", "name": "Test"}
            mock_ps.get_project_canvas.return_value = mock_canvas
            mock_ts.create_carica_task.return_value = _mock_task_info()
            res = client.post("/api/projects/proj-1/carica-async", json=_carica_body())

        assert res.status_code == 200
        data = res.json()
        assert data["task_id"] == "task-carica-001"
        assert data["project_id"] == "proj-1"
        assert data["message"] == "Task CARICA avviato"

    def test_carica_async_passes_correct_params(self):
        client = make_client()
        mock_canvas = MagicMock()
        mock_canvas.model_dump.return_value = {"nodes": ["n1"], "edges": ["e1"]}

        with patch("app.services.project_service.ProjectService") as mock_ps_cls, \
             patch("app.services.task_service.task_service") as mock_ts:
            mock_ps = mock_ps_cls.return_value
            mock_ps.get_project_metadata.return_value = {"id": "proj-1"}
            mock_ps.get_project_canvas.return_value = mock_canvas
            mock_ts.create_carica_task.return_value = _mock_task_info()

            body = _carica_body(format="geojson", force_recalculate=False)
            res = client.post("/api/projects/proj-1/carica-async", json=body)

        assert res.status_code == 200
        call_kwargs = mock_ts.create_carica_task.call_args.kwargs
        assert call_kwargs["project_id"] == "proj-1"
        assert call_kwargs["output_format"] == "geojson"
        assert call_kwargs["force_recalculate"] is False
        assert call_kwargs["triggered_by"] == "manual"
        assert call_kwargs["carica_node_data"] == body["carica_node_data"]

    def test_carica_async_canvas_dict_fallback(self):
        """Canvas senza model_dump (plain dict) usa direttamente il dict."""
        client = make_client()
        canvas_dict = {"nodes": [], "edges": []}

        with patch("app.services.project_service.ProjectService") as mock_ps_cls, \
             patch("app.services.task_service.task_service") as mock_ts:
            mock_ps = mock_ps_cls.return_value
            mock_ps.get_project_metadata.return_value = {"id": "proj-1"}
            # Return a plain dict (no model_dump)
            mock_ps.get_project_canvas.return_value = canvas_dict
            mock_ts.create_carica_task.return_value = _mock_task_info()

            res = client.post("/api/projects/proj-1/carica-async", json=_carica_body())

        assert res.status_code == 200
        call_kwargs = mock_ts.create_carica_task.call_args.kwargs
        assert call_kwargs["canvas"] == canvas_dict


# =============================================================================
# POST /projects/{id}/carica-async — error paths
# =============================================================================

class TestExecuteCaricaAsyncErrors:

    def test_project_not_found_404(self):
        client = make_client()
        with patch("app.services.project_service.ProjectService") as mock_ps_cls:
            mock_ps = mock_ps_cls.return_value
            mock_ps.get_project_metadata.return_value = None
            res = client.post("/api/projects/nonexistent/carica-async", json=_carica_body())
        assert res.status_code == 404
        assert "non trovato" in res.json()["detail"]

    def test_canvas_not_found_400(self):
        client = make_client()
        with patch("app.services.project_service.ProjectService") as mock_ps_cls:
            mock_ps = mock_ps_cls.return_value
            mock_ps.get_project_metadata.return_value = {"id": "proj-1"}
            mock_ps.get_project_canvas.return_value = None
            res = client.post("/api/projects/proj-1/carica-async", json=_carica_body())
        assert res.status_code == 400
        assert "Canvas" in res.json()["detail"]

    def test_missing_body_422(self):
        client = make_client()
        res = client.post("/api/projects/proj-1/carica-async")
        assert res.status_code == 422

    def test_missing_carica_node_data_422(self):
        client = make_client()
        res = client.post("/api/projects/proj-1/carica-async", json={
            "format": "csv",
        })
        assert res.status_code == 422

    def test_invalid_carica_node_data_type_422(self):
        client = make_client()
        res = client.post("/api/projects/proj-1/carica-async", json={
            "carica_node_data": "not_a_dict",
        })
        assert res.status_code == 422
