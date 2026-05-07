"""
Api Connections DAL helpers.

Keeps config-table persistence isolated behind a narrow database module.
"""

import json
from typing import Dict, List, Optional

from app.db.pg_connection import fetchone, fetchall, execute


def get_api_connection(api_id: str, group_id: str = None) -> Optional[Dict]:
    """Recupera API connection. group_id=None → no filter (backward compat)."""
    if group_id:
        row = fetchone(
            "SELECT * FROM api_connections WHERE id = ? AND group_id = ?",
            (api_id, group_id)
        )
    else:
        row = fetchone("SELECT * FROM api_connections WHERE id = ?", (api_id,))
    if row and row.get("config"):
        row["config"] = json.loads(row["config"])
    return row


def list_api_connections(group_id: str) -> List[Dict]:
    rows = fetchall(
        "SELECT * FROM api_connections WHERE group_id = ? ORDER BY created_at DESC",
        (group_id,)
    )
    for row in rows:
        if row.get("config"):
            row["config"] = json.loads(row["config"])
    return rows


def save_api_connection(api_id: str, group_id: str, config: dict,
                        created_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO api_connections (id, group_id, config, created_at, updated_at, created_by) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET "
        "config=EXCLUDED.config, updated_at=EXCLUDED.updated_at",
        (api_id, group_id, json.dumps(config), now, now, created_by)
    )
    return get_api_connection(api_id)


def create_api_connection(group_id: str, config: dict, created_by: str = None) -> Dict:
    """Alias per save_api_connection."""
    api_id = config.get("id")
    if not api_id:
        import uuid as _uuid
        api_id = f"api_{_uuid.uuid4().hex[:12]}"
        config["id"] = api_id
    return save_api_connection(api_id, group_id, config, created_by=created_by)


def update_api_connection(api_id: str, config: dict) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "UPDATE api_connections SET config = ?, updated_at = ? WHERE id = ?",
        (json.dumps(config), now, api_id)
    )


def delete_api_connection(api_id: str) -> bool:
    cur = execute("DELETE FROM api_connections WHERE id = ?", (api_id,))
    return cur.rowcount > 0
