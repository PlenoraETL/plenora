"""
Schedules DAL — estratto da `config_db_service.py` (scope micro Batch G #9,

invariants §6 commit `61b5058`).
"""

import json
from typing import Dict, List, Optional

from app.db.pg_connection import fetchone, fetchall, execute


def get_schedule(schedule_id: str) -> Optional[Dict]:
    row = fetchone("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
    if row and row.get("config"):
        row["config"] = json.loads(row["config"])
    return row


def list_schedules(group_id: str) -> List[Dict]:
    rows = fetchall(
        "SELECT * FROM schedules WHERE group_id = ? ORDER BY created_at DESC",
        (group_id,)
    )
    for r in rows:
        if r.get("config"):
            r["config"] = json.loads(r["config"])
    return rows


def save_schedule(schedule_id: str, group_id: str, project_id: str, name: str,
                  cron_expression: str, is_enabled: bool = True, config: dict = None,
                  created_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO schedules (id, group_id, project_id, name, cron_expression, is_enabled, config, created_at, updated_at, created_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET "
        "name=EXCLUDED.name, cron_expression=EXCLUDED.cron_expression, is_enabled=EXCLUDED.is_enabled, "
        "config=EXCLUDED.config, updated_at=EXCLUDED.updated_at",
        (schedule_id, group_id, project_id, name, cron_expression,
         is_enabled, json.dumps(config or {}), now, now, created_by)
    )
    return get_schedule(schedule_id)


def update_schedule(schedule_id: str, **fields) -> Optional[Dict]:
    from datetime import datetime, timezone
    from app.db.config_db_service import _safe_set_clause
    if "config" in fields and isinstance(fields["config"], dict):
        fields["config"] = json.dumps(fields["config"])
    if "is_enabled" in fields:
        fields["is_enabled"] = bool(fields["is_enabled"])
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, vals = _safe_set_clause("schedules", fields)
    execute(f"UPDATE schedules SET {set_clause} WHERE id = ?", vals + (schedule_id,))
    return get_schedule(schedule_id)


def delete_schedule(schedule_id: str) -> bool:
    cur = execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
    return cur.rowcount > 0
