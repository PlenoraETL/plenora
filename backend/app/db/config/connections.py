"""
Connections DAL — estratto da `config_db_service.py` (scope micro Batch G #9,

invariants §6 commit `2dee7da`).
"""

import json
from typing import Dict, List, Optional

from app.db.pg_connection import fetchone, fetchall, execute


def get_connection_config(conn_id: str, group_id: str = None) -> Optional[Dict]:
    """Recupera configurazione connessione. group_id=None → no filter (backward compat)."""
    if group_id:
        row = fetchone(
            "SELECT * FROM connections WHERE id = ? AND group_id = ?",
            (conn_id, group_id)
        )
    else:
        row = fetchone("SELECT * FROM connections WHERE id = ?", (conn_id,))
    if row and row.get("extra"):
        row["extra"] = json.loads(row["extra"])
    return row


def list_connections(group_id: str) -> List[Dict]:
    return fetchall(
        "SELECT id, group_id, name, db_type, host, port, database, db_schema, username, created_at FROM connections WHERE group_id = ? ORDER BY name",
        (group_id,)
    )


def create_connection(conn_id: str, group_id: str, name: str, db_type: str,
                      host: str = None, port: int = None, database: str = None,
                      db_schema: str = None, username: str = None,
                      password: str = None, extra: dict = None,
                      created_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO connections (id, group_id, name, db_type, host, port, database, db_schema, username, password, extra, created_at, updated_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (conn_id, group_id, name, db_type, host, port, database, db_schema,
         username, password, json.dumps(extra or {}), now, now, created_by)
    )
    return get_connection_config(conn_id)


def update_connection(conn_id: str, **fields) -> Optional[Dict]:
    from datetime import datetime, timezone
    from app.db.config_db_service import _safe_set_clause
    if "extra" in fields and isinstance(fields["extra"], dict):
        fields["extra"] = json.dumps(fields["extra"])
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, vals = _safe_set_clause("connections", fields)
    execute(f"UPDATE connections SET {set_clause} WHERE id = ?", vals + (conn_id,))
    return get_connection_config(conn_id)


def delete_connection(conn_id: str) -> bool:
    cur = execute("DELETE FROM connections WHERE id = ?", (conn_id,))
    return cur.rowcount > 0
