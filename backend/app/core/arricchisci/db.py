"""Domain-owned API connection persistence for arricchisci."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.db.pg_connection import unit_of_work
from app.core.arricchisci.types import JsonObject


def _fetchone(sql: str, params: tuple = ()) -> JsonObject | None:
    with unit_of_work() as session:
        return session.fetchone(sql, params)


def _fetchall(sql: str, params: tuple = ()) -> list[JsonObject]:
    with unit_of_work() as session:
        return session.fetchall(sql, params)


def _execute(sql: str, params: tuple = ()):
    with unit_of_work() as session:
        return session.execute(sql, params)


@dataclass(frozen=True)
class ApiConnectionRow:
    id: str
    group_id: str
    config: JsonObject
    created_at: str | None = None
    updated_at: str | None = None
    created_by: str | None = None


def _decode_config(raw_config: Any) -> JsonObject:
    if raw_config is None:
        return {}
    if isinstance(raw_config, dict):
        return raw_config
    if isinstance(raw_config, str):
        return json.loads(raw_config)
    return {}


def _row_from_record(record: JsonObject) -> ApiConnectionRow:
    return ApiConnectionRow(
        id=str(record.get("id") or ""),
        group_id=str(record.get("group_id") or ""),
        config=_decode_config(record.get("config")),
        created_at=record.get("created_at"),
        updated_at=record.get("updated_at"),
        created_by=record.get("created_by"),
    )


def get_api_connection_row(
    api_id: str,
    group_id: str | None = None,
) -> ApiConnectionRow | None:
    if group_id:
        record = _fetchone(
            "SELECT * FROM api_connections WHERE id = %s AND group_id = %s",
            (api_id, group_id),
        )
    else:
        record = _fetchone("SELECT * FROM api_connections WHERE id = %s", (api_id,))
    if not record:
        return None
    return _row_from_record(record)


def list_api_connection_rows(group_id: str | None = None) -> list[ApiConnectionRow]:
    if group_id is None:
        records = _fetchall("SELECT * FROM api_connections ORDER BY created_at DESC")
    else:
        records = _fetchall(
            "SELECT * FROM api_connections WHERE group_id = %s ORDER BY created_at DESC",
            (group_id,),
        )
    return [_row_from_record(record) for record in records]


def save_api_connection_row(
    api_id: str,
    group_id: str,
    config: JsonObject,
    created_by: str | None = None,
) -> ApiConnectionRow:
    now = datetime.now(timezone.utc).isoformat()
    _execute(
        "INSERT INTO api_connections (id, group_id, config, created_at, updated_at, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET "
        "config=EXCLUDED.config, updated_at=EXCLUDED.updated_at",
        (api_id, group_id, json.dumps(config), now, now, created_by),
    )
    row = get_api_connection_row(api_id)
    if row is None:
        raise RuntimeError(f"API connection '{api_id}' was not persisted")
    return row


def update_api_connection_row(api_id: str, config: JsonObject) -> ApiConnectionRow | None:
    now = datetime.now(timezone.utc).isoformat()
    _execute(
        "UPDATE api_connections SET config = %s, updated_at = %s WHERE id = %s",
        (json.dumps(config), now, api_id),
    )
    return get_api_connection_row(api_id)


def delete_api_connection(api_id: str) -> bool:
    cur = _execute("DELETE FROM api_connections WHERE id = %s", (api_id,))
    return cur.rowcount > 0
