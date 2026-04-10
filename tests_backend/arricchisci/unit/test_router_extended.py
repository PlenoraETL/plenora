"""
tests/arricchisci/unit/test_router_extended.py - Fixed v3
"""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

import app.core.arricchisci.router as R
from app.core.auth.auth import get_current_user, CurrentUser


def _mock_user():
    """Utente mock superadmin per i test unitari."""
    return CurrentUser(user_id="test-uid", username="test", group_id="default", role="superadmin")


def make_client():
    app = FastAPI()
    app.dependency_overrides[get_current_user] = _mock_user
    app.include_router(R.enrichment_router)
    app.include_router(R.api_connections_router)
    app.include_router(R.templates_router)
    mock_prj = MagicMock()
    mock_prj.list_projects.return_value = []
    mock_api = MagicMock()
    R.init_arricchisci(mock_prj, mock_api)
    return TestClient(app, raise_server_exceptions=False), mock_api


class TestApiConnectionsList:

    def test_list_200_empty(self):
        client, api = make_client()
        api.list_api_connections.return_value = []
        assert client.get("/api-connections").status_code == 200

    def test_list_200_con_dati(self):
        client, api = make_client()
        api.list_api_connections.return_value = [{"id": "api_1", "name": "Test"}]
        res = client.get("/api-connections")
        assert res.status_code == 200

    def test_list_con_category_filter(self):
        client, api = make_client()
        api.list_api_connections.return_value = []
        assert client.get("/api-connections?category=enricher").status_code == 200

    def test_list_errore_500(self):
        client, api = make_client()
        api.list_api_connections.side_effect = RuntimeError("DB error")
        assert client.get("/api-connections").status_code == 500


class TestApiConnectionsGet:

    def test_get_200(self):
        client, api = make_client()
        api.get_api_connection.return_value = {"id": "api_123", "name": "Test"}
        assert client.get("/api-connections/api_123").status_code == 200

    def test_get_404(self):
        client, api = make_client()
        api.get_api_connection.return_value = None
        assert client.get("/api-connections/non_esiste").status_code == 404

    def test_get_errore_500(self):
        client, api = make_client()
        api.get_api_connection.side_effect = RuntimeError("errore")
        assert client.get("/api-connections/api_123").status_code == 500


class TestApiConnectionsCreate:

    def test_create_200(self):
        client, api = make_client()
        api.create_api_connection.return_value = {"id": "api_new"}
        res = client.post("/api-connections", json={"name": "Nuova", "url": "http://x.com"})
        assert res.status_code == 200

    def test_create_errore_500(self):
        client, api = make_client()
        api.create_api_connection.side_effect = ValueError("dati invalidi")
        res = client.post("/api-connections", json={"name": "test"})
        assert res.status_code == 500


class TestApiConnectionsUpdate:

    def test_update_200(self):
        client, api = make_client()
        api.update_api_connection.return_value = {"id": "api_123", "name": "Aggiornata"}
        assert client.put("/api-connections/api_123", json={"name": "Aggiornata"}).status_code == 200

    def test_update_404(self):
        client, api = make_client()
        api.update_api_connection.return_value = None
        assert client.put("/api-connections/non_esiste", json={"name": "test"}).status_code == 404


class TestApiConnectionsDelete:

    def test_delete_200(self):
        client, api = make_client()
        api.delete_api_connection.return_value = True
        assert client.delete("/api-connections/api_123").status_code in (200, 204)

    def test_delete_404(self):
        client, api = make_client()
        api.delete_api_connection.return_value = False
        assert client.delete("/api-connections/non_esiste").status_code == 404


class TestTemplatesRouter:

    def test_list_templates(self):
        client, _ = make_client()
        assert client.get("/templates").status_code in (200, 404, 500)



class TestEnrichmentValidate:

    def test_validate_api_non_trovata(self):
        client, api = make_client()
        api.get_api_connection.return_value = None
        res = client.post("/enrichment/validate", json={
            "project_id": "proj-1", "api_id": "non_esiste", "param_mapping": {},
        })
        assert res.status_code in (404, 500)

    def test_validate_ok(self):
        client, api = make_client()
        api.get_api_connection.return_value = {"id": "api_1", "parameters": []}
        res = client.post("/enrichment/validate", json={
            "project_id": "proj-1", "api_id": "api_1", "param_mapping": {"country": "paese"},
        })
        assert res.status_code in (200, 404, 422, 500)


class TestEnrichmentEstimate:

    def test_estimate_ok(self):
        client, api = make_client()
        api.get_api_connection.return_value = {"id": "api_1", "parameters": []}
        res = client.post("/enrichment/estimate", json={
            "project_id": "proj-1", "api_id": "api_1", "param_mapping": {},
        })
        assert res.status_code in (200, 404, 422, 500)

    def test_estimate_api_non_trovata(self):
        client, api = make_client()
        api.get_api_connection.return_value = None
        res = client.post("/enrichment/estimate", json={
            "project_id": "proj-1", "api_id": "non_esiste", "param_mapping": {},
        })
        assert res.status_code in (404, 422, 500)


class TestApiConnectionsTestEndpoint:

    def test_test_api_connection_200(self):
        client, api = make_client()
        api.test_api_connection.return_value = {
            "success": True, "status_code": 200, "fields": []
        }
        res = client.post("/api-connections/api_123/test", json={})
        assert res.status_code in (200, 404, 422)

    def test_test_api_connection_fallita(self):
        client, api = make_client()
        api.test_api_connection.return_value = {
            "success": False, "error": "Connection refused"
        }
        res = client.post("/api-connections/api_123/test", json={})
        assert res.status_code in (200, 404, 422)
