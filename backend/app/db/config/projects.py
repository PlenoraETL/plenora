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
from typing import Dict, Iterable, List, Optional, Set

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


# ---------------------------------------------------------------------------
# Project-id lookups (BE-GO-READY-CONNECTION-ENGINE-003)
# ---------------------------------------------------------------------------
#
# Two read-only helpers extracted from `app/core/projects/router.py` and
# `app/core/imdf/router.py` so those routers can stop importing
# `app.db.pg_connection` directly. Both return `set[str]` because the
# only consumer-side need is membership testing / set algebra. Returning
# `set` (not `list`) also expresses the contract that order is not
# meaningful and that the result is deduplicated.
#
# Tabella `projects` non e' shareable (no `project_shares`): la
# visibilita' coincide col gruppo proprietario. Vedi RB.4.A.16. Questi
# helper NON conoscono il concetto di share / `is_superadmin`: il caller
# decide se applicare la lista `project_ids` (last-mile filter) oppure
# lasciar passare tutto via early-return prima di chiamare il DB.


def list_owned_project_ids(
    project_ids: Iterable[str],
    *,
    owner_group_id: str,
) -> Set[str]:
    """Subset di `project_ids` realmente posseduti da `owner_group_id`.

    Sostituisce l'inline `SELECT id FROM projects WHERE id IN (...) AND
    group_id = ?` in `projects/router._visible_project_ids_strict`.

    Contract:
      - input vuoto → `set()`;
      - `owner_group_id` falsy → `set()` (caller deve pre-filtrare i
        casi `is_superadmin` / no group);
      - batch single-query (1 `WHERE id IN (...)`), N-safe;
      - return SEMPRE `set[str]` (deduplicato, ordine non garantito).

    Args:
        project_ids: candidati da validare. Iterable; vuoto ammesso.
        owner_group_id: gruppo proprietario richiesto (kw-only per
            evitare ambiguita' di posizione con eventuali signatures
            future).

    Returns:
        Sottoinsieme di `project_ids` con `group_id == owner_group_id`.
    """
    ids = {pid for pid in (project_ids or ()) if pid}
    if not ids or not owner_group_id:
        return set()
    placeholders = ",".join(["?"] * len(ids))
    rows = fetchall(
        f"SELECT id FROM projects WHERE id IN ({placeholders}) "
        f"AND group_id = ?",
        tuple(ids) + (owner_group_id,),
    )
    return {r["id"] for r in rows}


def list_project_ids_for_group(group_id: str) -> Set[str]:
    """Tutti i `project.id` posseduti da `group_id`.

    Sostituisce l'inline `SELECT id FROM projects WHERE group_id = ?`
    in `imdf/router.list_bundles` per il calcolo
    `visible_project_ids`.

    Contract:
      - `group_id` falsy → `set()` (caller deve gestire il caso
        anonymous / superadmin separatamente — superadmin in
        particolare deve saltare questo lookup, non passargli
        `None`);
      - return SEMPRE `set[str]`.

    Args:
        group_id: identificatore del gruppo proprietario.

    Returns:
        Set di `project.id` con `group_id == group_id`. Vuoto ammesso
        (gruppo senza progetti) e indistinguibile dal `group_id` vuoto:
        il caller deve pre-validare l'input se la differenza conta.
    """
    if not group_id:
        return set()
    rows = fetchall(
        "SELECT id FROM projects WHERE group_id = ?",
        (group_id,),
    )
    return {r["id"] for r in rows}
