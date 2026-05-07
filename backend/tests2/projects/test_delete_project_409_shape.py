"""
Contract test per shape 409 canonica su DELETE /api/projects/{id}.

Invariante blindato (Batch Fix 6.1 — error-handling-governance):
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.auth.auth import require_project_owner
from app.core.projects import router as projects_router_module
from app.core.projects.router import router as projects_router
from app.shared.exceptions import ProjectHasDependenciesError


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
    # Sostituisce il _project_service reale con un mock che solleva l'eccezione.
    mock_service = MagicMock()
    monkeypatch.setattr(
        projects_router_module, "_project_service", mock_service,
    )
    app = FastAPI()
    app.dependency_overrides[require_project_owner] = lambda: current_user
    app.include_router(projects_router, prefix="/api")
    app.state.mock_service = mock_service
    return TestClient(app, raise_server_exceptions=False)


def test_delete_project_409_shape_has_canonical_dependencies_field(client):
    mock_service = client.app.state.mock_service
    mock_service.delete_project.side_effect = ProjectHasDependenciesError(
        project_id="proj-1",
        eseguibili=[{"id": "ese-1", "name": "Eseguibile Alpha"}],
        dependent_projects=[
            {"id": "proj-2", "name": "Progetto Beta", "project_type": "manipola"},
        ],
    )

    response = client.delete("/api/projects/proj-1")

    assert response.status_code == 409
    detail = response.json()["detail"]

    # Campi canonici presenti.
    assert detail["error"] == "project_has_dependencies"
    assert isinstance(detail["message"], str) and detail["message"]
    assert isinstance(detail["dependencies"], list)

    # Shape dependency: kind + id + name, project_type opzionale.
    kinds = [d["kind"] for d in detail["dependencies"]]
    assert kinds == ["eseguibile", "project"]

    ese = detail["dependencies"][0]
    assert ese == {"kind": "eseguibile", "id": "ese-1", "name": "Eseguibile Alpha"}

    proj = detail["dependencies"][1]
    assert proj["kind"] == "project"
    assert proj["id"] == "proj-2"
    assert proj["name"] == "Progetto Beta"
    assert proj["project_type"] == "manipola"

    # Campi legacy NON presenti: test anti-reintroduzione.
    assert "eseguibili" not in detail
    assert "dependent_projects" not in detail
    assert "projects" not in detail
    assert "in_use_by" not in detail


def test_delete_project_happy_path_returns_204(client):
    """F2-B2 (2026-04-24): DELETE /api/projects/{id} risponde 204 No
    Content con body vuoto. Pre-fix ritornava 200 + dict
    {"status": "deleted", "project_id": X}."""
    mock_service = client.app.state.mock_service
    mock_service.delete_project.return_value = None

    response = client.delete("/api/projects/proj-ok")

    assert response.status_code == 204
    assert response.content == b""
