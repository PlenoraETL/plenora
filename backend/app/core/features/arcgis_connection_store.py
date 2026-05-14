"""
Persistence boundary for ArcGIS connection CRUD.

This module owns PostgreSQL access for ``arcgis_connections`` so the feature
service can keep its public API without importing the concrete DB adapter.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import app.db.pg_connection as db
from app.core.features.models import ArcGISConnection, ArcGISCreate, ArcGISList, ArcGISUpdate
from app.infrastructure.security.crypto import decrypt as _decrypt
from app.infrastructure.security.crypto import encrypt as _encrypt
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_GROUP = "default"

_SENSITIVE_FIELDS = ("password", "client_secret")


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"expected datetime or ISO string, got {type(value).__name__}")


def ensure_default_group() -> None:
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO groups (id, name, description, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
        (DEFAULT_GROUP, "Default", "Gruppo default", now, now),
    )


def row_to_connection(row: dict) -> ArcGISConnection:
    return ArcGISConnection(
        id=row["id"],
        group_id=row.get("group_id") or DEFAULT_GROUP,
        name=row["name"],
        description=row.get("description") or "",
        server_url=row["server_url"],
        server_type=row.get("server_type") or "arcgis_server",
        auth_type=row.get("auth_type") or "token",
        username=row.get("username") or "",
        password=_decrypt(row.get("password") or "") or "",
        client_id=row.get("client_id") or "",
        client_secret=_decrypt(row.get("client_secret") or "") or "",
        token_url=row.get("token_url") or None,
        created_at=_as_datetime(row["created_at"]),
        updated_at=_as_datetime(row["updated_at"]),
        created_by=row.get("created_by"),
    )


def generate_id(name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_uuid = str(uuid.uuid4())[:8]
    safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_")).strip()
    safe_name = safe_name.replace(" ", "_")[:30]
    return f"arcgis_{safe_name}_{timestamp}_{short_uuid}"


def create_connection(
    data: ArcGISCreate,
    group_id: str = DEFAULT_GROUP,
    created_by: Optional[str] = None,
) -> ArcGISConnection:
    ensure_default_group()
    connection_id = generate_id(data.name)
    now = datetime.now(timezone.utc).isoformat()

    server_type = data.server_type
    if not server_type:
        url = (data.server_url or "").lower()
        if "arcgis.com" in url:
            server_type = "arcgis_online"
        elif "portal" in url or "enterprise" in url:
            server_type = "arcgis_enterprise"
        else:
            server_type = "arcgis_server"

    enc_password = _encrypt(data.password or "") or ""
    enc_client_secret = _encrypt(data.client_secret or "") or ""

    db.execute(
        """
        INSERT INTO arcgis_connections
        (id, group_id, name, description, server_url, server_type, auth_type,
         username, password, client_id, client_secret, token_url, created_at, updated_at, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            connection_id,
            group_id,
            data.name,
            data.description or "",
            data.server_url,
            server_type,
            data.auth_type,
            data.username or "",
            enc_password,
            data.client_id or "",
            enc_client_secret,
            data.token_url or "",
            now,
            now,
            created_by,
        ),
    )

    logger.info(f"Connessione ArcGIS creata: {connection_id} (group {group_id})", op="create")
    row = db.fetchone("SELECT * FROM arcgis_connections WHERE id = %s", (connection_id,))
    return row_to_connection(dict(row))


def list_connections(group_id: Optional[str] = None) -> ArcGISList:
    if group_id is None:
        rows = db.fetchall("SELECT * FROM arcgis_connections ORDER BY updated_at DESC")
    else:
        rows = db.fetchall(
            "SELECT * FROM arcgis_connections WHERE group_id = %s ORDER BY updated_at DESC",
            (group_id,),
        )
    return ArcGISList(connections=[row_to_connection(dict(row)) for row in rows])


def get_connection(connection_id: str) -> Optional[ArcGISConnection]:
    row = db.fetchone("SELECT * FROM arcgis_connections WHERE id = %s", (connection_id,))
    return row_to_connection(dict(row)) if row else None


def update_connection(connection_id: str, data: ArcGISUpdate) -> Optional[ArcGISConnection]:
    if not db.fetchone("SELECT id FROM arcgis_connections WHERE id = %s", (connection_id,)):
        return None

    update_data = data.model_dump(exclude_unset=True)
    now = datetime.now(timezone.utc).isoformat()

    for sensitive_field in list(update_data.keys()):
        if sensitive_field in _SENSITIVE_FIELDS and update_data.get(sensitive_field) in (None, "", "***"):
            del update_data[sensitive_field]

    field_map = {
        "name": "name",
        "description": "description",
        "server_url": "server_url",
        "server_type": "server_type",
        "auth_type": "auth_type",
        "username": "username",
        "password": "password",
        "client_id": "client_id",
        "client_secret": "client_secret",
        "token_url": "token_url",
    }

    sets, params = [], []
    for payload_field, column_name in field_map.items():
        if payload_field in update_data:
            sets.append(f"{column_name} = %s")
            field_value = update_data[payload_field]
            if payload_field in _SENSITIVE_FIELDS and isinstance(field_value, str) and field_value:
                field_value = _encrypt(field_value)
            params.append(field_value)

    if not sets:
        return get_connection(connection_id)

    sets.append("updated_at = %s")
    params.extend([now, connection_id])
    db.execute(f"UPDATE arcgis_connections SET {', '.join(sets)} WHERE id = %s", params)
    return get_connection(connection_id)


def delete_connection(connection_id: str) -> bool:
    if not db.fetchone("SELECT id FROM arcgis_connections WHERE id = %s", (connection_id,)):
        return False
    db.execute("DELETE FROM arcgis_connections WHERE id = %s", (connection_id,))
    logger.info(f"Connessione ArcGIS eliminata: {connection_id}", op="delete")
    return True
