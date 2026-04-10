"""
tests/estrai/unit/test_connections_router_coverage.py
=====================================================
Unit tests for the Connections router endpoints.

Covers:
  - POST   /connections             (create)
  - GET    /connections             (list)
  - GET    /connections/{id}        (get, 404, 403)
  - PUT    /connections/{id}        (update, 404, 403)
  - DELETE /connections/{id}        (delete, 404, 403)
  - POST   /connections/{id}/test   (test, 404, 403)
  - GET    /connections/{id}/tables (tables, 404, 500)

Uses TestClient with dependency_overrides. Mocks ConnectionService
and DatabaseInspector via init_connections().
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

import logging

from app.core.connections.router import router, init_connections
from app.core.connections.models import (
    DatabaseConnection,
    ConnectionList,
    ConnectionTestResult,
)
from app.core.auth.auth import get_current_user, CurrentUser


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _mock_user(role="admin", group_id="grp-1") -> CurrentUser:
    return CurrentUser(
        user_id="uid-1",
        username="testuser",
        group_id=group_id,
        role=role,
    )


def _make_connection(conn_id="conn-1", group_id="grp-1") -> DatabaseConnection:
    now = datetime.now(timezone.utc)
    return DatabaseConnection(
        id=conn_id,
        name="TestDB",
        description="test",
        type="postgres",
        host="localhost",
        port=5432,
        database="testdb",
        username="user",
        password="pass",
        group_id=group_id,
        created_at=now,
        updated_at=now,
    )


def _setup_client(
    mock_conn_svc=None,
    mock_db_inspector=None,
    user_role="admin",
    user_group="grp-1",
):
    """Build a TestClient with mocked services."""
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: _mock_user(
        role=user_role, group_id=user_group
    )
    app.include_router(router)

    # Inject mocks via the module-level init function
    init_connections(
        connection_service=mock_conn_svc or MagicMock(),
        database_inspector=mock_db_inspector or MagicMock(),
    )

    return TestClient(app, raise_server_exceptions=False)


# =============================================================================
# POST /connections
# =============================================================================


class TestCreateConnection:

    def test_create_200(self):
        mock_svc = MagicMock()
        conn = _make_connection()
        mock_svc.create_connection.return_value = conn
        client = _setup_client(mock_conn_svc=mock_svc)

        res = client.post(
            "/connections",
            json={
                "name": "TestDB",
                "type": "postgres",
                "host": "localhost",
                "port": 5432,
                "database": "testdb",
                "username": "user",
                "password": "pass",
            },
        )
        assert res.status_code == 200
        assert res.json()["id"] == "conn-1"
        mock_svc.create_connection.assert_called_once()

    def test_create_422_missing_fields(self):
        client = _setup_client()
        res = client.post("/connections", json={"name": "OnlyName"})
        assert res.status_code == 422


# =============================================================================
# GET /connections
# =============================================================================


class TestListConnections:

    def test_list_200_empty(self):
        mock_svc = MagicMock()
        mock_svc.list_connections.return_value = ConnectionList(connections=[])
        client = _setup_client(mock_conn_svc=mock_svc)

        res = client.get("/connections")
        assert res.status_code == 200
        assert res.json()["connections"] == []

    def test_list_200_with_data(self):
        mock_svc = MagicMock()
        conn = _make_connection()
        mock_svc.list_connections.return_value = ConnectionList(
            connections=[conn]
        )
        client = _setup_client(mock_conn_svc=mock_svc)

        res = client.get("/connections")
        assert res.status_code == 200
        assert len(res.json()["connections"]) == 1

    def test_list_superadmin_no_group_filter(self):
        mock_svc = MagicMock()
        mock_svc.list_connections.return_value = ConnectionList(connections=[])
        client = _setup_client(
            mock_conn_svc=mock_svc, user_role="superadmin"
        )

        res = client.get("/connections")
        assert res.status_code == 200
        # superadmin passes group_id=None
        mock_svc.list_connections.assert_called_once_with(group_id=None)


# =============================================================================
# GET /connections/{id}
# =============================================================================


class TestGetConnection:

    def test_get_200(self):
        mock_svc = MagicMock()
        conn = _make_connection()
        mock_svc.get_connection.return_value = conn
        client = _setup_client(mock_conn_svc=mock_svc)

        res = client.get("/connections/conn-1")
        assert res.status_code == 200
        assert res.json()["id"] == "conn-1"

    def test_get_404(self):
        mock_svc = MagicMock()
        mock_svc.get_connection.return_value = None
        client = _setup_client(mock_conn_svc=mock_svc)

        res = client.get("/connections/missing")
        assert res.status_code == 404

    def test_get_403_wrong_group(self):
        mock_svc = MagicMock()
        conn = _make_connection(group_id="other-group")
        mock_svc.get_connection.return_value = conn
        client = _setup_client(
            mock_conn_svc=mock_svc, user_role="admin", user_group="grp-1"
        )

        res = client.get("/connections/conn-1")
        assert res.status_code == 403

    def test_get_superadmin_bypasses_group_check(self):
        mock_svc = MagicMock()
        conn = _make_connection(group_id="other-group")
        mock_svc.get_connection.return_value = conn
        client = _setup_client(
            mock_conn_svc=mock_svc, user_role="superadmin"
        )

        res = client.get("/connections/conn-1")
        assert res.status_code == 200


# =============================================================================
# PUT /connections/{id}
# =============================================================================


class TestUpdateConnection:

    def test_update_200(self):
        mock_svc = MagicMock()
        conn = _make_connection()
        mock_svc.get_connection.return_value = conn
        updated = _make_connection()
        updated.name = "Updated"
        mock_svc.update_connection.return_value = updated
        client = _setup_client(mock_conn_svc=mock_svc)

        res = client.put(
            "/connections/conn-1", json={"name": "Updated"}
        )
        assert res.status_code == 200
        mock_svc.update_connection.assert_called_once()

    def test_update_404(self):
        mock_svc = MagicMock()
        mock_svc.get_connection.return_value = None
        client = _setup_client(mock_conn_svc=mock_svc)

        res = client.put(
            "/connections/missing", json={"name": "Updated"}
        )
        assert res.status_code == 404

    def test_update_403_wrong_group(self):
        mock_svc = MagicMock()
        conn = _make_connection(group_id="other-group")
        mock_svc.get_connection.return_value = conn
        client = _setup_client(
            mock_conn_svc=mock_svc, user_role="admin", user_group="grp-1"
        )

        res = client.put(
            "/connections/conn-1", json={"name": "Hacked"}
        )
        assert res.status_code == 403


# =============================================================================
# DELETE /connections/{id}
# =============================================================================


class TestDeleteConnection:

    def test_delete_200(self):
        mock_svc = MagicMock()
        conn = _make_connection()
        mock_svc.get_connection.return_value = conn
        client = _setup_client(mock_conn_svc=mock_svc)

        res = client.delete("/connections/conn-1")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "deleted"
        assert body["connection_id"] == "conn-1"

    def test_delete_404(self):
        mock_svc = MagicMock()
        mock_svc.get_connection.return_value = None
        client = _setup_client(mock_conn_svc=mock_svc)

        res = client.delete("/connections/missing")
        assert res.status_code == 404

    def test_delete_403_wrong_group(self):
        mock_svc = MagicMock()
        conn = _make_connection(group_id="other-group")
        mock_svc.get_connection.return_value = conn
        client = _setup_client(
            mock_conn_svc=mock_svc, user_role="user", user_group="grp-1"
        )

        res = client.delete("/connections/conn-1")
        assert res.status_code == 403


# =============================================================================
# POST /connections/{id}/test
# =============================================================================


class TestTestConnection:

    def test_test_200_success(self):
        mock_svc = MagicMock()
        conn = _make_connection()
        mock_svc.get_connection.return_value = conn
        mock_svc.test_connection.return_value = ConnectionTestResult(
            success=True, message="OK"
        )
        client = _setup_client(mock_conn_svc=mock_svc)

        res = client.post("/connections/conn-1/test")
        assert res.status_code == 200
        assert res.json()["success"] is True

    def test_test_200_failure(self):
        mock_svc = MagicMock()
        conn = _make_connection()
        mock_svc.get_connection.return_value = conn
        mock_svc.test_connection.return_value = ConnectionTestResult(
            success=False, message="Connection refused"
        )
        client = _setup_client(mock_conn_svc=mock_svc)

        res = client.post("/connections/conn-1/test")
        assert res.status_code == 200
        assert res.json()["success"] is False

    def test_test_404(self):
        mock_svc = MagicMock()
        mock_svc.get_connection.return_value = None
        client = _setup_client(mock_conn_svc=mock_svc)

        res = client.post("/connections/missing/test")
        assert res.status_code == 404

    def test_test_403_wrong_group(self):
        mock_svc = MagicMock()
        conn = _make_connection(group_id="other-group")
        mock_svc.get_connection.return_value = conn
        client = _setup_client(
            mock_conn_svc=mock_svc, user_role="admin", user_group="grp-1"
        )

        res = client.post("/connections/conn-1/test")
        assert res.status_code == 403


# =============================================================================
# GET /connections/{id}/tables
# =============================================================================


class TestGetConnectionTables:

    def test_tables_200(self):
        mock_svc = MagicMock()
        conn = _make_connection()
        mock_svc.get_connection.return_value = conn

        mock_inspector = MagicMock()
        mock_inspector.get_tables_and_columns.return_value = {
            "users": [{"name": "id", "type": "int"}]
        }
        mock_inspector.get_views_and_columns.return_value = {
            "v_users": [{"name": "id", "type": "int"}]
        }

        client = _setup_client(
            mock_conn_svc=mock_svc, mock_db_inspector=mock_inspector
        )

        res = client.get("/connections/conn-1/tables")
        assert res.status_code == 200
        body = res.json()
        assert "tables" in body
        assert "views" in body
        assert "users" in body["tables"]

    def test_tables_404_connection_not_found(self):
        mock_svc = MagicMock()
        mock_svc.get_connection.return_value = None
        client = _setup_client(mock_conn_svc=mock_svc)

        res = client.get("/connections/missing/tables")
        assert res.status_code == 404

    def test_tables_500_db_error(self):
        mock_svc = MagicMock()
        conn = _make_connection()
        mock_svc.get_connection.return_value = conn

        mock_inspector = MagicMock()
        mock_inspector.get_tables_and_columns.side_effect = Exception(
            "DB unreachable"
        )

        client = _setup_client(
            mock_conn_svc=mock_svc, mock_db_inspector=mock_inspector
        )

        res = client.get("/connections/conn-1/tables")
        assert res.status_code == 500

    def test_tables_views_error_does_not_block(self):
        """Views failure should not prevent tables from being returned."""
        mock_svc = MagicMock()
        conn = _make_connection()
        mock_svc.get_connection.return_value = conn

        mock_inspector = MagicMock()
        mock_inspector.get_tables_and_columns.return_value = {"t1": []}
        mock_inspector.get_views_and_columns.side_effect = Exception("no views")

        client = _setup_client(
            mock_conn_svc=mock_svc, mock_db_inspector=mock_inspector
        )

        res = client.get("/connections/conn-1/tables")
        assert res.status_code == 200
        body = res.json()
        assert "tables" in body
        assert body["views"] == {}


# =============================================================================
# SERVICE NOT INITIALIZED (503)
# =============================================================================


class TestServiceNotInitialized:

    def test_503_when_connection_service_none(self):
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: _mock_user()
        app.include_router(router)
        init_connections(
            connection_service=None,
            database_inspector=None,
        )
        client = TestClient(app, raise_server_exceptions=False)

        res = client.get("/connections")
        assert res.status_code == 503
