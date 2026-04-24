"""Contract test per fail-closed sul pre-check cascade di
DELETE /api/api-connections/{id}.

Invariante blindato (Batch Fix 6.3 — error-handling-governance):
    Il pre-check cerca le dipendenze sui canvas dei progetti arricchisci.
    Distinzione chiave tra due classi di failure:

    - per-consumer failure (canvas corrotto di UN singolo progetto) →
      fail-open legittimo: log warning + continue. La delete procede
      perche' altri progetti sono stati verificabili.

    - global failure (list_projects() solleva, o il loop esterno crasha)
      → fail-closed obbligatorio: HTTPException(500). Impossibile dire
      se il datasource e' usato o no; non possiamo silent-proceed o
      violeremmo l'invariant "risorsa in uso non cancellabile".

Reperto pre-fix (audit §4.1 S-FAILOPEN-CASCADE): il try esterno faceva
``logger.warning + prosegue`` anche su global failure. Un glitch DB
poteva portare a cancellazione di api_connection referenziate.
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
from app.shared.exceptions import CanvasCorruptedError


pytestmark = [
    pytest.mark.unit,
    pytest.mark.contract,
    pytest.mark.arricchisci,
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


def _build_client(current_user: CurrentUser, monkeypatch, mock_project_service, mock_api_service) -> TestClient:
    monkeypatch.setattr(arric_router_module, "_project_service", mock_project_service)
    monkeypatch.setattr(arric_router_module, "_api_connection_service", mock_api_service)
    monkeypatch.setattr(
        arric_router_module,
        "_check_api_connection_ownership",
        lambda api_id, current_user: None,
    )
    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: current_user
    app.include_router(api_connections_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_delete_api_connection_check_global_failure_returns_500(current_user, monkeypatch):
    """Se list_projects() solleva globalmente, la delete NON procede: 500."""
    mock_project_service = MagicMock()
    mock_project_service.list_projects.side_effect = RuntimeError("DB pool exhausted")

    mock_api_service = MagicMock()
    mock_api_service.delete_api_connection.return_value = True

    client = _build_client(current_user, monkeypatch, mock_project_service, mock_api_service)

    response = client.delete("/api/api-connections/api-1")

    assert response.status_code == 500
    assert response.json()["detail"] == "Impossibile verificare dipendenze: riprova piu' tardi"
    # La delete del service NON deve essere chiamata: fail-closed stretto.
    mock_api_service.delete_api_connection.assert_not_called()


def test_delete_api_connection_per_consumer_canvas_error_proceeds(current_user, monkeypatch):
    """Un singolo canvas corrotto non blocca la delete (fail-open per-consumer
    preservato). Solo il global failure attiva fail-closed."""
    mock_project_service = MagicMock()

    # Lista 2 progetti arricchisci.
    proj_a = SimpleNamespace(
        model_dump=lambda: {
            "project_id": "proj-a",
            "name": "Arricchisci A",
            "project_type": "arricchisci",
        }
    )
    proj_b = SimpleNamespace(
        model_dump=lambda: {
            "project_id": "proj-b",
            "name": "Arricchisci B",
            "project_type": "arricchisci",
        }
    )
    mock_project_service.list_projects.return_value = SimpleNamespace(
        projects=[proj_a, proj_b],
    )

    # proj-a: canvas corrotto. proj-b: canvas valido senza api_id match.
    clean_canvas = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                model_dump=lambda: {"id": "n-1", "data": {"api_id": "different-api-id"}}
            )
        ]
    )

    def _get_canvas(proj_id):
        if proj_id == "proj-a":
            raise CanvasCorruptedError(project_id=proj_id, reason="JSON malformato")
        return clean_canvas

    mock_project_service.get_project_canvas.side_effect = _get_canvas

    mock_api_service = MagicMock()
    mock_api_service.delete_api_connection.return_value = True

    client = _build_client(current_user, monkeypatch, mock_project_service, mock_api_service)

    response = client.delete("/api/api-connections/api-1")

    # Delete procede: un canvas checkato (proj-b) non usa l'api_id.
    # F2-B1 (2026-04-24): DELETE risponde 204 No Content, body vuoto.
    assert response.status_code == 204
    assert response.content == b""
    mock_api_service.delete_api_connection.assert_called_once()
