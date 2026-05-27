"""
Tests the arcgis crypto behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from datetime import datetime, timezone

import pytest

from app.core.features.features_service import _row_to_connection, create_connection_db
from app.infrastructure.security.crypto import decrypt, is_encrypted
from tests2.helpers.builders import build_arcgis_create


pytestmark = [pytest.mark.unit, pytest.mark.features, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


def test_create_connection_db_encrypts_password_and_client_secret_before_insert(monkeypatch):
    captured = {}

    import app.core.features.arcgis_connection_store as store

    def _execute(sql, params):
        captured["sql"] = sql
        captured["params"] = params

    def _fetchone(sql, params):
        return {
            "id": "arcgis_test",
            "group_id": "grp-1",
            "name": "ArcGIS",
            "description": "",
            "server_url": "https://example.test",
            "server_type": "arcgis_server",
            "auth_type": "oauth2",
            "username": "user-1",
            "password": captured["params"][8],
            "client_id": "client-1",
            "client_secret": captured["params"][10],
            "token_url": "",
            "created_at": "2026-04-13T00:00:00",
            "updated_at": "2026-04-13T00:00:00",
            "created_by": "u-1",
        }

    monkeypatch.setattr(store, "ensure_default_group", lambda: None)
    monkeypatch.setattr(store, "generate_id", lambda name: "arcgis_test")
    monkeypatch.setattr(store, "_execute", _execute)
    monkeypatch.setattr(store, "_fetchone", _fetchone)

    conn = create_connection_db(
        build_arcgis_create(password="pwd-123", client_secret="secret-123"),
        group_id="grp-1",
        created_by="u-1",
    )

    encrypted_password = captured["params"][8]
    encrypted_client_secret = captured["params"][10]

    assert is_encrypted(encrypted_password)
    assert is_encrypted(encrypted_client_secret)
    assert decrypt(encrypted_password) == "pwd-123"
    assert decrypt(encrypted_client_secret) == "secret-123"
    assert conn.password == "pwd-123"
    assert conn.client_secret == "secret-123"


def test_row_to_connection_decrypts_sensitive_fields_from_storage():
    row = {
        "id": "arcgis_test",
        "group_id": "grp-1",
        "name": "ArcGIS",
        "description": "",
        "server_url": "https://example.test",
        "server_type": "arcgis_server",
        "auth_type": "oauth2",
        "username": "user-1",
        "password": "pwd-123",
        "client_id": "client-1",
        "client_secret": "secret-123",
        "token_url": "",
        "created_at": "2026-04-13T00:00:00",
        "updated_at": "2026-04-13T00:00:00",
        "created_by": "u-1",
    }

    from app.infrastructure.security.crypto import encrypt

    row["password"] = encrypt(row["password"])
    row["client_secret"] = encrypt(row["client_secret"])

    conn = _row_to_connection(row)

    assert conn.password == "pwd-123"
    assert conn.client_secret == "secret-123"


def test_row_to_connection_accepts_native_pg_datetimes_from_psycopg2():
    created = datetime(2026, 4, 13, tzinfo=timezone.utc)
    updated = datetime(2026, 4, 14, tzinfo=timezone.utc)
    row = {
        "id": "arcgis_test",
        "group_id": "grp-1",
        "name": "ArcGIS",
        "description": "",
        "server_url": "https://example.test",
        "server_type": "arcgis_server",
        "auth_type": "oauth2",
        "username": "user-1",
        "password": "",
        "client_id": "client-1",
        "client_secret": "",
        "token_url": "",
        "created_at": created,
        "updated_at": updated,
        "created_by": "u-1",
    }

    conn = _row_to_connection(row)

    assert conn.created_at is created
    assert conn.updated_at is updated
