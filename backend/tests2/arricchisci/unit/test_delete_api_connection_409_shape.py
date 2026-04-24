"""Contract test per shape 409 canonica su DELETE /api/api-connections/{id}.

Invariante blindato (Batch Fix 6.1 — error-handling-governance):
    Quando una API connection e' referenziata nel canvas di uno o piu'
    progetti arricchisci, la delete ritorna 409 con shape canonica:
        {
            "detail": {
                "error": "api_connection_in_use",
                "message": <str>,
                "dependencies": [
                    {"kind": "project", "id": str, "name": str}
                ]
            }
        }

Reperto pre-fix (API audit §3.3): la shape era {message, in_use_by:
[{project_id, project_name}]} SENZA chiave ``error`` e con nomi campo
divergenti (``project_id`` invece di ``id``). Il FE non poteva scrivere
un handler uniforme. Post 6.1 allineata alle altre 3 cascade-delete.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.auth import CurrentUser, require_admin
from app.core.arricchisci import router as arric_router_module
from app.core.arricchisci.router import api_connections_router


pytestmark = [
    pytest.mark.unit,
    pytest.mark.contract,
    pytest.mark.arricchisci,
    pytest.mark.regression,
]


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
    mock_project_service = MagicMock()
    mock_api_service = MagicMock()

    # Simula un progetto arricchisci il cui canvas referenzia api-1.
    fake_canvas = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                model_dump=lambda: {
                    "id": "node-x",
                    "data": {"api_id": "api-1"},
                }
            )
        ]
    )
    fake_project_list = SimpleNamespace(
        projects=[
            SimpleNamespace(
                model_dump=lambda: {
                    "project_id": "proj-arr-1",
                    "name": "Arricchisci Alpha",
                    "project_type": "arricchisci",
                }
            )
        ]
    )
    mock_project_service.list_projects.return_value = fake_project_list
    mock_project_service.get_project_canvas.return_value = fake_canvas
    mock_api_service.delete_api_connection.return_value = True

    monkeypatch.setattr(arric_router_module, "_project_service", mock_project_service)
    monkeypatch.setattr(
        arric_router_module, "_api_connection_service", mock_api_service,
    )
    # Stub il check ownership — fa query DB, bypass nel test.
    monkeypatch.setattr(
        arric_router_module,
        "_check_api_connection_ownership",
        lambda api_id, current_user: None,
    )

    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: current_user
    app.include_router(api_connections_router, prefix="/api")
    app.state.mock_project_service = mock_project_service
    app.state.mock_api_service = mock_api_service
    return TestClient(app, raise_server_exceptions=False)


def test_delete_api_connection_409_shape_has_canonical_dependencies_field(client):
    response = client.delete("/api/api-connections/api-1")

    assert response.status_code == 409
    detail = response.json()["detail"]

    # Pre-fix NON aveva ``error``. Post 6.1 lo espone sempre.
    assert detail["error"] == "api_connection_in_use"
    assert isinstance(detail["message"], str) and detail["message"]
    assert isinstance(detail["dependencies"], list)
    assert len(detail["dependencies"]) == 1

    dep = detail["dependencies"][0]
    assert dep["kind"] == "project"
    assert dep["id"] == "proj-arr-1"
    assert dep["name"] == "Arricchisci Alpha"
    # F1-B1 (2026-04-24): project_type presente nella shape cascade,
    # uniformata con connection/datasource 409.
    assert dep["project_type"] == "arricchisci"
    assert isinstance(dep["project_type"], str)

    # Campi legacy NON presenti.
    assert "in_use_by" not in detail


def test_delete_api_connection_happy_path_returns_204(client, monkeypatch):
    """F2-B1 (2026-04-24): DELETE /api/api-connections/{id} risponde 204
    No Content con body vuoto. Pre-fix ritornava 200 + dict
    {"status": "deleted", "api_id": X}."""
    # Canvas senza match → niente dipendenze, happy path.
    empty_list = SimpleNamespace(projects=[])
    client.app.state.mock_project_service.list_projects.return_value = empty_list

    response = client.delete("/api/api-connections/api-not-used")

    assert response.status_code == 204
    assert response.content == b""
