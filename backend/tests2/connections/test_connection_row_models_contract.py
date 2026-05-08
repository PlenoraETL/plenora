"""Typed row contracts for connection DB/core boundaries."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.connections import arcgis_connection_resolver
from app.core.connections import db as connection_db
from app.core.connections.service import ConnectionService
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.crypto import encrypt


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_connection_db_bridge_returns_typed_row_and_legacy_conversion(monkeypatch) -> None:
    def _legacy_config_leaf(conn_id: str, group_id: str | None = None) -> dict[str, object]:
        assert conn_id == "conn-1"
        assert group_id == "grp-1"
        return {
            "id": "conn-1",
            "group_id": "grp-1",
            "name": "Warehouse",
            "db_type": "postgres",
            "host": "db.example.test",
            "port": "5432",
            "database": "analytics",
            "db_schema": "public",
            "username": "reader",
            "password": "pwd-123",
            "extra": '{"description": "primary"}',
            "created_at": "2026-05-08T08:00:00+00:00",
            "updated_at": "2026-05-08T08:00:00+00:00",
            "created_by": "u-1",
        }

    monkeypatch.setattr(connection_db, "_get_connection_config", _legacy_config_leaf)

    row = connection_db.get_connection_config("conn-1", "grp-1")

    assert isinstance(row, connection_db.ConnectionConfigRow)
    assert row.port == 5432
    assert row.extra == {"description": "primary"}
    assert row.to_legacy_dict()["db_type"] == "postgres"


def test_connection_service_consumes_typed_connection_row(monkeypatch) -> None:
    fixed_now = datetime(2026, 5, 8, tzinfo=timezone.utc)
    row = connection_db.ConnectionConfigRow(
        id="conn-1",
        group_id="grp-1",
        name="Warehouse",
        db_type="postgres",
        host="db.example.test",
        port=5432,
        database="analytics",
        db_schema="public",
        username="reader",
        password="pwd-123",
        extra={"description": "primary"},
        created_at=fixed_now,
        updated_at=fixed_now,
        created_by="u-1",
    )
    monkeypatch.setattr(
        connection_db,
        "get_connection_config",
        lambda connection_id: row if connection_id == "conn-1" else None,
    )

    conn = ConnectionService().get_connection("conn-1")

    assert conn is not None
    assert conn.id == "conn-1"
    assert conn.description == "primary"
    assert conn.password == "pwd-123"


def test_arcgis_resolver_consumes_typed_row_and_returns_provider_contract(monkeypatch) -> None:
    provider = object()
    row = connection_db.ArcGISConnectionRow(
        id="arcgis-1",
        server_url="https://example.test/arcgis",
        server_type="arcgis_server",
        auth_type="oauth2",
        username="publisher",
        password=encrypt("pwd-123") or "",
        client_id="client-1",
        client_secret=encrypt("secret-123") or "",
        token_url="https://example.test/token",
    )

    monkeypatch.setattr(
        arcgis_connection_resolver.connection_db,
        "get_arcgis_connection_row",
        lambda connection_id: row if connection_id == "arcgis-1" else None,
    )
    monkeypatch.setattr(
        arcgis_connection_resolver,
        "create_arcgis_provider",
        lambda: provider,
    )

    resolved = arcgis_connection_resolver.resolve_arcgis_connection("arcgis-1")

    assert resolved is not None
    conn_info, resolved_provider = resolved
    assert isinstance(conn_info, ArcGISConnectionInfo)
    assert conn_info.password == "pwd-123"
    assert conn_info.client_secret == "secret-123"
    assert resolved_provider is provider


def test_arcgis_resolver_returns_none_for_missing_typed_row(monkeypatch) -> None:
    monkeypatch.setattr(
        arcgis_connection_resolver.connection_db,
        "get_arcgis_connection_row",
        lambda connection_id: None,
    )

    assert arcgis_connection_resolver.resolve_arcgis_connection("missing") is None
