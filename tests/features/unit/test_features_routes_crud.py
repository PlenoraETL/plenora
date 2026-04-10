"""
tests/features/unit/test_features_routes_crud.py
=================================================
Unit test per le route FastAPI CRUD del modulo Features.

Copre:
  - POST   /features/connections         (create)
  - GET    /features/connections         (list)
  - GET    /features/connections/{id}    (get)
  - PUT    /features/connections/{id}    (update)
  - DELETE /features/connections/{id}    (delete)
  - POST   /features/connections/{id}/test (test)
  - _mask_password helper
  - get_service lazy init
"""

import pytest
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def silence_crash_logs(caplog):
    with caplog.at_level(logging.CRITICAL, logger="app.core.features.routes"):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from app.core.features.models import (
    ArcGISConnection, ArcGISList, ArcGISTestResult,
)


def _now():
    return datetime.now(timezone.utc)


def _make_connection(**overrides) -> ArcGISConnection:
    defaults = dict(
        id="arcgis_test_20260101_abcd1234",
        name="Test", description="",
        server_url="https://server.example.com/arcgis",
        server_type="arcgis_server", auth_type="token",
        username="user", password="secret",
        client_id="", client_secret="", token_url=None,
        created_at=_now(), updated_at=_now(),
    )
    defaults.update(overrides)
    return ArcGISConnection(**defaults)


def _make_client(mock_svc=None):
    """Create TestClient with mocked FeaturesService."""
    from app.core.features.routes import router
    import app.core.features.routes as routes_mod

    app = FastAPI()
    app.include_router(router)

    if mock_svc is None:
        mock_svc = MagicMock()

    # Override the get_service function
    original_get_service = routes_mod.get_service
    routes_mod.get_service = lambda: mock_svc
    client = TestClient(app, raise_server_exceptions=False)

    yield client, mock_svc

    routes_mod.get_service = original_get_service


@pytest.fixture
def client_and_svc():
    yield from _make_client()


# =============================================================================
# _mask_password
# =============================================================================

class TestMaskPassword:

    def test_password_masked(self):
        from app.core.features.routes import _mask_password
        conn = _make_connection(password="super_secret")
        result = _mask_password(conn)
        assert result["password"] == "***"
        assert result["name"] == "Test"

    def test_empty_password_not_masked(self):
        from app.core.features.routes import _mask_password
        conn = _make_connection(password="")
        result = _mask_password(conn)
        assert result["password"] == ""

    def test_preserves_other_fields(self):
        from app.core.features.routes import _mask_password
        conn = _make_connection(username="admin", description="my desc")
        result = _mask_password(conn)
        assert result["username"] == "admin"
        assert result["description"] == "my desc"
        assert result["server_url"] == "https://server.example.com/arcgis"


# =============================================================================
# get_service lazy init
# =============================================================================

class TestGetService:

    def test_lazy_init(self):
        import app.core.features.routes as routes_mod
        original = routes_mod._service
        try:
            routes_mod._service = None
            with patch("app.core.features.routes.FeaturesService") as MockCls:
                MockCls.return_value = MagicMock()
                svc = routes_mod.get_service()
                MockCls.assert_called_once()
                assert svc is not None
        finally:
            routes_mod._service = original

    def test_returns_cached(self):
        import app.core.features.routes as routes_mod
        original = routes_mod._service
        try:
            sentinel = MagicMock()
            routes_mod._service = sentinel
            svc = routes_mod.get_service()
            assert svc is sentinel
        finally:
            routes_mod._service = original


# =============================================================================
# POST /features/connections
# =============================================================================

class TestCreateConnection:

    def test_create_200(self, client_and_svc):
        client, mock_svc = client_and_svc
        conn = _make_connection()
        mock_svc.create_connection.return_value = conn
        res = client.post("/features/connections", json={
            "name": "Test",
            "server_url": "https://server.example.com/arcgis",
            "auth_type": "token",
            "username": "user",
            "password": "secret",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "Test"
        assert data["password"] == "***"

    def test_create_validation_error_422(self, client_and_svc):
        client, _ = client_and_svc
        res = client.post("/features/connections", json={})
        assert res.status_code == 422

    def test_create_internal_error_500(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.create_connection.side_effect = RuntimeError("DB failure")
        res = client.post("/features/connections", json={
            "name": "Test",
            "server_url": "https://x.com",
        })
        assert res.status_code == 500


# =============================================================================
# GET /features/connections
# =============================================================================

class TestListConnections:

    def test_list_200_empty(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.list_connections.return_value = ArcGISList(connections=[])
        res = client.get("/features/connections")
        assert res.status_code == 200
        assert res.json()["connections"] == []

    def test_list_200_with_data(self, client_and_svc):
        client, mock_svc = client_and_svc
        c1 = _make_connection(id="c1", password="s1")
        c2 = _make_connection(id="c2", password="s2")
        mock_svc.list_connections.return_value = ArcGISList(connections=[c1, c2])
        res = client.get("/features/connections")
        assert res.status_code == 200
        conns = res.json()["connections"]
        assert len(conns) == 2
        assert all(c["password"] == "***" for c in conns)

    def test_list_500(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.list_connections.side_effect = RuntimeError("fail")
        res = client.get("/features/connections")
        assert res.status_code == 500


# =============================================================================
# PUT /features/connections/{id}
# =============================================================================

class TestUpdateConnection:

    def test_update_200(self, client_and_svc):
        client, mock_svc = client_and_svc
        updated = _make_connection(name="Updated")
        mock_svc.update_connection.return_value = updated
        res = client.put("/features/connections/some-id", json={"name": "Updated"})
        assert res.status_code == 200
        assert res.json()["name"] == "Updated"

    def test_update_404(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.update_connection.return_value = None
        res = client.put("/features/connections/missing", json={"name": "x"})
        assert res.status_code == 404


# =============================================================================
# DELETE /features/connections/{id}
# =============================================================================

class TestDeleteConnection:

    def test_delete_200(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.delete_connection.return_value = True
        res = client.delete("/features/connections/some-id")
        assert res.status_code == 200
        assert res.json()["success"] is True

    def test_delete_404(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.delete_connection.return_value = False
        res = client.delete("/features/connections/missing")
        assert res.status_code == 404


# =============================================================================
# POST /features/connections/{id}/test
# =============================================================================

class TestTestConnection:

    def test_test_success(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.test_connection.return_value = ArcGISTestResult(
            success=True, message="Connected", details={"version": "10.9"}
        )
        res = client.post("/features/connections/some-id/test")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["message"] == "Connected"

    def test_test_failure_result(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.test_connection.return_value = ArcGISTestResult(
            success=False, message="Auth failed"
        )
        res = client.post("/features/connections/some-id/test")
        assert res.status_code == 200
        assert res.json()["success"] is False

    def test_test_500(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.test_connection.side_effect = RuntimeError("crash")
        res = client.post("/features/connections/some-id/test")
        assert res.status_code == 500
