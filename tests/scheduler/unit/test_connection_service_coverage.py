"""
tests/scheduler/unit/test_connection_service_coverage.py
========================================================
Unit tests for app.services.connection_service — CRUD, encryption,
test_connection dispatching.

Mocked: app.db.db_service, cryptography.fernet, driver imports.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit

from app.services.connection_service import (
    ConnectionService,
    _encrypt_password,
    _decrypt_password,
    _get_fernet,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**overrides) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    defaults = dict(
        id="conn_Test_20260101_000000_abcd1234",
        name="Test",
        db_type="postgres",
        host="localhost",
        port=5432,
        database="testdb",
        db_schema=None,
        username="user",
        password="secret",
        group_id="default",
        extra='{"description": "test desc"}',
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return defaults


# =============================================================================
# _encrypt_password / _decrypt_password
# =============================================================================

class TestEncryptDecrypt:

    @patch("app.services.connection_service._get_fernet")
    def test_encrypt_password(self, mock_fernet_fn):
        mock_f = MagicMock()
        mock_f.encrypt.return_value = b"encrypted_token"
        mock_fernet_fn.return_value = mock_f

        result = _encrypt_password("my_secret")
        assert result == "enc:encrypted_token"

    def test_encrypt_none_returns_none(self):
        assert _encrypt_password(None) is None

    def test_encrypt_empty_returns_empty(self):
        assert _encrypt_password("") == ""

    def test_encrypt_already_encrypted_noop(self):
        assert _encrypt_password("enc:token123") == "enc:token123"

    @patch("app.services.connection_service._get_fernet")
    def test_encrypt_failure_returns_plain(self, mock_fernet_fn):
        mock_fernet_fn.side_effect = RuntimeError("no key")
        result = _encrypt_password("secret")
        assert result == "secret"

    @patch("app.services.connection_service._get_fernet")
    def test_decrypt_password(self, mock_fernet_fn):
        mock_f = MagicMock()
        mock_f.decrypt.return_value = b"my_secret"
        mock_fernet_fn.return_value = mock_f

        result = _decrypt_password("enc:encrypted_token")
        assert result == "my_secret"

    def test_decrypt_none_returns_none(self):
        assert _decrypt_password(None) is None

    def test_decrypt_empty_returns_empty(self):
        assert _decrypt_password("") == ""

    def test_decrypt_plain_text_legacy(self):
        """Non-encrypted password (no enc: prefix) returned as-is for backward compat."""
        assert _decrypt_password("plain_password") == "plain_password"

    @patch("app.services.connection_service._get_fernet")
    def test_decrypt_failure_returns_stored(self, mock_fernet_fn):
        mock_f = MagicMock()
        mock_f.decrypt.side_effect = RuntimeError("bad key")
        mock_fernet_fn.return_value = mock_f

        result = _decrypt_password("enc:bad_token")
        assert result == "enc:bad_token"


# =============================================================================
# _get_fernet
# =============================================================================

class TestGetFernet:

    @patch("app.services.connection_service.settings")
    def test_uses_conn_encrypt_key(self, mock_settings):
        mock_settings.get.side_effect = lambda k: "my-secret-key" if k == "CONN_ENCRYPT_KEY" else None
        f = _get_fernet()
        assert f is not None

    @patch("app.services.connection_service.settings")
    def test_uses_secret_key_fallback(self, mock_settings):
        def side_effect(k):
            if k == "CONN_ENCRYPT_KEY":
                return None
            if k == "SECRET_KEY":
                return "fallback-secret"
            return None
        mock_settings.get.side_effect = side_effect
        f = _get_fernet()
        assert f is not None

    @patch("app.services.connection_service.settings")
    @patch.dict("os.environ", {"ENVIRONMENT": "development"}, clear=False)
    def test_dev_mode_default_key_with_warning(self, mock_settings):
        mock_settings.get.return_value = None
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            f = _get_fernet()
            assert f is not None
            assert any("CONN_ENCRYPT_KEY" in str(warning.message) for warning in w)


# =============================================================================
# ConnectionService._generate_connection_id
# =============================================================================

class TestGenerateConnectionId:

    def test_format(self):
        svc = ConnectionService()
        cid = svc._generate_connection_id("My DB")
        assert cid.startswith("My_DB_")

    def test_special_chars_stripped(self):
        svc = ConnectionService()
        cid = svc._generate_connection_id("t@st!#conn")
        assert "@" not in cid.split("_")[0]

    def test_truncation(self):
        svc = ConnectionService()
        cid = svc._generate_connection_id("A" * 100)
        parts = cid.split("_")
        assert len(parts[0]) <= 30


# =============================================================================
# ConnectionService._row_to_connection
# =============================================================================

class TestRowToConnection:

    def test_full_row(self):
        svc = ConnectionService()
        row = _make_row()
        conn = svc._row_to_connection(row)
        assert conn.id == row["id"]
        assert conn.name == "Test"
        assert conn.type == "postgres"
        assert conn.description == "test desc"

    def test_none_row(self):
        svc = ConnectionService()
        result = svc._row_to_connection(None)
        assert result is None

    def test_mask_password(self):
        svc = ConnectionService()
        conn = svc._row_to_connection(_make_row(), mask_password=True)
        assert conn.password == "***"

    def test_extra_as_dict(self):
        svc = ConnectionService()
        row = _make_row(extra={"description": "from dict"})
        conn = svc._row_to_connection(row)
        assert conn.description == "from dict"

    def test_missing_extra(self):
        svc = ConnectionService()
        row = _make_row(extra=None)
        conn = svc._row_to_connection(row)
        assert conn.description == ""


# =============================================================================
# CRUD — create
# =============================================================================

class TestCreateConnection:

    @patch("app.services.connection_service.db")
    @patch("app.services.connection_service._encrypt_password", return_value="enc:xxx")
    def test_creates_connection(self, mock_enc, mock_db):
        from app.models import ConnectionCreate
        svc = ConnectionService()
        mock_db.get_connection_config.return_value = _make_row()

        data = ConnectionCreate(
            name="Test", type="postgres", host="localhost",
            port=5432, database="testdb", username="user", password="secret",
        )
        result = svc.create_connection(data)
        assert result is not None
        assert result.name == "Test"
        mock_db.create_connection.assert_called_once()


# =============================================================================
# CRUD — list
# =============================================================================

class TestListConnections:

    @patch("app.services.connection_service.db")
    def test_returns_list(self, mock_db):
        svc = ConnectionService()
        mock_db.list_connections.return_value = [{"id": "c1"}, {"id": "c2"}]
        mock_db.get_connection_config.side_effect = [_make_row(id="c1"), _make_row(id="c2")]

        result = svc.list_connections()
        assert len(result.connections) == 2

    @patch("app.services.connection_service.db")
    def test_empty_list(self, mock_db):
        svc = ConnectionService()
        mock_db.list_connections.return_value = []
        result = svc.list_connections()
        assert result.connections == []


# =============================================================================
# CRUD — get
# =============================================================================

class TestGetConnection:

    @patch("app.services.connection_service.db")
    def test_found(self, mock_db):
        svc = ConnectionService()
        mock_db.get_connection_config.return_value = _make_row()
        result = svc.get_connection("conn_id")
        assert result is not None
        assert result.name == "Test"

    @patch("app.services.connection_service.db")
    def test_not_found(self, mock_db):
        svc = ConnectionService()
        mock_db.get_connection_config.return_value = None
        result = svc.get_connection("nonexistent")
        assert result is None


# =============================================================================
# CRUD — update
# =============================================================================

class TestUpdateConnection:

    @patch("app.services.connection_service.db")
    @patch("app.services.connection_service._encrypt_password", return_value="enc:new")
    def test_update_name_and_password(self, mock_enc, mock_db):
        from app.models import ConnectionUpdate
        svc = ConnectionService()

        mock_db.get_connection_config.side_effect = [
            _make_row(),  # existence check
            _make_row(name="Updated"),  # after update
        ]

        data = ConnectionUpdate(name="Updated", password="newpass")
        result = svc.update_connection("conn_id", data)
        assert result is not None
        mock_db.update_connection.assert_called_once()
        call_kwargs = mock_db.update_connection.call_args
        assert "password" in call_kwargs[1]

    @patch("app.services.connection_service.db")
    def test_update_not_found(self, mock_db):
        from app.models import ConnectionUpdate
        svc = ConnectionService()
        mock_db.get_connection_config.return_value = None
        result = svc.update_connection("missing", ConnectionUpdate(name="x"))
        assert result is None

    @patch("app.services.connection_service.db")
    def test_update_description(self, mock_db):
        from app.models import ConnectionUpdate
        svc = ConnectionService()
        mock_db.get_connection_config.side_effect = [
            _make_row(),
            _make_row(),
        ]
        data = ConnectionUpdate(description="new desc")
        result = svc.update_connection("conn_id", data)
        assert result is not None
        mock_db.update_connection.assert_called_once()


# =============================================================================
# CRUD — delete
# =============================================================================

class TestDeleteConnection:

    @patch("app.services.connection_service.db")
    def test_delete_success(self, mock_db):
        svc = ConnectionService()
        mock_db.get_connection_config.return_value = _make_row()
        assert svc.delete_connection("conn_id") is True
        mock_db.delete_connection.assert_called_once_with("conn_id")

    @patch("app.services.connection_service.db")
    def test_delete_not_found(self, mock_db):
        svc = ConnectionService()
        mock_db.get_connection_config.return_value = None
        assert svc.delete_connection("missing") is False


# =============================================================================
# connection_exists
# =============================================================================

class TestConnectionExists:

    @patch("app.services.connection_service.db")
    def test_exists(self, mock_db):
        svc = ConnectionService()
        mock_db.get_connection_config.return_value = _make_row()
        assert svc.connection_exists("conn_id") is True

    @patch("app.services.connection_service.db")
    def test_not_exists(self, mock_db):
        svc = ConnectionService()
        mock_db.get_connection_config.return_value = None
        assert svc.connection_exists("conn_id") is False


# =============================================================================
# test_connection — dispatching
# =============================================================================

class TestTestConnection:

    @patch("app.services.connection_service.db")
    def test_not_found(self, mock_db):
        svc = ConnectionService()
        mock_db.get_connection_config.return_value = None
        result = svc.test_connection("missing")
        assert result.success is False
        assert "non trovata" in result.message

    @patch("app.services.connection_service.db")
    def test_unsupported_type(self, mock_db):
        """When get_connection returns a connection with unsupported type,
        test_connection should return an error. We mock get_connection to
        return a connection object with an unusual type."""
        svc = ConnectionService()
        mock_conn = MagicMock()
        mock_conn.type = "cassandra"
        with patch.object(svc, "get_connection", return_value=mock_conn):
            result = svc.test_connection("conn_id")
        assert result.success is False
        assert "non supportato" in result.message

    @patch("app.services.connection_service.db")
    def test_postgres_connection_error(self, mock_db):
        """test_connection handles exceptions from the DB driver."""
        svc = ConnectionService()
        mock_db.get_connection_config.return_value = _make_row(db_type="postgres")
        with patch.object(svc, "_test_postgres", return_value=MagicMock(
            success=False, message="psycopg2 non installato"
        )):
            result = svc.test_connection("conn_id")
        assert result.success is False

    @patch("app.services.connection_service.db")
    def test_sqlite_success(self, mock_db):
        svc = ConnectionService()
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        mock_db.get_connection_config.return_value = _make_row(
            db_type="sqlite", database=db_path
        )
        result = svc.test_connection("conn_id")
        assert result.success is True
        assert "SQLite" in result.message
        import os
        os.unlink(db_path)

    @patch("app.services.connection_service.db")
    def test_exception_in_test(self, mock_db):
        svc = ConnectionService()
        mock_db.get_connection_config.return_value = _make_row(db_type="postgres")
        with patch.object(svc, "_test_postgres", side_effect=RuntimeError("boom")):
            result = svc.test_connection("conn_id")
        assert result.success is False
        assert "boom" in result.message
