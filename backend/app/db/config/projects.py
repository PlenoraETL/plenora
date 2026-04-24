"""
Projects DAL pure — estratto da `config_db_service.py` (scope micro Batch G #9
ottava riapertura — governance autonoma scope-split transazionale, invariants
§6 commit `7a00c1f`).

Cluster `projects` (6 funzioni pure DAL): CRUD sulla tabella `projects` senza
contract transazionale/atomic. La 7° funzione del cluster originale,
`update_project_canvas_key_atomic` (62 LOC, `transaction()` + SELECT FOR UPDATE
+ merge JSON), resta **verbatim nel facade** `config_db_service` per
preservare contract atomic e 8 file test dedicati (unit/contract/integration DB
reale/shared) — vedi invariants §6 commit `7a00c1f`.

Consumer principali (tutti invariati per costruzione via re-export 1:1):
`app/services/project_service.py` (10+ usi `db.*`), `workers/tasks/
{carica,pipeline}_task.py`, `app/utils/{result_store,debug_payload_builder}.py`,
`app/services/schema_inference.py`. Test patch-target via `real_db.get_project`,
`cfg_db.get_project`, `app.services.project_service.db.get_project` —
preservati per identità attributo modulo.

Helper `_safe_set_clause` importato lazy dal facade in `update_project` per
rompere ciclo `config_db_service` ↔ `config/projects` e preservare
zero-call-site-touched sugli altri cluster che lo condividono (users/schedules
+ datasources/connections via lazy — pattern 7° uso).
"""

import json
from typing import Dict, List, Optional

from app.db.pg_connection import fetchone, fetchall, execute
from app.utils.logger import get_logger

logger = get_logger(__name__)


def get_project(project_id: str, group_id: str = None) -> Optional[Dict]:
    if group_id:
        row = fetchone(
            "SELECT * FROM projects WHERE id = ? AND group_id = ?",
            (project_id, group_id)
        )
    else:
        row = fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    if row and row.get("canvas"):
        # Su canvas corrotto: log warning + fallback a dict vuoto. Il caller
        # (ProjectService.get_project_canvas) ha la sua difesa aggiuntiva.
        try:
            row["canvas"] = json.loads(row["canvas"])
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                f"Canvas corrotto in DB per progetto {project_id}: {e}. "
                f"Fallback a canvas vuoto."
            )
            row["canvas"] = {"nodes": [], "edges": []}
    return row


def list_projects(group_id: Optional[str] = None, project_type: str = None) -> List[Dict]:
    """Lista progetti. group_id=None → no filter (usato dal superadmin)."""
    if group_id is None:
        if project_type:
            return fetchall(
                "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at, created_by FROM projects WHERE project_type = ? ORDER BY updated_at DESC",
                (project_type,)
            )
        return fetchall(
            "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at, created_by FROM projects ORDER BY updated_at DESC"
        )
    if project_type:
        return fetchall(
            "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at, created_by FROM projects WHERE group_id = ? AND project_type = ? ORDER BY updated_at DESC",
            (group_id, project_type)
        )
    return fetchall(
        "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at, created_by FROM projects WHERE group_id = ? ORDER BY updated_at DESC",
        (group_id,)
    )


def create_project(project_id: str, group_id: str, name: str, project_type: str,
                   connection_id: str = None, created_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    canvas = json.dumps({"nodes": [], "edges": []})
    execute(
        "INSERT INTO projects (id, group_id, name, project_type, canvas, connection_id, created_at, updated_at, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
        (project_id, group_id, name, project_type, canvas, connection_id, now, now, created_by)
    )
    return get_project(project_id)


def update_project(project_id: str, **fields) -> Optional[Dict]:
    from datetime import datetime, timezone
    # Lazy import: `_safe_set_clause` è helper private cross-cluster nel facade
    # `config_db_service` (usato da users/schedules + datasources/connections/
    # projects via lazy). Import locale rompe ciclo facade ↔ config/projects
    # senza spostare il helper (violerebbe zero call site touched sugli altri
    # cluster). Vedi invariants §6 commit `7a00c1f`.
    from app.db.config_db_service import _safe_set_clause
    if "canvas" in fields and isinstance(fields["canvas"], dict):
        fields["canvas"] = json.dumps(fields["canvas"])
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, vals = _safe_set_clause("projects", fields)
    execute(f"UPDATE projects SET {set_clause} WHERE id = ?", vals + (project_id,))
    return get_project(project_id)


def delete_project(project_id: str, group_id: str = None) -> bool:
    if group_id:
        cur = execute("DELETE FROM projects WHERE id = ? AND group_id = ?", (project_id, group_id))
    else:
        cur = execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return cur.rowcount > 0


def get_project_retention_n(project_id: str) -> Optional[int]:
    """Ritorna retention_n del canvas (None = usa default globale)."""
    row = fetchone("SELECT retention_n FROM projects WHERE id = ?", (project_id,))
    return row["retention_n"] if row else None
