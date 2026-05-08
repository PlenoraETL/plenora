"""Domain-owned database bridge for connection persistence.

The SQL implementation remains in the existing config DB leaf while callers
move to the connection domain boundary.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Mapping, Optional

from app.db.config.connections import delete_connection as _delete_connection
from app.db.config.safe_set import safe_set_clause
from app.db.pg_connection import execute, fetchall, fetchone


@dataclass(frozen=True)
class ConnectionConfigRow:
    """Typed database connection row consumed inside the connections domain."""

    id: str
    group_id: str
    name: str
    db_type: str
    host: str | None = None
    port: int | None = None
    database: str | None = None
    db_schema: str | None = None
    username: str | None = None
    password: str | None = None
    extra: Mapping[str, object] | None = None
    created_at: object | None = None
    updated_at: object | None = None
    created_by: str | None = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ConnectionConfigRow":
        extra = row.get("extra") or {}
        if isinstance(extra, str):
            extra = json.loads(extra)
        if not isinstance(extra, Mapping):
            extra = {}

        port_value = row.get("port")
        port = int(port_value) if port_value is not None else None

        return cls(
            id=str(row["id"]),
            group_id=str(row.get("group_id") or ""),
            name=str(row["name"]),
            db_type=str(row["db_type"]),
            host=_optional_str(row.get("host")),
            port=port,
            database=_optional_str(row.get("database")),
            db_schema=_optional_str(row.get("db_schema")),
            username=_optional_str(row.get("username")),
            password=_optional_str(row.get("password")),
            extra=extra,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            created_by=_optional_str(row.get("created_by")),
        )

    def to_legacy_dict(self) -> dict[str, object]:
        """KEEP_PUBLIC_COMPAT: dict shape for callers not yet typed."""
        return {
            "id": self.id,
            "group_id": self.group_id,
            "name": self.name,
            "db_type": self.db_type,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "db_schema": self.db_schema,
            "username": self.username,
            "password": self.password,
            "extra": dict(self.extra or {}),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
        }


@dataclass(frozen=True)
class ArcGISConnectionRow:
    """Typed ArcGIS connection row used by provider resolution."""

    id: str
    server_url: str
    server_type: str = "arcgis_server"
    auth_type: str = "token"
    username: str = ""
    password: str = ""
    client_id: str = ""
    client_secret: str = ""
    token_url: str = ""

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ArcGISConnectionRow":
        return cls(
            id=str(row["id"]),
            server_url=str(row["server_url"]),
            server_type=str(row.get("server_type") or "arcgis_server"),
            auth_type=str(row.get("auth_type") or "token"),
            username=str(row.get("username") or ""),
            password=str(row.get("password") or ""),
            client_id=str(row.get("client_id") or ""),
            client_secret=str(row.get("client_secret") or ""),
            token_url=str(row.get("token_url") or ""),
        )


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_connection_row(
    row: Mapping[str, object] | None,
) -> Optional[ConnectionConfigRow]:
    return ConnectionConfigRow.from_mapping(row) if row else None


def get_connection_config(
    conn_id: str,
    group_id: str | None = None,
) -> Optional[ConnectionConfigRow]:
    if group_id:
        row = fetchone(
            "SELECT * FROM connections WHERE id = ? AND group_id = ?",
            (conn_id, group_id),
        )
    else:
        row = fetchone("SELECT * FROM connections WHERE id = ?", (conn_id,))
    return _optional_connection_row(row)


def create_connection(
    conn_id: str,
    group_id: str,
    name: str,
    db_type: str,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    db_schema: str | None = None,
    username: str | None = None,
    password: str | None = None,
    extra: Mapping[str, object] | None = None,
    created_by: str | None = None,
) -> ConnectionConfigRow:
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO connections (id, group_id, name, db_type, host, port, database, db_schema, username, password, extra, created_at, updated_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            conn_id,
            group_id,
            name,
            db_type,
            host,
            port,
            database,
            db_schema,
            username,
            password,
            json.dumps(dict(extra or {})),
            now,
            now,
            created_by,
        ),
    )
    row = get_connection_config(conn_id)
    if row is None:
        raise RuntimeError(f"connection '{conn_id}' was inserted but could not be loaded")
    return row


def update_connection(conn_id: str, **fields: object) -> Optional[ConnectionConfigRow]:
    update_fields = dict(fields)
    if "extra" in update_fields and isinstance(update_fields["extra"], Mapping):
        update_fields["extra"] = json.dumps(dict(update_fields["extra"]))
    update_fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, vals = safe_set_clause("connections", update_fields)
    execute(f"UPDATE connections SET {set_clause} WHERE id = ?", vals + (conn_id,))
    return get_connection_config(conn_id)


def delete_connection(conn_id: str) -> bool:
    return _delete_connection(conn_id)


def list_connection_configs(group_id: str | None = None) -> list[ConnectionConfigRow]:
    if group_id is None:
        rows = fetchall("SELECT * FROM connections ORDER BY name")
    else:
        rows = fetchall(
            "SELECT * FROM connections WHERE group_id = ? ORDER BY name",
            (group_id,),
        )
    return [ConnectionConfigRow.from_mapping(row) for row in rows]


def get_arcgis_connection_row(connection_id: str) -> Optional[ArcGISConnectionRow]:
    row = fetchone("SELECT * FROM arcgis_connections WHERE id = ?", (connection_id,))
    if row is None:
        return None
    return ArcGISConnectionRow.from_mapping(row)
