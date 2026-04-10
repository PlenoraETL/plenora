"""
tests/features/unit/test_features_service_crud.py
=================================================
Unit test per FeaturesService — operazioni CRUD e helper.

Copre:
  - create_connection, list_connections, get_connection
  - update_connection, delete_connection, test_connection
  - _generate_connection_id, _to_connection_info
  - _detect_server_type (static)
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from app.core.features.models import (
    ArcGISConnection, ArcGISCreate, ArcGISUpdate,
    ArcGISList, ArcGISTestResult,
)
from app.core.features.arcgis_client import ArcGISConnectionInfo


def _make_connection(**overrides) -> ArcGISConnection:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id="arcgis_TestConn_20260101_000000_abcd1234",
        name="TestConn",
        description="desc",
        server_url="https://server.example.com/arcgis",
        server_type="arcgis_server",
        auth_type="token",
        username="user1",
        password="secret",
        client_id="",
        client_secret="",
        token_url=None,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return ArcGISConnection(**defaults)


def _make_service(**overrides):
    """Create a FeaturesService with mocked dependencies."""
    from app.core.features.features_service import FeaturesService
    client = overrides.pop("client", MagicMock())
    svc = FeaturesService.__new__(FeaturesService)
    svc._client = client
    svc._save = overrides.pop("save_function", MagicMock())
    svc._arcgis_dir = overrides.pop("arcgis_dir", MagicMock())
    svc._results_dir = overrides.pop("results_dir", MagicMock())
    return svc


# =============================================================================
# _generate_connection_id
# =============================================================================

class TestGenerateConnectionId:

    def test_basic_format(self):
        svc = _make_service()
        cid = svc._generate_connection_id("My Connection")
        assert cid.startswith("arcgis_My_Connection_")
        parts = cid.split("_")
        # arcgis, My, Connection, YYYYMMDD, HHMMSS, uuid8
        assert len(parts) >= 5

    def test_special_chars_stripped(self):
        svc = _make_service()
        cid = svc._generate_connection_id("te$t!@#na me")
        # Only alphanumeric, space, underscore survive; $ ! @ # are removed
        # "te$t!@#na me" -> "tetna me" -> "tetna_me"
        assert cid.startswith("arcgis_tetna_me_")

    def test_long_name_truncated(self):
        svc = _make_service()
        long_name = "A" * 100
        cid = svc._generate_connection_id(long_name)
        # Format: arcgis_{safe_name}_{YYYYMMDD}_{HHMMSS}_{uuid8}
        # safe_name is truncated to 30 chars
        # Extract safe_name: between first "arcgis_" and the timestamp pattern
        import re
        m = re.match(r"arcgis_(.+?)_\d{8}_\d{6}_[a-f0-9]{8}$", cid)
        assert m is not None
        assert len(m.group(1)) <= 30

    def test_empty_name(self):
        svc = _make_service()
        cid = svc._generate_connection_id("")
        assert cid.startswith("arcgis_")

    def test_unique_ids(self):
        svc = _make_service()
        ids = {svc._generate_connection_id("Same") for _ in range(20)}
        # uuid part makes them unique (extremely high probability)
        assert len(ids) >= 2


# =============================================================================
# _to_connection_info
# =============================================================================

class TestToConnectionInfo:

    def test_maps_all_fields(self):
        svc = _make_service()
        conn = _make_connection(
            username="u", password="p",
            client_id="cid", client_secret="csec",
            token_url="https://tok.example.com",
        )
        info = svc._to_connection_info(conn)
        assert isinstance(info, ArcGISConnectionInfo)
        assert info.server_url == conn.server_url
        assert info.server_type == conn.server_type
        assert info.auth_type == conn.auth_type
        assert info.username == "u"
        assert info.password == "p"
        assert info.client_id == "cid"
        assert info.client_secret == "csec"
        assert info.token_url == "https://tok.example.com"

    def test_token_url_none_becomes_empty(self):
        svc = _make_service()
        conn = _make_connection(token_url=None)
        info = svc._to_connection_info(conn)
        assert info.token_url == ""


# =============================================================================
# _detect_server_type (static)
# =============================================================================

class TestDetectServerType:

    def test_arcgis_online(self):
        from app.core.features.features_service import FeaturesService
        assert FeaturesService._detect_server_type("https://www.arcgis.com/home") == "arcgis_online"

    def test_arcgis_enterprise(self):
        from app.core.features.features_service import FeaturesService
        assert FeaturesService._detect_server_type("https://gis.org/portal/rest") == "arcgis_enterprise"

    def test_arcgis_server(self):
        from app.core.features.features_service import FeaturesService
        assert FeaturesService._detect_server_type("https://myserver.local/arcgis") == "arcgis_server"

    def test_empty_url(self):
        from app.core.features.features_service import FeaturesService
        assert FeaturesService._detect_server_type("") == "arcgis_server"

    def test_none_url(self):
        from app.core.features.features_service import FeaturesService
        assert FeaturesService._detect_server_type(None) == "arcgis_server"


# =============================================================================
# create_connection
# =============================================================================

class TestCreateConnection:

    @patch("app.core.features.features_service.create_connection_db")
    def test_delegates_to_db(self, mock_create_db):
        conn = _make_connection()
        mock_create_db.return_value = conn
        svc = _make_service()
        data = ArcGISCreate(
            name="TestConn",
            server_url="https://server.example.com/arcgis",
            auth_type="token",
            username="user1",
            password="secret",
        )
        result = svc.create_connection(data)
        mock_create_db.assert_called_once_with(data)
        assert result.id == conn.id

    @patch("app.core.features.features_service.create_connection_db")
    def test_propagates_exception(self, mock_create_db):
        mock_create_db.side_effect = RuntimeError("DB error")
        svc = _make_service()
        data = ArcGISCreate(name="X", server_url="https://x.com")
        with pytest.raises(RuntimeError, match="DB error"):
            svc.create_connection(data)


# =============================================================================
# list_connections
# =============================================================================

class TestListConnections:

    @patch("app.core.features.features_service.list_connections_db")
    def test_returns_arcgis_list(self, mock_list_db):
        conn = _make_connection()
        mock_list_db.return_value = ArcGISList(connections=[conn])
        svc = _make_service()
        result = svc.list_connections()
        assert isinstance(result, ArcGISList)
        assert len(result.connections) == 1

    @patch("app.core.features.features_service.list_connections_db")
    def test_empty_list(self, mock_list_db):
        mock_list_db.return_value = ArcGISList(connections=[])
        svc = _make_service()
        result = svc.list_connections()
        assert result.connections == []


# =============================================================================
# get_connection
# =============================================================================

class TestGetConnection:

    @patch("app.core.features.features_service.get_connection_db")
    def test_found(self, mock_get_db):
        conn = _make_connection()
        mock_get_db.return_value = conn
        svc = _make_service()
        result = svc.get_connection("arcgis_TestConn_20260101_000000_abcd1234")
        assert result.name == "TestConn"

    @patch("app.core.features.features_service.get_connection_db")
    def test_not_found_returns_none(self, mock_get_db):
        mock_get_db.return_value = None
        svc = _make_service()
        assert svc.get_connection("nonexistent") is None


# =============================================================================
# update_connection
# =============================================================================

class TestUpdateConnection:

    @patch("app.core.features.features_service.update_connection_db")
    def test_update_success(self, mock_update_db):
        updated = _make_connection(name="Updated")
        mock_update_db.return_value = updated
        svc = _make_service()
        data = ArcGISUpdate(name="Updated")
        result = svc.update_connection("some-id", data)
        assert result.name == "Updated"
        mock_update_db.assert_called_once_with("some-id", data)

    @patch("app.core.features.features_service.update_connection_db")
    def test_update_not_found(self, mock_update_db):
        mock_update_db.return_value = None
        svc = _make_service()
        result = svc.update_connection("missing", ArcGISUpdate(name="x"))
        assert result is None


# =============================================================================
# delete_connection
# =============================================================================

class TestDeleteConnection:

    @patch("app.core.features.features_service.delete_connection_db")
    def test_delete_success(self, mock_delete_db):
        mock_delete_db.return_value = True
        svc = _make_service()
        assert svc.delete_connection("some-id") is True
        mock_delete_db.assert_called_once_with("some-id")

    @patch("app.core.features.features_service.delete_connection_db")
    def test_delete_not_found(self, mock_delete_db):
        mock_delete_db.return_value = False
        svc = _make_service()
        assert svc.delete_connection("missing") is False


# =============================================================================
# test_connection
# =============================================================================

class TestTestConnection:

    @patch("app.core.features.features_service.get_connection_db")
    def test_connection_not_found(self, mock_get_db):
        mock_get_db.return_value = None
        svc = _make_service()
        result = svc.test_connection("missing")
        assert isinstance(result, ArcGISTestResult)
        assert result.success is False
        assert "non trovata" in result.message

    @patch("app.core.features.features_service.get_connection_db")
    def test_connection_success(self, mock_get_db):
        conn = _make_connection()
        mock_get_db.return_value = conn
        mock_client = MagicMock()
        mock_client.test_connection.return_value = {
            "success": True,
            "message": "OK",
            "details": {"version": "10.9"},
        }
        svc = _make_service(client=mock_client)
        result = svc.test_connection(conn.id)
        assert result.success is True
        assert result.message == "OK"
        assert result.details == {"version": "10.9"}

    @patch("app.core.features.features_service.get_connection_db")
    def test_connection_failure(self, mock_get_db):
        conn = _make_connection()
        mock_get_db.return_value = conn
        mock_client = MagicMock()
        mock_client.test_connection.return_value = {
            "success": False,
            "message": "Timeout",
        }
        svc = _make_service(client=mock_client)
        result = svc.test_connection(conn.id)
        assert result.success is False
        assert result.message == "Timeout"
        assert result.details is None

    @patch("app.core.features.features_service.get_connection_db")
    def test_client_receives_connection_info(self, mock_get_db):
        conn = _make_connection(username="admin", password="pass")
        mock_get_db.return_value = conn
        mock_client = MagicMock()
        mock_client.test_connection.return_value = {"success": True, "message": "ok"}
        svc = _make_service(client=mock_client)
        svc.test_connection(conn.id)

        call_args = mock_client.test_connection.call_args[0][0]
        assert isinstance(call_args, ArcGISConnectionInfo)
        assert call_args.username == "admin"
        assert call_args.password == "pass"
