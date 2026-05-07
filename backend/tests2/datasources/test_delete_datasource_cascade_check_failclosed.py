"""
Contract test per fail-closed sul pre-check cascade di

DELETE /api/datasources/{id}.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.auth.auth import require_admin, require_not_esecutore
from app.core.datasources import router as ds_router_module
from app.core.datasources.router import router as ds_router


pytestmark = [
    pytest.mark.unit,
    pytest.mark.contract,
    pytest.mark.regression,
    pytest.mark.security,
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
    mock_service = MagicMock()
    mock_service.get_datasource.return_value = {
        "id": "ds-1",
        "group_id": "grp-1",
        "created_by": "u-1",
    }
    mock_service.delete_datasource.return_value = True
    monkeypatch.setattr(ds_router_module, "_svc", lambda: mock_service)
    monkeypatch.setattr(
        ds_router_module, "assert_can_modify", lambda *a, **kw: None,
    )

    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: current_user
    app.dependency_overrides[require_not_esecutore] = lambda: current_user
    app.include_router(ds_router, prefix="/api")
    app.state.mock_service = mock_service
    return TestClient(app, raise_server_exceptions=False)


def test_delete_datasource_all_canvases_broken_returns_500(client, monkeypatch):
    """Se TUTTE le rows hanno canvas non parsabile (JSON malformato),
    _find_projects_using_datasource solleva RuntimeError e delete_datasource
    traduce in 500. La delete non procede."""
    import app.db.pg_connection as pg_connection
    # 2 rows con JSON invalido: _json.loads fallisce per entrambe.
    monkeypatch.setattr(
        pg_connection,
        "fetchall",
        lambda *args, **kwargs: [
            {"id": "proj-a", "name": "A", "project_type": "estrai", "canvas": "{not json"},
            {"id": "proj-b", "name": "B", "project_type": "manipola", "canvas": "[[broken"},
        ],
    )

    response = client.delete("/api/datasources/ds-1")

    assert response.status_code == 500
    assert response.json()["detail"] == "Impossibile verificare dipendenze: riprova piu' tardi"
    # delete_datasource del service NON deve essere chiamato: fail-closed stretto.
    client.app.state.mock_service.delete_datasource.assert_not_called()


def test_delete_datasource_partial_canvas_failure_proceeds(client, monkeypatch):
    """Una row con canvas corrotto + una row con canvas valido senza match:
    partial failure resta fail-open, la delete procede come happy path."""
    import app.db.pg_connection as pg_connection
    good_canvas = '{"nodes": [{"datasource_id": "other-ds", "id": "n-1"}]}'
    monkeypatch.setattr(
        pg_connection,
        "fetchall",
        lambda *args, **kwargs: [
            {"id": "proj-broken", "name": "Broken", "project_type": "estrai", "canvas": "{corrupt"},
            {"id": "proj-ok", "name": "OK", "project_type": "manipola", "canvas": good_canvas},
        ],
    )

    response = client.delete("/api/datasources/ds-1")

    # Happy path: almeno una row checkata (proj-ok), nessun match, delete OK.
    # delete_datasource ha status_code=204 dichiarato sul decorator.
    assert response.status_code == 204
    client.app.state.mock_service.delete_datasource.assert_called_once_with("ds-1")
