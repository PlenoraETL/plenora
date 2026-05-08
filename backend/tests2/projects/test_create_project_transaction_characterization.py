"""
Regression coverage for the project create transaction boundary.

POST /projects creates the project row before saving the optional submitted
canvas. If the canvas save raises unexpectedly, the endpoint must return the
same 500 shape and compensate the newly-created project row.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.auth.dependencies import require_user_or_above
from app.core.projects import router as projects_router_module
from app.core.projects.router import router as projects_router
from app.shared.contracts.auth import CurrentUser


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


@pytest.fixture
def client(current_user: CurrentUser, monkeypatch) -> TestClient:
    mock_service = MagicMock()
    metadata = MagicMock()
    metadata.id = "proj-created"
    metadata.model_dump.return_value = {
        "id": "proj-created",
        "name": "Created",
        "group_id": "grp-1",
    }
    mock_service.create_project.return_value = metadata
    mock_service.update_canvas.side_effect = RuntimeError("canvas write crashed")
    monkeypatch.setattr(projects_router_module, "_project_service", mock_service)

    app = FastAPI()
    app.dependency_overrides[require_user_or_above] = lambda: current_user
    app.include_router(projects_router, prefix="/api")

    async def _fake_global_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"detail": "Errore interno del server"},
        )

    app.add_exception_handler(Exception, _fake_global_handler)
    app.state.mock_service = mock_service
    return TestClient(app, raise_server_exceptions=False)


def test_create_project_canvas_crash_rolls_back_created_project(client):
    response = client.post(
        "/api/projects",
        json={
            "name": "Created",
            "project_type": "manipola",
            "canvas": {
                "nodes": [],
                "edges": [],
            },
        },
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Errore salvataggio canvas"}
    mock_service = client.app.state.mock_service
    mock_service.create_project.assert_called_once()
    mock_service.update_canvas.assert_called_once()
    mock_service.rollback_created_project.assert_called_once_with(
        "proj-created",
        group_id="grp-1",
    )
