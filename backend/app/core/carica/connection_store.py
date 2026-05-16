"""Store locale per il fallback connection lookup di CARICA."""

from __future__ import annotations

from app.core.connections.service import _decrypt_password
from app.db.pg_connection import unit_of_work
from app.shared.contracts.payloads import JsonObject


def fetch_connection_config(connection_id: str) -> JsonObject | None:
    with unit_of_work() as session:
        row = session.fetchone("SELECT * FROM connections WHERE id = %s", (connection_id,))
    if not row:
        return None

    record = dict(row)
    return {
        "id": record["id"],
        "name": record["name"],
        "type": record["db_type"],
        "host": record.get("host", ""),
        "port": record.get("port"),
        "database": record.get("database", ""),
        "db_schema": record.get("db_schema", ""),
        "username": record.get("username", ""),
        "password": _decrypt_password(record.get("password", "")),
    }
