"""
Contract test per narrow catch su PUT /api/projects/{id}/canvas.

Invariante blindato (— error-handling-governance):
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.auth.auth import require_user_or_above
from app.core.projects import router as projects_router_module
from app.core.projects.router import router as projects_router
from app.core.projects.lineage import ProjectLineageCycleError


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
    # Stub delle precondition del canvas update per portare il flow al
    # try/except del update_canvas senza dover costruire un canvas reale
    # (assert_group_access, compute_result_columns, ecc. sono rumore qui).
    mock_service.get_project_metadata.return_value = MagicMock(
        id="proj-1", group_id="grp-1",
    )
    monkeypatch.setattr(projects_router_module, "_project_service", mock_service)
    monkeypatch.setattr(
        projects_router_module, "assert_group_access", lambda *a, **kw: None,
    )

    app = FastAPI()
    app.dependency_overrides[require_user_or_above] = lambda: current_user
    app.include_router(projects_router, prefix="/api")

    # Replica minima del global exception handler di main.py:129 (senza la
    # logica CORS, qui irrilevante): serve per verificare che un'eccezione
    # non-tipizzata propaghi come 500 con detail generico, senza leak.
    async def _fake_global_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"detail": "Errore interno del server"},
        )

    app.add_exception_handler(Exception, _fake_global_handler)
    app.state.mock_service = mock_service
    return TestClient(app, raise_server_exceptions=False)


def _minimal_canvas_body() -> dict:
    """Body minimo valido che passa la pre-check orphans + C9 compute."""
    return {
        "nodes": [
            {"id": "src-1", "type": "source", "position": {"x": 0, "y": 0}, "data": {}},
        ],
        "edges": [],
    }


def test_update_canvas_lineage_cycle_error_returns_400(client):
    """ProjectLineageCycleError -> 400 preserva messaggio del dominio.

    La classe formatta chain in un messaggio del tipo:
      ``"Ciclo di dipendenza tra progetti: proj-a → proj-b → proj-a"``.
    Il detail HTTP deve contenere gli ID del ciclo.
    """
    client.app.state.mock_service.update_canvas.side_effect = (
        ProjectLineageCycleError(chain=["proj-a", "proj-b", "proj-a"])
    )

    response = client.put("/api/projects/proj-1/canvas", json=_minimal_canvas_body())

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "proj-a" in detail
    assert "proj-b" in detail


def test_update_canvas_unrelated_exception_does_not_leak_as_400(client):
    """Eccezioni non-tipizzate NON devono essere accidentalmente mappate a 400:
    post-6.4 il pattern isinstance e' stato rimosso, quindi un RuntimeError
    generico propaga al global handler (500). Il detail NON deve contenere
    il messaggio grezzo dell'eccezione (no leak)."""
    client.app.state.mock_service.update_canvas.side_effect = (
        RuntimeError("boom generico")
    )

    response = client.put("/api/projects/proj-1/canvas", json=_minimal_canvas_body())

    # Comportamento: il RuntimeError non tipizzato non viene catturato dal
    # narrow, quindi arriva al global exception handler FastAPI → 500.
    assert response.status_code == 500
    # Non dipendiamo dal testo esatto del global handler, ma blindiamo
    # la non-leak: il messaggio raw dell'eccezione non deve filtrarsi
    # nel detail.
    detail = response.json().get("detail", "")
    assert "boom generico" not in detail
