"""
tests/features/unit/test_features_service_db.py
================================================
Unit test per features_service_db.py — operazioni CRUD su SQLite.

Copre:
  - create_connection_db
  - list_connections_db
  - get_connection_db
  - update_connection_db
  - delete_connection_db
  - _row_to_connection
  - _generate_id
  - _ensure_default_group
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit

from app.core.features.models import (
    ArcGISConnection, ArcGISCreate, ArcGISUpdate, ArcGISList,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**overrides) -> dict:
    """Create a fake DB row dict."""
    now = datetime.now(timezone.utc).isoformat()
    defaults = dict(
        id="arcgis_Test_20260101_000000_abcd1234",
        name="Test",
        description="desc",
        server_url="https://server.example.com/arcgis",
        server_type="arcgis_server",
        auth_type="token",
        username="user",
        password="pass",
        client_id="",
        client_secret="",
        token_url="",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return defaults


# =============================================================================
# _generate_id
# =============================================================================

class TestGenerateId:

    def test_format(self):
        from app.core.features.features_service_db import _generate_id
        result = _generate_id("My Connection")
        assert result.startswith("arcgis_My_Connection_")

    def test_special_chars(self):
        from app.core.features.features_service_db import _generate_id
        result = _generate_id("t@st!#conn")
        # Only alphanumeric, space, and underscore should survive
        name_part = result.split("_", 1)[1].rsplit("_", 2)[0]
        assert "@" not in name_part
        assert "!" not in name_part

    def test_truncation(self):
        from app.core.features.features_service_db import _generate_id
        result = _generate_id("A" * 100)
        # Extract the name part between arcgis_ and the timestamp
        parts = result.split("_")
        name_part = parts[1]  # The AAAA... part
        assert len(name_part) <= 30

    def test_uniqueness(self):
        from app.core.features.features_service_db import _generate_id
        ids = {_generate_id("Same") for _ in range(20)}
        assert len(ids) >= 2

    def test_empty_name(self):
        from app.core.features.features_service_db import _generate_id
        result = _generate_id("")
        assert result.startswith("arcgis_")


# =============================================================================
# _row_to_connection
# =============================================================================

class TestRowToConnection:

    def test_all_fields_mapped(self):
        from app.core.features.features_service_db import _row_to_connection
        row = _make_row()
        conn = _row_to_connection(row)
        assert isinstance(conn, ArcGISConnection)
        assert conn.id == row["id"]
        assert conn.name == row["name"]
        assert conn.server_url == row["server_url"]
        assert conn.username == row["username"]

    def test_missing_optional_fields(self):
        from app.core.features.features_service_db import _row_to_connection
        row = _make_row()
        row.pop("description", None)
        row.pop("server_type", None)
        row.pop("auth_type", None)
        row.pop("username", None)
        row.pop("password", None)
        row.pop("client_id", None)
        row.pop("client_secret", None)
        row.pop("token_url", None)
        conn = _row_to_connection(row)
        assert conn.description == ""
        assert conn.server_type == "arcgis_server"
        assert conn.auth_type == "token"
        assert conn.username == ""

    def test_none_description(self):
        from app.core.features.features_service_db import _row_to_connection
        row = _make_row(description=None)
        conn = _row_to_connection(row)
        assert conn.description == ""

    def test_token_url_none(self):
        from app.core.features.features_service_db import _row_to_connection
        row = _make_row(token_url=None)
        conn = _row_to_connection(row)
        assert conn.token_url is None

    def test_token_url_empty_string(self):
        from app.core.features.features_service_db import _row_to_connection
        row = _make_row(token_url="")
        conn = _row_to_connection(row)
        # Empty string is falsy → becomes None via `or None`
        assert conn.token_url is None


# =============================================================================
# create_connection_db
# =============================================================================

class TestCreateConnectionDb:

    @patch("app.core.features.features_service_db.db")
    def test_creates_and_returns(self, mock_db):
        from app.core.features.features_service_db import create_connection_db
        row = _make_row()
        mock_db.fetchone.return_value = row

        data = ArcGISCreate(
            name="Test",
            server_url="https://server.example.com/arcgis",
            auth_type="token",
            username="user",
            password="pass",
        )
        result = create_connection_db(data)
        assert isinstance(result, ArcGISConnection)
        assert mock_db.execute.call_count >= 2  # ensure_default_group + INSERT

    @patch("app.core.features.features_service_db.db")
    def test_auto_detect_online(self, mock_db):
        from app.core.features.features_service_db import create_connection_db
        row = _make_row(server_type="arcgis_online")
        mock_db.fetchone.return_value = row

        data = ArcGISCreate(
            name="Online",
            server_url="https://www.arcgis.com/sharing/rest",
            auth_type="token",
        )
        create_connection_db(data)
        # Check the INSERT call contains arcgis_online
        insert_call = [c for c in mock_db.execute.call_args_list if "INSERT INTO arcgis_connections" in str(c)]
        assert len(insert_call) >= 1

    @patch("app.core.features.features_service_db.db")
    def test_auto_detect_enterprise(self, mock_db):
        from app.core.features.features_service_db import create_connection_db
        row = _make_row(server_type="arcgis_enterprise")
        mock_db.fetchone.return_value = row

        data = ArcGISCreate(
            name="Enterprise",
            server_url="https://gis.org/portal/rest",
            auth_type="token",
        )
        create_connection_db(data)
        insert_call = [c for c in mock_db.execute.call_args_list if "INSERT INTO arcgis_connections" in str(c)]
        assert len(insert_call) >= 1

    @patch("app.core.features.features_service_db.db")
    def test_explicit_server_type_not_overridden(self, mock_db):
        from app.core.features.features_service_db import create_connection_db
        row = _make_row(server_type="arcgis_server")
        mock_db.fetchone.return_value = row

        data = ArcGISCreate(
            name="Explicit",
            server_url="https://www.arcgis.com/sharing/rest",
            server_type="arcgis_server",  # explicit
            auth_type="token",
        )
        create_connection_db(data)
        insert_call = mock_db.execute.call_args_list[-1]
        # The params tuple — server_type should be "arcgis_server" (explicit)
        params = insert_call[0][1]
        assert "arcgis_server" in params


# =============================================================================
# list_connections_db
# =============================================================================

class TestListConnectionsDb:

    @patch("app.core.features.features_service_db.db")
    def test_returns_arcgis_list(self, mock_db):
        from app.core.features.features_service_db import list_connections_db
        mock_db.fetchall.return_value = [_make_row(), _make_row(id="arcgis_two")]
        result = list_connections_db()
        assert isinstance(result, ArcGISList)
        assert len(result.connections) == 2

    @patch("app.core.features.features_service_db.db")
    def test_empty(self, mock_db):
        from app.core.features.features_service_db import list_connections_db
        mock_db.fetchall.return_value = []
        result = list_connections_db()
        assert result.connections == []


# =============================================================================
# get_connection_db
# =============================================================================

class TestGetConnectionDb:

    @patch("app.core.features.features_service_db.db")
    def test_found(self, mock_db):
        from app.core.features.features_service_db import get_connection_db
        mock_db.fetchone.return_value = _make_row()
        result = get_connection_db("arcgis_Test_20260101_000000_abcd1234")
        assert result is not None
        assert result.name == "Test"

    @patch("app.core.features.features_service_db.db")
    def test_not_found(self, mock_db):
        from app.core.features.features_service_db import get_connection_db
        mock_db.fetchone.return_value = None
        result = get_connection_db("nonexistent")
        assert result is None


# =============================================================================
# update_connection_db
# =============================================================================

class TestUpdateConnectionDb:

    @patch("app.core.features.features_service_db.db")
    def test_update_name(self, mock_db):
        from app.core.features.features_service_db import update_connection_db
        # First fetchone: check existence
        # Second fetchone: return updated row
        mock_db.fetchone.side_effect = [
            {"id": "x"},  # existence check
            _make_row(name="Updated"),  # get after update
        ]
        data = ArcGISUpdate(name="Updated")
        result = update_connection_db("x", data)
        assert result is not None
        assert result.name == "Updated"
        # execute should have been called for UPDATE
        update_calls = [c for c in mock_db.execute.call_args_list if "UPDATE" in str(c)]
        assert len(update_calls) == 1

    @patch("app.core.features.features_service_db.db")
    def test_not_found(self, mock_db):
        from app.core.features.features_service_db import update_connection_db
        mock_db.fetchone.return_value = None
        result = update_connection_db("missing", ArcGISUpdate(name="x"))
        assert result is None

    @patch("app.core.features.features_service_db.db")
    def test_no_fields_returns_current(self, mock_db):
        from app.core.features.features_service_db import update_connection_db
        mock_db.fetchone.side_effect = [
            {"id": "x"},  # existence check
            _make_row(),  # get_connection_db
        ]
        data = ArcGISUpdate()  # no fields set
        result = update_connection_db("x", data)
        assert result is not None
        # No execute UPDATE should be called
        update_calls = [c for c in mock_db.execute.call_args_list if "UPDATE" in str(c)]
        assert len(update_calls) == 0

    @patch("app.core.features.features_service_db.db")
    def test_multiple_fields(self, mock_db):
        from app.core.features.features_service_db import update_connection_db
        mock_db.fetchone.side_effect = [
            {"id": "x"},
            _make_row(name="New", description="New desc"),
        ]
        data = ArcGISUpdate(name="New", description="New desc", password="newpass")
        result = update_connection_db("x", data)
        assert result is not None
        # Check the SQL contains all three SET clauses
        update_call = [c for c in mock_db.execute.call_args_list if "UPDATE" in str(c)]
        assert len(update_call) == 1
        sql = update_call[0][0][0]
        assert "name = ?" in sql
        assert "description = ?" in sql
        assert "password = ?" in sql


# =============================================================================
# delete_connection_db
# =============================================================================

class TestDeleteConnectionDb:

    @patch("app.core.features.features_service_db.db")
    def test_success(self, mock_db):
        from app.core.features.features_service_db import delete_connection_db
        mock_db.fetchone.return_value = {"id": "x"}
        result = delete_connection_db("x")
        assert result is True
        delete_calls = [c for c in mock_db.execute.call_args_list if "DELETE" in str(c)]
        assert len(delete_calls) == 1

    @patch("app.core.features.features_service_db.db")
    def test_not_found(self, mock_db):
        from app.core.features.features_service_db import delete_connection_db
        mock_db.fetchone.return_value = None
        result = delete_connection_db("missing")
        assert result is False
        delete_calls = [c for c in mock_db.execute.call_args_list if "DELETE" in str(c)]
        assert len(delete_calls) == 0


# =============================================================================
# _ensure_default_group
# =============================================================================

class TestEnsureDefaultGroup:

    @patch("app.core.features.features_service_db.db")
    def test_inserts_default_group(self, mock_db):
        from app.core.features.features_service_db import _ensure_default_group
        _ensure_default_group()
        mock_db.execute.assert_called_once()
        sql = mock_db.execute.call_args[0][0]
        assert "INSERT OR IGNORE INTO groups" in sql
        params = mock_db.execute.call_args[0][1]
        assert params[0] == "default"
