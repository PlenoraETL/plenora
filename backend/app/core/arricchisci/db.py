"""
Domain-owned API connection persistence for arricchisci.

The public helpers intentionally keep the legacy dict shape consumed by the
arricchisci service/router while SQL ownership lives in this domain boundary.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.pg_connection import execute, fetchall, fetchone


@dataclass(frozen=True)
class ApiConnectionRow:
    id: str
    group_id: str
    config: dict[str, Any]
    created_at: str | None = None
    updated_at: str | None = None
    created_by: str | None = None

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "group_id": self.group_id,
            "config": self.config,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
        }


def _decode_config(raw_config: Any) -> dict[str, Any]:
    if raw_config is None:
        return {}
    if isinstance(raw_config, dict):
        return raw_config
    if isinstance(raw_config, str):
        return json.loads(raw_config)
    return {}


def _row_from_record(record: dict[str, Any]) -> ApiConnectionRow:
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
        record = fetchone(
            "SELECT * FROM api_connections WHERE id = ? AND group_id = ?",
            (api_id, group_id),
        )
    else:
        record = fetchone("SELECT * FROM api_connections WHERE id = ?", (api_id,))
    if not record:
        return None
    return _row_from_record(record)


def list_api_connection_rows(group_id: str | None = None) -> list[ApiConnectionRow]:
    if group_id is None:
        records = fetchall("SELECT * FROM api_connections ORDER BY created_at DESC")
    else:
        records = fetchall(
            "SELECT * FROM api_connections WHERE group_id = ? ORDER BY created_at DESC",
            (group_id,),
        )
    return [_row_from_record(record) for record in records]


def save_api_connection_row(
    api_id: str,
    group_id: str,
    config: dict[str, Any],
    created_by: str | None = None,
) -> ApiConnectionRow:
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO api_connections (id, group_id, config, created_at, updated_at, created_by) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET "
        "config=EXCLUDED.config, updated_at=EXCLUDED.updated_at",
        (api_id, group_id, json.dumps(config), now, now, created_by),
    )
    row = get_api_connection_row(api_id)
    if row is None:
        raise RuntimeError(f"API connection '{api_id}' was not persisted")
    return row


def get_api_connection(api_id: str, group_id: str | None = None) -> Optional[Dict]:
    row = get_api_connection_row(api_id, group_id)
    return row.to_legacy_dict() if row is not None else None


def list_api_connections(group_id: str | None = None) -> List[Dict]:
    return [row.to_legacy_dict() for row in list_api_connection_rows(group_id)]


def save_api_connection(
    api_id: str,
    group_id: str,
    config: dict,
    created_by: str | None = None,
) -> Dict:
    return save_api_connection_row(
        api_id=api_id,
        group_id=group_id,
        config=config,
        created_by=created_by,
    ).to_legacy_dict()


def create_api_connection(
    group_id: str,
    config: dict,
    created_by: str | None = None,
) -> Dict:
    api_id = config.get("id")
    if not api_id:
        api_id = f"api_{uuid.uuid4().hex[:12]}"
        config["id"] = api_id
    return save_api_connection(api_id, group_id, config, created_by=created_by)


def update_api_connection(api_id: str, config: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "UPDATE api_connections SET config = ?, updated_at = ? WHERE id = ?",
        (json.dumps(config), now, api_id),
    )


def delete_api_connection(api_id: str) -> bool:
    cur = execute("DELETE FROM api_connections WHERE id = ?", (api_id,))
    return cur.rowcount > 0
