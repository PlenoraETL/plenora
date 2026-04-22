"""Contract test per shape 409 canonica su DELETE /api/datasources/{id}.

Invariante blindato (Batch Fix 6.1 — error-handling-governance):
    Quando un Data Source e' referenziato nel canvas di uno o piu'
    progetti, la delete ritorna 409 con shape canonica:
        {
            "detail": {
                "error": "datasource_in_use",
                "message": <str>,
                "dependencies": [
                    {"kind": "project", "id": str, "name": str,
                     "project_type": str}
                ]
            }
        }

Reperto pre-fix (API audit §3.3): il campo lista era ``projects`` con
shape flat. Post 6.1 e' ``dependencies`` con ``kind``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.auth import CurrentUser, require_admin, require_not_esecutore
from app.core.datasources import router as ds_router_module
from app.core.datasources.router import router as ds_router


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
    mock_service.get_datasource.return_value = {
        "id": "ds-1",
        "group_id": "grp-1",
        "created_by": "u-1",
    }
    # Patch _svc() helper che il router usa internamente.
    monkeypatch.setattr(ds_router_module, "_svc", lambda: mock_service)
    monkeypatch.setattr(
        ds_router_module, "assert_can_modify", lambda *a, **kw: None,
    )

    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: current_user
    app.dependency_overrides[require_not_esecutore] = lambda: current_user
    # Il router ha gia' prefix="/datasources" interno; qui montiamo solo /api.
    app.include_router(ds_router, prefix="/api")
    app.state.mock_service = mock_service
    return TestClient(app, raise_server_exceptions=False)


def test_delete_datasource_409_shape_has_canonical_dependencies_field(client, monkeypatch):
    # Patch _find_projects_using_datasource helper per simulare uso.
    monkeypatch.setattr(
        ds_router_module,
        "_find_projects_using_datasource",
        lambda ds_id: [
            {"id": "proj-1", "name": "Canvas A", "project_type": "manipola"},
            {"id": "proj-2", "name": "Canvas B", "project_type": "arricchisci"},
        ],
    )

    response = client.delete("/api/datasources/ds-1")

    assert response.status_code == 409
    detail = response.json()["detail"]

    assert detail["error"] == "datasource_in_use"
    assert isinstance(detail["message"], str) and detail["message"]
    assert isinstance(detail["dependencies"], list)
    assert len(detail["dependencies"]) == 2

    for d in detail["dependencies"]:
        assert d["kind"] == "project"
        assert "id" in d and "name" in d and "project_type" in d

    # Campi legacy NON presenti.
    assert "projects" not in detail
    assert "in_use_by" not in detail


def test_delete_datasource_happy_path_returns_204(client, monkeypatch):
    monkeypatch.setattr(
        ds_router_module, "_find_projects_using_datasource", lambda ds_id: [],
    )
    client.app.state.mock_service.delete_datasource.return_value = True

    response = client.delete("/api/datasources/ds-1")

    # delete_datasource ha status_code=204 dichiarato sul decorator.
    assert response.status_code == 204
