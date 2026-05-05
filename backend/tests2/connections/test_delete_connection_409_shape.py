"""Contract test per shape 409 canonica su DELETE /api/connections/{id}.

Invariante blindato (Batch Fix 6.1 — error-handling-governance):
    Quando una connessione e' referenziata da uno o piu' progetti, la
    delete ritorna 409 con shape canonica:
        {
            "detail": {
                "error": "connection_in_use",
                "message": <str>,
                "dependencies": [
                    {"kind": "project", "id": str, "name": str,
                     "project_type": str}
                ]
            }
        }

Reperto pre-fix (API audit §3.3): il campo lista era ``projects`` con
shape flat. Post 6.1 e' ``dependencies`` con ``kind`` per allineare a
projects/datasources/api-connections.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.auth.auth import require_admin, require_not_esecutore
from app.core.connections import router as connections_router_module
from app.core.connections.router import router as connections_router


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
    # Simula connection esistente (any non-None basta per passare assert_can_modify mockato).
    mock_service.get_connection.return_value = {
        "id": "conn-1",
        "group_id": "grp-1",
        "created_by": "u-1",
    }
    monkeypatch.setattr(
        connections_router_module, "_connection_service", mock_service,
    )
    # Stub l'auth helper che altrimenti rifiuterebbe per mancanza di role.
    monkeypatch.setattr(
        connections_router_module, "assert_can_modify", lambda *a, **kw: None,
    )

    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: current_user
    app.dependency_overrides[require_not_esecutore] = lambda: current_user
    app.include_router(connections_router, prefix="/api")
    app.state.mock_service = mock_service
    return TestClient(app, raise_server_exceptions=False)


def test_delete_connection_409_shape_has_canonical_dependencies_field(client, monkeypatch):
    # Il router fa db.fetchall(...) — monkeypatch per simulare la connessione in uso.
    import app.db.db_service as db_service
    monkeypatch.setattr(
        db_service,
        "fetchall",
        # PR-δ: la SELECT include `group_id` per consentire filtro
        # cross-tenant in fase di costruzione del 409 detail. Per non
        # rompere lo shape test, entrambi i progetti appartengono al
        # gruppo di current_user (grp-1) → tutti visibili, hidden=0.
        lambda *args, **kwargs: [
            {"id": "proj-1", "name": "Progetto A", "project_type": "estrai",  "group_id": "grp-1"},
            {"id": "proj-2", "name": "Progetto B", "project_type": "manipola", "group_id": "grp-1"},
        ],
    )

    response = client.delete("/api/connections/conn-1")

    assert response.status_code == 409
    detail = response.json()["detail"]

    assert detail["error"] == "connection_in_use"
    assert isinstance(detail["message"], str) and detail["message"]
    assert isinstance(detail["dependencies"], list)
    assert len(detail["dependencies"]) == 2

    for d in detail["dependencies"]:
        assert d["kind"] == "project"
        assert "id" in d and "name" in d and "project_type" in d

    # Campi legacy NON presenti.
    assert "projects" not in detail
    assert "in_use_by" not in detail


def test_delete_connection_happy_path_returns_204(client, monkeypatch):
    """F2-B1 (2026-04-24): DELETE /api/connections/{id} risponde 204 No
    Content con body vuoto. Pre-fix ritornava 200 + dict
    {"status": "deleted", "connection_id": X} (ridondante — ID gia' noto
    al client che ha chiamato DELETE)."""
    import app.db.db_service as db_service
    monkeypatch.setattr(db_service, "fetchall", lambda *a, **kw: [])
    client.app.state.mock_service.delete_connection.return_value = None

    response = client.delete("/api/connections/conn-1")

    assert response.status_code == 204
    assert response.content == b""
