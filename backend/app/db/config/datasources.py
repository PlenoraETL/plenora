"""
Datasources DAL — estratto da `config_db_service.py` (scope micro Batch G #9,
invariants §6 commit `86ea1c5`).

Cluster `datasources` (5 funzioni): CRUD completo sulla tabella `datasources` +
helper `_safe_set_clause` importato lazy dal facade per rompere ciclo
`config_db_service` ↔ `config/datasources`.
"""

import json
from typing import Dict, List, Optional

from app.db.pg_connection import fetchone, fetchall, execute


def get_datasource(ds_id: str, group_id: str = None) -> Optional[Dict]:
    """Recupera datasource. group_id=None → no filter (backward compat)."""
    if group_id:
        row = fetchone(
            "SELECT * FROM datasources WHERE id = ? AND group_id = ?",
            (ds_id, group_id)
        )
    else:
        row = fetchone("SELECT * FROM datasources WHERE id = ?", (ds_id,))
    if row and row.get("config"):
        row["config"] = json.loads(row["config"])
    return row


def list_datasources(group_id: str) -> List[Dict]:
    return fetchall(
        "SELECT id, group_id, name, ds_type, created_at, created_by FROM datasources WHERE group_id = ? ORDER BY name",
        (group_id,)
    )


def save_datasource(ds_id: str, group_id: str, name: str, ds_type: str, config: dict,
                    created_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO datasources (id, group_id, name, ds_type, config, created_at, updated_at, created_by) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET "
        "name=EXCLUDED.name, ds_type=EXCLUDED.ds_type, config=EXCLUDED.config, updated_at=EXCLUDED.updated_at",
        (ds_id, group_id, name, ds_type, json.dumps(config), now, now, created_by)
    )
    return get_datasource(ds_id)


def update_datasource(ds_id: str, **kwargs) -> None:
    """Aggiorna i campi consentiti di una datasource.

    Solo i campi in `_ALLOWED_UPDATE_FIELDS["datasources"]` sono accettati.
    Solleva ValueError se un campo non consentito viene passato (anti-injection
    e protezione da escalation: nessuno deve poter aggiornare group_id, id,
    created_by, ecc.).
    """
    from datetime import datetime, timezone
    # Lazy import: `_safe_set_clause` è helper private cross-cluster nel facade
    # `config_db_service` (usato da 5 cluster: users/projects/connections/
    # datasources/schedules). Import locale rompe ciclo facade ↔ config/datasources
    # senza spostare il helper (violerebbe zero call site touched sugli altri 4
    # cluster). Vedi invariants §6 commit `86ea1c5`.
    from app.db.config_db_service import _safe_set_clause
    if not kwargs:
        return
    # Serializza il blob `config` se passato come dict (convenienza per i caller)
    fields = dict(kwargs)
    if "config" in fields and isinstance(fields["config"], dict):
        fields["config"] = json.dumps(fields["config"])
    # Forza updated_at lato server
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, params = _safe_set_clause("datasources", fields)
    execute(f"UPDATE datasources SET {set_clause} WHERE id = ?", params + (ds_id,))


def delete_datasource(ds_id: str) -> bool:
    cur = execute("DELETE FROM datasources WHERE id = ?", (ds_id,))
    return cur.rowcount > 0
