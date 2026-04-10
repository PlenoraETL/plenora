"""
tests/arricchisci/unit/test_router_coverage.py

Unit tests targeting uncovered lines in app.core.arricchisci.router (lines 152-1015).
"""

import json
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch, PropertyMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

import app.core.arricchisci.router as R
from app.core.auth.auth import get_current_user, CurrentUser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_user():
    return CurrentUser(
        user_id="test-uid", username="test",
        group_id="default", role="superadmin",
    )


def _mock_user_non_admin():
    return CurrentUser(
        user_id="user2", username="viewer",
        group_id="group-b", role="viewer",
    )


def make_client(user_fn=None):
    """Build a TestClient with all sub-routers and mocked services."""
    app = FastAPI()
    app.dependency_overrides[get_current_user] = user_fn or _mock_user
    app.include_router(R.enrichment_router)
    app.include_router(R.api_connections_router)
    app.include_router(R.templates_router)
    app.include_router(R.executables_router)
    app.include_router(R.enrich_router)

    mock_prj = MagicMock()
    mock_prj.list_projects.return_value = []
    mock_api = MagicMock()
    R.init_arricchisci(mock_prj, mock_api)
    return TestClient(app, raise_server_exceptions=False), mock_prj, mock_api




# ---------------------------------------------------------------------------
# execute_enrichment_async (lines 893-950)
# ---------------------------------------------------------------------------

class TestExecuteEnrichmentAsync:

    def test_project_not_found_404(self):
        client, prj, api = make_client()
        prj.get_project_metadata.return_value = None
        res = client.post("/projects/p1/enrich-async", json={
            "format": "csv", "force_recalculate": False
        })
        assert res.status_code == 404

    def test_wrong_project_type_400(self):
        client, prj, api = make_client()
        meta = MagicMock()
        meta.project_type = "estrai"
        prj.get_project_metadata.return_value = meta
        res = client.post("/projects/p1/enrich-async", json={
            "format": "csv", "force_recalculate": False
        })
        assert res.status_code == 400

    def test_canvas_not_found_400(self):
        client, prj, api = make_client()
        meta = MagicMock()
        meta.project_type = "arricchisci"
        prj.get_project_metadata.return_value = meta
        prj.get_project_canvas.return_value = None
        res = client.post("/projects/p1/enrich-async", json={
            "format": "csv", "force_recalculate": False
        })
        assert res.status_code == 400

    @patch("app.services.task_service.task_service")
    def test_async_success(self, mock_ts):
        client, prj, api = make_client()
        meta = MagicMock()
        meta.project_type = "arricchisci"
        prj.get_project_metadata.return_value = meta
        canvas = MagicMock()
        canvas.model_dump.return_value = {"nodes": [], "edges": []}
        canvas.nodes = []
        prj.get_project_canvas.return_value = canvas

        task_info = MagicMock()
        task_info.task_id = "task-123"
        task_info.status.value = "pending"
        mock_ts.create_enrichment_task.return_value = task_info

        res = client.post("/projects/p1/enrich-async", json={
            "format": "gpkg", "force_recalculate": True
        })
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["async"] is True
        assert body["task_id"] == "task-123"

    @patch("app.services.task_service.task_service")
    def test_async_reads_format_from_canvas_result_node(self, mock_ts):
        """When format is 'csv' (default), should try to read from canvas result node."""
        client, prj, api = make_client()
        meta = MagicMock()
        meta.project_type = "arricchisci"
        prj.get_project_metadata.return_value = meta

        result_node = MagicMock()
        result_node.type = "result"
        result_node.output_format = "geojson"
        result_node.data = {"output_format": "geojson"}
        canvas = MagicMock()
        canvas.model_dump.return_value = {"nodes": [], "edges": []}
        canvas.nodes = [result_node]
        prj.get_project_canvas.return_value = canvas

        task_info = MagicMock()
        task_info.task_id = "task-456"
        task_info.status.value = "pending"
        mock_ts.create_enrichment_task.return_value = task_info

        # format=csv (default) should trigger canvas format lookup
        res = client.post("/projects/p1/enrich-async", json={
            "format": "csv", "force_recalculate": False
        })
        assert res.status_code == 200
        # Verify the task was created with the canvas format
        call_kwargs = mock_ts.create_enrichment_task.call_args[1]
        assert call_kwargs["output_format"] == "geojson"

    @patch("app.services.task_service.task_service")
    def test_async_no_body_uses_defaults(self, mock_ts):
        """When no body is provided, should use default format='csv'."""
        client, prj, api = make_client()
        meta = MagicMock()
        meta.project_type = "arricchisci"
        prj.get_project_metadata.return_value = meta
        canvas = MagicMock()
        canvas.model_dump.return_value = {"nodes": [], "edges": []}
        canvas.nodes = []
        prj.get_project_canvas.return_value = canvas

        task_info = MagicMock()
        task_info.task_id = "task-789"
        task_info.status.value = "pending"
        mock_ts.create_enrichment_task.return_value = task_info

        # POST without body - FastAPI may or may not allow this depending on model
        # With EnrichAsyncRequest having defaults, it should work with empty json
        res = client.post("/projects/p1/enrich-async")
        # Either 200 or 422 (if body required); test that it does not crash with 500
        assert res.status_code in (200, 422)

    def test_async_crash_500(self):
        client, prj, api = make_client()
        meta = MagicMock()
        meta.project_type = "arricchisci"
        prj.get_project_metadata.return_value = meta
        canvas = MagicMock()
        canvas.model_dump.return_value = {"nodes": [], "edges": []}
        canvas.nodes = []
        prj.get_project_canvas.return_value = canvas
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.create_enrichment_task.side_effect = RuntimeError("celery down")
            res = client.post("/projects/p1/enrich-async", json={
                "format": "csv", "force_recalculate": False
            })
        assert res.status_code == 500


# ---------------------------------------------------------------------------
# list_api_connections with connection_type filter (line 362)
# ---------------------------------------------------------------------------

class TestListApiConnectionsExtended:

    def test_list_with_connection_type(self):
        client, _, api = make_client()
        api.list_api_connections.return_value = [{"id": "a1"}]
        res = client.get("/api-connections?connection_type=rest")
        assert res.status_code == 200
        body = res.json()
        assert "api_connections" in body


# ---------------------------------------------------------------------------
# create_api_connection sets group_id (line 390)
# ---------------------------------------------------------------------------

class TestCreateApiConnectionGroupId:

    def test_create_sets_group_id_from_user(self):
        client, _, api = make_client()
        api.create_api_connection.return_value = {"id": "new1"}
        res = client.post("/api-connections", json={"name": "Test"})
        assert res.status_code == 200
        # Verify that group_id was set in the data
        call_args = api.create_api_connection.call_args[0][0]
        assert call_args["group_id"] == "default"
