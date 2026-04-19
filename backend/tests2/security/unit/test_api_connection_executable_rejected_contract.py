"""Difesa in profondità: `connection_type='executable'` deve essere
rifiutato dal router al create e all'update.

Contesto
--------
Il supporto per `connection_type == 'executable'` (branch python_module +
subprocess, eseguito dal worker durante l'enrichment) è stato rimosso
ad aprile 2026 per chiudere una superficie RCE: un admin applicativo
poteva salvare una connection con `executable.module = 'subprocess'` +
`function = 'run'` e qualunque editor dello stesso tenant poteva poi
eseguire comandi arbitrari sul worker OS tramite `exec_params`,
leggendo `/backend/.env` e propagando cross-tenant.

Questo test vincola i due endpoint POST/PUT `/api/api-connections` a
restituire 400 se qualcuno (o un record legacy riciclato) prova ancora
a passare `connection_type='executable'`, in modo che la rimozione non
possa essere silently reintrodotta da un refactor futuro.
"""

from unittest.mock import MagicMock, patch

import pytest

from tests2.helpers.builders import build_current_user


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def client():
    """TestClient con require_admin by-passato per concentrarsi sul reject."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.arricchisci.router import api_connections_router
    from app.core.auth.auth import require_admin

    app = FastAPI()
    app.include_router(api_connections_router, prefix="/api")
    app.dependency_overrides[require_admin] = lambda: build_current_user(
        user_id="admin-1", group_id="grp-1", role="admin"
    )
    return TestClient(app)


def test_create_api_connection_rejects_executable_with_400(client):
    response = client.post(
        "/api/api-connections",
        json={
            "name": "malicious-subprocess",
            "connection_type": "executable",
            "executable": {"module": "subprocess", "function": "run"},
        },
    )

    assert response.status_code == 400
    assert "executable" in response.json()["detail"].lower()


def test_create_api_connection_allows_rest_file(client):
    """rest_file è il tipo file-processor supportato: deve restare accettato.
    Mockiamo il service per evitare side-effect DB."""
    with patch(
        "app.core.arricchisci.router._api_connection_service",
        MagicMock(create_api_connection=MagicMock(return_value={"id": "api-1"})),
    ):
        response = client.post(
            "/api/api-connections",
            json={
                "name": "ok-rest-file",
                "connection_type": "rest_file",
                "url": "https://imdf.plenora.it/api/process",
            },
        )

    # Non assertiamo un 200/201 specifico (dipende dal service mock) — ci basta
    # che NON sia il 400 della deny-list executable.
    assert response.status_code != 400


def test_update_api_connection_rejects_executable_with_400(client):
    response = client.put(
        "/api/api-connections/api-42",
        json={
            "connection_type": "executable",
            "executable": {"module": "os", "function": "system"},
        },
    )

    assert response.status_code == 400
    assert "executable" in response.json()["detail"].lower()


def test_create_reject_runs_before_service_is_called(client):
    """Garantisce che il 400 venga sollevato dal guard del router, non
    dall'exception handler generico che torna 500. Se il service fosse
    chiamato prima del guard, un refactor potrebbe accidentalmente
    aprire un path di iniezione del payload."""
    service_mock = MagicMock()
    with patch(
        "app.core.arricchisci.router._api_connection_service",
        service_mock,
    ):
        response = client.post(
            "/api/api-connections",
            json={"name": "x", "connection_type": "executable"},
        )

    assert response.status_code == 400
    service_mock.create_api_connection.assert_not_called()
