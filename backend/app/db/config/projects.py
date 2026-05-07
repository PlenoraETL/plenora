"""
Projects DAL - canonical leaf for the `projects` table.

This module owns the 6 pure-DAL CRUD operations on `projects`
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Optional, Set, cast

from app.db.pg_connection import fetchone, fetchall, execute, transaction
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ProjectRow:
    """Read model for the project list row shape."""

    id: str
    group_id: Optional[str]
    name: str
    project_type: str
    connection_id: Optional[str]
    created_at: Optional[datetime | str]
    updated_at: Optional[datetime | str]
    created_by: Optional[str]

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ProjectRow":
        return cls(
            id=cast(str, row["id"]),
            group_id=cast(Optional[str], row.get("group_id")),
            name=cast(str, row["name"]),
            project_type=cast(str, row["project_type"]),
            connection_id=cast(Optional[str], row.get("connection_id")),
            created_at=cast(Optional[datetime | str], row.get("created_at")),
            updated_at=cast(Optional[datetime | str], row.get("updated_at")),
            created_by=cast(Optional[str], row.get("created_by")),
        )


def _project_rows(rows: Iterable[Mapping[str, object]]) -> List[ProjectRow]:
    return [ProjectRow.from_mapping(row) for row in rows]


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
        # ProjectService.get_project_canvas) ha la sua difesa aggiuntiva
        try:
            row["canvas"] = json.loads(row["canvas"])
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                f"Canvas corrotto in DB per progetto {project_id}: {e}. "
                f"Fallback a canvas vuoto."
            )
            row["canvas"] = {"nodes": [], "edges": []}
    return row


def list_projects(
    group_id: Optional[str] = None,
    project_type: Optional[str] = None,
) -> List[ProjectRow]:
    """Lista progetti. group_id=None → no filter (usato dal superadmin)."""
    if group_id is None:
        if project_type:
            return _project_rows(
                fetchall(
                    "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at, created_by FROM projects WHERE project_type = ? ORDER BY updated_at DESC",
                    (project_type,)
                )
            )
        return _project_rows(
            fetchall(
                "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at, created_by FROM projects ORDER BY updated_at DESC"
            )
        )
    if project_type:
        return _project_rows(
            fetchall(
                "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at, created_by FROM projects WHERE group_id = ? AND project_type = ? ORDER BY updated_at DESC",
                (group_id, project_type)
            )
        )
    return _project_rows(
        fetchall(
            "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at, created_by FROM projects WHERE group_id = ? ORDER BY updated_at DESC",
            (group_id,)
        )
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


def list_owned_project_ids(
    project_ids: Iterable[str],
    *,
    owner_group_id: str,
) -> Set[str]:
    """Subset di `project_ids` realmente posseduti da `owner_group_id`.

    Sostituisce l'inline `SELECT id FROM projects WHERE id IN (...) AND
    group_id = ?` in `projects/router._visible_project_ids_strict`.

    Contract:
      - input vuoto → `set`;
      - `owner_group_id` falsy → `set` (caller deve pre-filtrare i
        casi `is_superadmin` / no group);
      - `), N-safe;
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
      - `group_id` falsy → `set` (caller deve gestire il caso
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


def list_projects_using_connection(connection_id: str) -> List[Dict]:
    """Projects that directly reference a database connection."""
    return fetchall(
        "SELECT id, name, project_type, group_id FROM projects WHERE connection_id = ?",
        (connection_id,),
    )


def list_projects_with_canvas_for_datasource_check() -> List[Dict]:
    """Project rows needed by datasource cascade checks."""
    return fetchall("SELECT id, name, project_type, canvas, group_id FROM projects")


# ATOMIC CANVAS-KEY UPDATE
# Moved verbatim from `app.db.config_db_service` (the 7th projects-cluster
# function the original scope-split kept in the compatibility module). Production callers
# now import from this leaf directly (`app.services.project_service`); the
# compatibility module keeps a compatibility export for back-compat with the 8 dedicated test files
# that still patch via `cfg_db.<name>` for non-production-path scenarios


def update_project_canvas_key_atomic(project_id: str, key: str, value) -> bool:
    """Aggiorna una singola chiave del canvas JSON in maniera atomica.

    Pattern: BEGIN; SELECT ... FOR UPDATE; merge; UPDATE; COMMIT.
    Il SELECT FOR UPDATE blocca il row dal momento del check fino al
    COMMIT, serializzando scritture concorrenti sullo stesso progetto
    (cross-process: worker Celery + uvicorn handler + altri worker).

    Senza questo, due write concorrenti facevano lost-update:
      - A: canvas = get; canvas["columns_metadata"] = X; update
      - B: canvas = get; canvas["nodes"] = Y; update
    Se B lo pone dopo A ma legge prima, X va perso. Ora il 2°
    SELECT FOR UPDATE aspetta il 1° COMMIT → vede il canvas gia'
    aggiornato e fa merge sopra.

    Nota: `canvas` in DB e' TEXT (JSON serializzato), non jsonb → non
    possiamo usare `jsonb_set` nativo. Il lock pessimistico e' la
    strategia piu' semplice che funziona sulla colonna attuale senza
    migration di schema.

    Ritorna True se aggiornato, False se progetto inesistente.
    Solleva sulle altre eccezioni (rollback automatico via transaction).
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT canvas FROM projects WHERE id = %s FOR UPDATE",
                (project_id,),
            )
            row = cur.fetchone()
            if not row:
                return False
            canvas_raw = row["canvas"] if isinstance(row, dict) else row[0]
            if isinstance(canvas_raw, str):
                try:
                    canvas_obj = json.loads(canvas_raw) if canvas_raw else {}
                except json.JSONDecodeError:
                    # Canvas corrotto: non vogliamo sovrascriverlo silent
                    # come documentato in project_service.get_project_canvas
                    # Solleva → rollback automatico del lock
                    raise ValueError(
                        f"Canvas corrotto per progetto {project_id}: JSON non parsabile. "
                        f"Rifiuto di sovrascrivere — ripristinare da backup."
                    )
            elif canvas_raw is None:
                canvas_obj = {}
            elif isinstance(canvas_raw, dict):
                canvas_obj = canvas_raw
            else:
                raise ValueError(
                    f"Canvas di tipo inatteso per progetto {project_id}: "
                    f"{type(canvas_raw).__name__}"
                )

            canvas_obj[key] = value
            cur.execute(
                "UPDATE projects SET canvas = %s, updated_at = %s WHERE id = %s",
                (json.dumps(canvas_obj), now, project_id),
            )
    return True
