"""Contract tests for corrupted canvas raw recovery reads."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.dependencies import require_not_esecutore
from app.core.projects import router as projects_router_module
from app.core.projects.router import router as projects_router
from app.core.projects.service import ProjectService, RawProjectCanvas
from app.shared.contracts.auth import CurrentUser
from app.core.projects.exceptions import CanvasCorruptedError


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.security]


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


def _metadata(project_id: str = "p-corrupt", group_id: str = "grp-1"):
    return SimpleNamespace(id=project_id, group_id=group_id)


@pytest.fixture
def client(current_user: CurrentUser, monkeypatch) -> TestClient:
    mock_service = MagicMock()
    monkeypatch.setattr(projects_router_module, "_project_service", mock_service)

    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: current_user
    app.include_router(projects_router, prefix="/api")
    app.state.mock_service = mock_service
    return TestClient(app, raise_server_exceptions=False)


def test_service_raw_canvas_read_keeps_group_filter() -> None:
    svc = ProjectService()
    malformed_canvas = '{"nodes": ['

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def fetchone(self, sql, params):
            calls.append((sql, params))
            return {"id": "p-corrupt", "canvas": malformed_canvas}

    calls = []
    with patch("app.core.projects.service.unit_of_work", return_value=_Session()):
        raw = svc.get_project_raw_canvas("p-corrupt", group_id="grp-1")

    assert raw == RawProjectCanvas(
        project_id="p-corrupt",
        canvas=malformed_canvas,
    )
    assert calls == [
        (
            "SELECT id, canvas FROM projects WHERE id = %s AND group_id = %s",
            ("p-corrupt", "grp-1"),
        )
    ]


def test_raw_canvas_endpoint_recovers_when_strict_canvas_validation_fails(
    client: TestClient,
) -> None:
    mock_service = client.app.state.mock_service
    raw_value = '{"nodes": ['
    mock_service.get_project_metadata.return_value = _metadata()
    mock_service.get_project_canvas.side_effect = CanvasCorruptedError(
        project_id="p-corrupt",
        reason="JSON malformato",
    )
    mock_service.get_project_raw_canvas.return_value = RawProjectCanvas(
        project_id="p-corrupt",
        canvas=raw_value,
    )

    response = client.get("/api/projects/p-corrupt/canvas/raw")

    assert response.status_code == 200
    assert response.json() == {
        "project_id": "p-corrupt",
        "canvas": raw_value,
        "raw": True,
        "validated": False,
    }
    mock_service.get_project_canvas.assert_not_called()
    mock_service.get_project_raw_canvas.assert_called_once_with(
        "p-corrupt",
        group_id="grp-1",
    )


def test_raw_canvas_endpoint_blocks_cross_tenant_access(
    client: TestClient,
) -> None:
    mock_service = client.app.state.mock_service
    mock_service.get_project_metadata.return_value = _metadata(group_id="grp-2")

    response = client.get("/api/projects/p-other/canvas/raw")

    assert response.status_code == 403
    mock_service.get_project_raw_canvas.assert_not_called()
    mock_service.get_project_canvas.assert_not_called()
