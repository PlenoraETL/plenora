"""
Contract test F7-B1 (2026-04-24) — list_projects marker fail-open per

canvas corrotto.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.auth.auth import require_not_esecutore
from app.core.projects import router as projects_router_module
from app.core.projects.router import router as projects_router
from app.core.projects.exceptions import CanvasCorruptedError


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


def _make_project(project_id: str, name: str = "demo",
                  project_type: str = "manipola") -> SimpleNamespace:
    """Stand-in di un ProjectSummary/Metadata Pydantic-like.

    Il router chiama `project.model_dump()` + `project.project_type` +
    `project.id`: SimpleNamespace + model_dump stubbed copre il contract.
    """
    base = {
        "id": project_id,
        "name": name,
        "project_type": project_type,
        "group_id": "grp-1",
    }
    ns = SimpleNamespace(**base)
    ns.model_dump = lambda: dict(base)
    return ns


@pytest.fixture
def client(current_user: CurrentUser, monkeypatch) -> TestClient:
    mock_service = MagicMock()
    monkeypatch.setattr(
        projects_router_module, "_project_service", mock_service,
    )
    # group_filter e' invocato direttamente; lo monkeypatchiamo per
    # evitare la dipendenza da role/group logic.
    monkeypatch.setattr(
        projects_router_module, "group_filter", lambda cu: None,
    )
    monkeypatch.setattr(
        projects_router_module,
        "list_projects_with_canvas_for_datasource_check",
        lambda _project_ids: [],
    )

    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: current_user
    app.include_router(projects_router, prefix="/api")
    app.state.mock_service = mock_service
    return TestClient(app, raise_server_exceptions=False)


def test_list_projects_includes_canvas_corrupted_marker_when_canvas_raises(client):
    """F7-B1: progetto manipola con `CanvasCorruptedError` nella list →
    response contiene `canvas_corrupted=True` + `canvas_corrupted_reason`."""
    mock_service = client.app.state.mock_service
    projects = [_make_project("p-corrupt", name="Rotto", project_type="manipola")]
    mock_service.list_projects.return_value = SimpleNamespace(projects=projects)

    mock_service.get_project_canvas.side_effect = CanvasCorruptedError(
        project_id="p-corrupt",
        reason="JSON non parsabile",
    )

    response = client.get("/api/projects/")
    assert response.status_code == 200

    body = response.json()
    assert "projects" in body
    proj_dicts = body["projects"]
    assert len(proj_dicts) == 1
    p = proj_dicts[0]

    assert p["id"] == "p-corrupt"
    assert p["canvas_corrupted"] is True
    assert p["canvas_corrupted_reason"] == "JSON non parsabile"
    assert p["source_project_ids"] == []
    assert p["file_source_ids"] == []


def test_list_projects_healthy_projects_have_no_canvas_corrupted_marker(client):
    """Sparse contract: sui progetti con canvas valido, le chiavi
    `canvas_corrupted` e `canvas_corrupted_reason` NON devono comparire
    (non serializzate come False/null). Il FE usa presenza-chiave per
    discriminare."""
    mock_service = client.app.state.mock_service
    projects = [_make_project("p-ok", name="Sano", project_type="manipola")]
    mock_service.list_projects.return_value = SimpleNamespace(projects=projects)

    # Canvas healthy: oggetto con `.nodes` vuoto (niente project_source / file_source),
    # il router entra nel branch `else` e setta source_ids=[] / file_source_ids=[]
    # SENZA il marker.
    healthy_canvas = SimpleNamespace(nodes=[])
    mock_service.get_project_canvas.return_value = healthy_canvas

    response = client.get("/api/projects/")
    assert response.status_code == 200

    p = response.json()["projects"][0]
    assert "canvas_corrupted" not in p, (
        f"sparse violata: marker canvas_corrupted presente su progetto "
        f"healthy: {p!r}"
    )
    assert "canvas_corrupted_reason" not in p, (
        f"sparse violata: reason presente su progetto healthy: {p!r}"
    )


def test_list_projects_canvas_corrupted_reason_does_not_leak_original_error(client):
    """Anti-leak: anche se CanvasCorruptedError ha `original_error` con
    dettaglio tecnico sensibile, il marker espone SOLO `exc.reason` (che
    e' safe-by-construction). `str(original_error)` non deve mai
    comparire nella response."""
    mock_service = client.app.state.mock_service
    projects = [_make_project("p-corrupt", project_type="manipola")]
    mock_service.list_projects.return_value = SimpleNamespace(projects=projects)

    mock_service.get_project_canvas.side_effect = CanvasCorruptedError(
        project_id="p-corrupt",
        reason="Schema Pydantic non valido",
        original_error=RuntimeError("INTERNAL_LEAK_TOKEN: trace line 42"),
    )

    response = client.get("/api/projects/")
    assert response.status_code == 200

    body_text = response.text
    assert "INTERNAL_LEAK_TOKEN" not in body_text, (
        f"leak regression: original_error leakato in list_projects response: "
        f"{body_text!r}"
    )
    assert "trace line 42" not in body_text

    p = response.json()["projects"][0]
    assert p["canvas_corrupted_reason"] == "Schema Pydantic non valido"
