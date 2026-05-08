"""Typed row contracts for connection DB/core boundaries."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.connections import arcgis_connection_resolver
from app.core.connections import db as connection_db
from app.core.connections.models import ConnectionCreate
from app.core.connections.service import ConnectionService
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.crypto import encrypt


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_connection_db_bridge_returns_typed_row_and_legacy_conversion(monkeypatch) -> None:
    def _fetchone(sql: str, params: tuple[object, ...]) -> dict[str, object]:
        assert "FROM connections" in sql
        assert params == ("conn-1", "grp-1")
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

    monkeypatch.setattr(connection_db, "fetchone", _fetchone)

    row = connection_db.get_connection_config("conn-1", "grp-1")

    assert isinstance(row, connection_db.ConnectionConfigRow)
    assert row.port == 5432
    assert row.extra == {"description": "primary"}
    assert row.to_legacy_dict()["db_type"] == "postgres"


def test_connection_db_create_hydrates_typed_row_without_legacy_dict_return(
    monkeypatch,
) -> None:
    executed: list[tuple[str, tuple[object, ...]]] = []

    def _execute(sql: str, params: tuple[object, ...]) -> object:
        executed.append((sql, params))
        return object()

    def _fetchone(sql: str, params: tuple[object, ...]) -> dict[str, object]:
        assert "FROM connections" in sql
        assert params == ("conn-1",)
        return {
            "id": "conn-1",
            "group_id": "grp-1",
            "name": "Warehouse",
            "db_type": "postgres",
            "host": "db.example.test",
            "port": 5432,
            "database": "analytics",
            "db_schema": "public",
            "username": "reader",
            "password": "pwd-123",
            "extra": '{"description": "primary"}',
            "created_at": "2026-05-08T08:00:00+00:00",
            "updated_at": "2026-05-08T08:00:00+00:00",
            "created_by": "u-1",
        }

    monkeypatch.setattr(connection_db, "execute", _execute)
    monkeypatch.setattr(connection_db, "fetchone", _fetchone)

    row = connection_db.create_connection(
        conn_id="conn-1",
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
        created_by="u-1",
    )

    assert isinstance(row, connection_db.ConnectionConfigRow)
    assert row.extra == {"description": "primary"}
    assert executed
    assert executed[0][1][10] == '{"description": "primary"}'


def test_connection_db_update_returns_typed_row(monkeypatch) -> None:
    executed: list[tuple[str, tuple[object, ...]]] = []

    def _execute(sql: str, params: tuple[object, ...]) -> object:
        executed.append((sql, params))
        return object()

    def _fetchone(sql: str, params: tuple[object, ...]) -> dict[str, object]:
        assert "FROM connections" in sql
        assert params == ("conn-1",)
        return {
            "id": "conn-1",
            "group_id": "grp-1",
            "name": "Warehouse Updated",
            "db_type": "postgres",
            "host": "db.example.test",
            "port": 5432,
            "database": "analytics",
            "db_schema": "public",
            "username": "reader",
            "password": "pwd-123",
            "extra": '{"description": "updated"}',
            "created_at": "2026-05-08T08:00:00+00:00",
            "updated_at": "2026-05-08T09:00:00+00:00",
            "created_by": "u-1",
        }

    monkeypatch.setattr(connection_db, "execute", _execute)
    monkeypatch.setattr(connection_db, "fetchone", _fetchone)

    row = connection_db.update_connection(
        "conn-1",
        name="Warehouse Updated",
        extra={"description": "updated"},
    )

    assert isinstance(row, connection_db.ConnectionConfigRow)
    assert row.name == "Warehouse Updated"
    assert row.extra == {"description": "updated"}
    assert "UPDATE connections SET" in executed[0][0]
    assert executed[0][1][-1] == "conn-1"


def test_connection_db_list_returns_typed_rows(monkeypatch) -> None:
    def _fetchall(sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
        assert "FROM connections" in sql
        assert params == ("grp-1",)
        return [
            {
                "id": "conn-1",
                "group_id": "grp-1",
                "name": "Warehouse",
                "db_type": "postgres",
                "host": None,
                "port": None,
                "database": None,
                "db_schema": None,
                "username": None,
                "password": "pwd-123",
                "extra": {},
                "created_at": None,
                "updated_at": None,
                "created_by": None,
            }
        ]

    monkeypatch.setattr(connection_db, "fetchall", _fetchall)

    rows = connection_db.list_connection_configs("grp-1")

    assert len(rows) == 1
    assert isinstance(rows[0], connection_db.ConnectionConfigRow)
    assert rows[0].id == "conn-1"


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


def test_connection_service_create_uses_typed_create_return(monkeypatch) -> None:
    fixed_now = datetime(2026, 5, 8, tzinfo=timezone.utc)
    data = ConnectionCreate(
        name="Warehouse",
        description="primary",
        type="postgres",
        host="db.example.test",
        port=5432,
        database="analytics",
        db_schema="public",
        username="reader",
        password="pwd-123",
    )

    monkeypatch.setattr(
        ConnectionService,
        "_generate_connection_id",
        lambda self, name: "conn-1",
    )
    monkeypatch.setattr(
        connection_db,
        "create_connection",
        lambda **kwargs: connection_db.ConnectionConfigRow(
            id=kwargs["conn_id"],
            group_id=kwargs["group_id"],
            name=kwargs["name"],
            db_type=kwargs["db_type"],
            host=kwargs["host"],
            port=kwargs["port"],
            database=kwargs["database"],
            db_schema=kwargs["db_schema"],
            username=kwargs["username"],
            password=kwargs["password"],
            extra=kwargs["extra"],
            created_at=fixed_now,
            updated_at=fixed_now,
            created_by=kwargs["created_by"],
        ),
    )
    monkeypatch.setattr(
        connection_db,
        "get_connection_config",
        lambda connection_id: pytest.fail("create must use typed create return"),
    )

    conn = ConnectionService().create_connection(data, group_id="grp-1", created_by="u-1")

    assert conn.id == "conn-1"
    assert conn.description == "primary"


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
