"""
Tests the http cross tenant behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.auth.auth import require_not_esecutore
from app.core.orchestrator.router import tasks_router
from app.shared.execution.canvas_graph import CanvasGraph
from tests2.helpers.builders import build_current_user


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.security, pytest.mark.regression]


def _build_client():
    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: build_current_user()
    app.include_router(tasks_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_create_task_rejects_cross_tenant_project_before_task_creation():
    client = _build_client()

    with patch("app.shared.authz_helpers.assert_project_access", side_effect=HTTPException(status_code=403, detail="Accesso negato")), \
         patch("app.services.task_service.task_service.create_enrichment_task") as create_task:
        response = client.post(
            "/api/tasks",
            json={
                "task_type": "enrichment",
                "project_id": "proj-foreign",
                "canvas": {"nodes": [], "edges": []},
            },
        )

    assert response.status_code == 403
    create_task.assert_not_called()


def test_create_task_parses_canvas_graph_once_before_resource_authz():
    client = _build_client()
    canvas = {
        "nodes": [
            {
                "id": "file-1",
                "type": "file_source",
                "data": {"file_source_id": "fs-1"},
            }
        ],
        "edges": [],
    }
    from_canvas_calls = {"count": 0}
    original_from_canvas = CanvasGraph.from_canvas

    def _from_canvas_once(raw_canvas):
        from_canvas_calls["count"] += 1
        return original_from_canvas(raw_canvas)

    task_info = SimpleNamespace(
        to_dict=lambda: {
            "task_id": "task-1",
            "task_type": "enrichment",
            "project_id": "proj-1",
            "status": "pending",
            "progress": 0,
            "message": "queued",
        }
    )

    with patch("app.shared.authz_helpers.assert_project_access"), \
         patch("app.shared.authz_helpers.assert_file_source_access") as assert_file_source_access, \
         patch("app.core.orchestrator.router.CanvasGraph.from_canvas", side_effect=_from_canvas_once), \
         patch("app.services.task_service.task_service.create_enrichment_task", return_value=task_info) as create_task:
        response = client.post(
            "/api/tasks",
            json={
                "task_type": "enrichment",
                "project_id": "proj-1",
                "canvas": canvas,
            },
        )

    assert response.status_code == 200
    assert from_canvas_calls["count"] == 1
    assert_file_source_access.assert_called_once()
    assert_file_source_access.assert_called_once_with("fs-1", build_current_user())
    assert create_task.call_args.kwargs["canvas"] == canvas
