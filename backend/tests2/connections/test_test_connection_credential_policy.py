"""Contract test Bug #1 (2026-04-23): test_connection password None/corrotta → 422.

Blinda il contratto: l'endpoint POST /api/connections/{id}/test non deve
andare in 500 ASGI se la password nel DB e' NULL o non decriptabile. Deve
rispondere HTTP 422 Unprocessable Entity con detail esplicito.

Regression risk: in passato _row_to_connection(mask_password=False)
passava password=None a DatabaseConnection (str) → Pydantic ValidationError
→ 500. Il fix (2026-04-23) raise CredentialUnavailableError che il router
traduce in 422.
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
from app.shared.crypto import CredentialUnavailableError


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
def client_with_service(current_user: CurrentUser, monkeypatch) -> tuple:
    mock_service = MagicMock()
    monkeypatch.setattr(
        connections_router_module, "_connection_service", mock_service,
    )
    monkeypatch.setattr(
        connections_router_module, "assert_resource_visible", lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        connections_router_module, "assert_can_modify", lambda *a, **kw: None,
    )

    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: current_user
    app.dependency_overrides[require_not_esecutore] = lambda: current_user
    app.include_router(connections_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False), mock_service


def test_test_connection_password_null_returns_422(client_with_service):
    """Bug #1: password=NULL nel DB → CredentialUnavailableError → 422."""
    client, mock_service = client_with_service
    mock_service.get_connection.side_effect = CredentialUnavailableError(
        "connection 'conn-1': password non configurata"
    )

    response = client.post("/api/connections/conn-1/test")

    assert response.status_code == 422
    assert "password non configurata" in response.json()["detail"]


def test_test_connection_decrypt_failure_returns_422(client_with_service):
    """Bug #1: password cifrata ma decrypt ritorna None → 422."""
    client, mock_service = client_with_service
    mock_service.get_connection.side_effect = CredentialUnavailableError(
        "connection 'conn-1': password non decriptabile "
        "(chiave mancante/sbagliata o token corrotto)"
    )

    response = client.post("/api/connections/conn-1/test")

    assert response.status_code == 422
    assert "non decriptabile" in response.json()["detail"]


def test_put_connection_credential_error_returns_422(client_with_service):
    """Bug #1: PUT sullo stesso record rotto → 422 consistente."""
    client, mock_service = client_with_service
    mock_service.get_connection.side_effect = CredentialUnavailableError(
        "connection 'conn-1': password non decriptabile"
    )

    response = client.put(
        "/api/connections/conn-1",
        json={"name": "updated", "type": "postgres", "host": "localhost",
              "port": 5432, "database": "test", "username": "u", "password": "p"},
    )

    assert response.status_code == 422
    assert "non decriptabile" in response.json()["detail"]
