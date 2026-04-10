"""
Unit tests for datasources/router.py — CRUD endpoints + test_connection.
Uses FastAPI TestClient with dependency overrides.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

from app.core.datasources.models import (
    DataSource,
    DataSourceCreate,
    DataSourceList,
    DataSourceTestResult,
)
from app.core.datasources.router import router, init_datasources, _svc
from app.core.auth.auth import get_current_user, CurrentUser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fake_user():
    return CurrentUser(user_id="u1", username="test", group_id="g1", role="admin")


def _fake_superadmin():
    return CurrentUser(user_id="u0", username="admin", group_id="g0", role="superadmin")


def _ds_model(ds_id="ds_1", group_id="g1"):
    now = datetime.now()
    return DataSource(
        id=ds_id, name="Test", type="local", root_path="/data",
        group_id=group_id, created_at=now, updated_at=now,
    )


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api")

    mock_service = MagicMock()
    init_datasources(mock_service)

    app.dependency_overrides[get_current_user] = _fake_user

    yield TestClient(app), mock_service

    app.dependency_overrides.clear()
    init_datasources(None)


@pytest.fixture
def superadmin_client():
    app = FastAPI()
    app.include_router(router, prefix="/api")

    mock_service = MagicMock()
    init_datasources(mock_service)

    app.dependency_overrides[get_current_user] = _fake_superadmin

    yield TestClient(app), mock_service

    app.dependency_overrides.clear()
    init_datasources(None)


# ========================== POST /datasources =============================

class TestCreateDatasource:

    def test_success(self, client):
        tc, svc = client
        svc.create_datasource.return_value = _ds_model()

        resp = tc.post("/api/datasources", json={
            "name": "Test", "type": "local", "root_path": "/data",
        })
        assert resp.status_code == 201
        assert resp.json()["id"] == "ds_1"
        svc.create_datasource.assert_called_once()

    def test_invalid_type(self, client):
        tc, svc = client
        resp = tc.post("/api/datasources", json={
            "name": "Test", "type": "s3",
        })
        assert resp.status_code == 422

    def test_missing_name(self, client):
        tc, svc = client
        resp = tc.post("/api/datasources", json={"type": "local"})
        assert resp.status_code == 422

    def test_server_error(self, client):
        tc, svc = client
        svc.create_datasource.side_effect = Exception("DB error")
        resp = tc.post("/api/datasources", json={
            "name": "Test", "type": "local",
        })
        assert resp.status_code == 500


# ========================== GET /datasources ==============================

class TestListDatasources:

    def test_success(self, client):
        tc, svc = client
        svc.list_datasources.return_value = DataSourceList(
            datasources=[_ds_model()]
        )
        resp = tc.get("/api/datasources")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["datasources"]) == 1

    def test_empty(self, client):
        tc, svc = client
        svc.list_datasources.return_value = DataSourceList(datasources=[])
        resp = tc.get("/api/datasources")
        assert resp.status_code == 200
        assert resp.json()["datasources"] == []


# ========================== GET /datasources/{id} =========================

class TestGetDatasource:

    def test_success(self, client):
        tc, svc = client
        svc.get_datasource.return_value = _ds_model()
        resp = tc.get("/api/datasources/ds_1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "ds_1"

    def test_not_found(self, client):
        tc, svc = client
        svc.get_datasource.return_value = None
        resp = tc.get("/api/datasources/nope")
        assert resp.status_code == 404

    def test_access_denied_wrong_group(self, client):
        tc, svc = client
        svc.get_datasource.return_value = _ds_model(group_id="other_group")
        resp = tc.get("/api/datasources/ds_1")
        assert resp.status_code == 403

    def test_superadmin_any_group(self, superadmin_client):
        tc, svc = superadmin_client
        svc.get_datasource.return_value = _ds_model(group_id="any_group")
        resp = tc.get("/api/datasources/ds_1")
        assert resp.status_code == 200


# ========================== PUT /datasources/{id} =========================

class TestUpdateDatasource:

    def test_success(self, client):
        tc, svc = client
        svc.get_datasource.return_value = _ds_model()
        updated = _ds_model()
        updated.name = "Updated"
        svc.update_datasource.return_value = updated

        resp = tc.put("/api/datasources/ds_1", json={"name": "Updated"})
        assert resp.status_code == 200

    def test_not_found(self, client):
        tc, svc = client
        svc.get_datasource.return_value = None
        resp = tc.put("/api/datasources/nope", json={"name": "X"})
        assert resp.status_code == 404

    def test_access_denied(self, client):
        tc, svc = client
        svc.get_datasource.return_value = _ds_model(group_id="other")
        resp = tc.put("/api/datasources/ds_1", json={"name": "X"})
        assert resp.status_code == 403


# ========================== DELETE /datasources/{id} ======================

class TestDeleteDatasource:

    def test_success(self, client):
        tc, svc = client
        svc.get_datasource.return_value = _ds_model()
        svc.delete_datasource.return_value = True
        resp = tc.delete("/api/datasources/ds_1")
        assert resp.status_code == 204

    def test_not_found(self, client):
        tc, svc = client
        svc.get_datasource.return_value = None
        resp = tc.delete("/api/datasources/nope")
        assert resp.status_code == 404

    def test_access_denied(self, client):
        tc, svc = client
        svc.get_datasource.return_value = _ds_model(group_id="other")
        resp = tc.delete("/api/datasources/ds_1")
        assert resp.status_code == 403

    def test_delete_returns_false(self, client):
        tc, svc = client
        svc.get_datasource.return_value = _ds_model()
        svc.delete_datasource.return_value = False
        resp = tc.delete("/api/datasources/ds_1")
        assert resp.status_code == 404


# ========================== POST /datasources/{id}/test ===================

class TestTestConnection:

    def test_success(self, client):
        tc, svc = client
        svc.get_datasource.return_value = _ds_model()
        svc.test_connection.return_value = DataSourceTestResult(
            success=True, message="OK",
        )
        resp = tc.post("/api/datasources/ds_1/test")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_not_found(self, client):
        tc, svc = client
        svc.get_datasource.return_value = None
        resp = tc.post("/api/datasources/nope/test")
        assert resp.status_code == 404

    def test_access_denied(self, client):
        tc, svc = client
        svc.get_datasource.return_value = _ds_model(group_id="other")
        resp = tc.post("/api/datasources/ds_1/test")
        assert resp.status_code == 403


# ========================== _svc uninitialized ============================

class TestServiceNotInitialized:

    def test_raises_500(self):
        app = FastAPI()
        app.include_router(router, prefix="/api")
        init_datasources(None)
        app.dependency_overrides[get_current_user] = _fake_user
        tc = TestClient(app)
        resp = tc.get("/api/datasources")
        assert resp.status_code == 500
        app.dependency_overrides.clear()
