"""
Projects DAL - canonical leaf for the `projects` table.

This module owns the 6 pure-DAL CRUD operations on `projects`
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Optional, Set

from .safe_set import safe_set_clause
from app.db.pg_connection import fetchone, fetchall, execute, transaction
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _required_str(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Malformed project DB row: {key} must be a string")
    return value


def _optional_str(row: Mapping[str, object], key: str) -> Optional[str]:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Malformed project DB row: {key} must be a string or null")
    return value


def _optional_datetime_or_str(
    row: Mapping[str, object],
    key: str,
) -> Optional[datetime | str]:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, (datetime, str)):
        return value
    raise ValueError(
        f"Malformed project DB row: {key} must be a datetime, string, or null"
    )


def _optional_int(row: Mapping[str, object], key: str) -> Optional[int]:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Malformed project DB row: {key} must be an int or null")
    return value


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
            id=_required_str(row, "id"),
            group_id=_optional_str(row, "group_id"),
            name=_required_str(row, "name"),
            project_type=_required_str(row, "project_type"),
            connection_id=_optional_str(row, "connection_id"),
            created_at=_optional_datetime_or_str(row, "created_at"),
            updated_at=_optional_datetime_or_str(row, "updated_at"),
            created_by=_optional_str(row, "created_by"),
        )


@dataclass(frozen=True)
class ProjectDetailRow:
    """Read model for the full project row shape."""

    id: str
    group_id: Optional[str]
    name: str
    project_type: str
    canvas: object
    connection_id: Optional[str]
    created_at: Optional[datetime | str]
    updated_at: Optional[datetime | str]
    created_by: Optional[str]
    description: Optional[str] = None
    retention_n: Optional[int] = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ProjectDetailRow":
        return cls(
            id=_required_str(row, "id"),
            group_id=_optional_str(row, "group_id"),
            name=_required_str(row, "name"),
            project_type=_required_str(row, "project_type"),
            canvas=row.get("canvas"),
            connection_id=_optional_str(row, "connection_id"),
            created_at=_optional_datetime_or_str(row, "created_at"),
            updated_at=_optional_datetime_or_str(row, "updated_at"),
            created_by=_optional_str(row, "created_by"),
            description=_optional_str(row, "description"),
            retention_n=_optional_int(row, "retention_n"),
        )

    def to_legacy_dict(self) -> Dict[str, object]:
        """Compatibility shape for callers not migrated to ProjectDetailRow."""
        row: Dict[str, object] = {
            "id": self.id,
            "group_id": self.group_id,
            "name": self.name,
            "project_type": self.project_type,
            "canvas": self.canvas,
            "connection_id": self.connection_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "description": self.description,
            "retention_n": self.retention_n,
        }
        return row


@dataclass(frozen=True)
class ProjectDependencyRow:
    """Read model for lightweight project dependency checks."""

    id: str
    name: str
    project_type: str
    group_id: Optional[str]

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ProjectDependencyRow":
        return cls(
            id=_required_str(row, "id"),
            name=_required_str(row, "name"),
            project_type=_required_str(row, "project_type"),
            group_id=_optional_str(row, "group_id"),
        )

    def to_legacy_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "project_type": self.project_type,
            "group_id": self.group_id,
        }


@dataclass(frozen=True)
class ProjectCanvasCheckRow:
    """Read model for datasource cascade checks over persisted canvases."""

    id: str
    name: str
    project_type: str
    canvas: object
    group_id: Optional[str]

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ProjectCanvasCheckRow":
        return cls(
            id=_required_str(row, "id"),
            name=_required_str(row, "name"),
            project_type=_required_str(row, "project_type"),
            canvas=row.get("canvas"),
            group_id=_optional_str(row, "group_id"),
        )

    def to_legacy_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "project_type": self.project_type,
            "canvas": self.canvas,
            "group_id": self.group_id,
        }


def _project_rows(rows: Iterable[Mapping[str, object]]) -> List[ProjectRow]:
    return [ProjectRow.from_mapping(row) for row in rows]


def _project_dependency_rows(
    rows: Iterable[Mapping[str, object]],
) -> List[ProjectDependencyRow]:
    return [ProjectDependencyRow.from_mapping(row) for row in rows]


def _project_canvas_check_rows(
    rows: Iterable[Mapping[str, object]],
) -> List[ProjectCanvasCheckRow]:
    return [ProjectCanvasCheckRow.from_mapping(row) for row in rows]


def get_project_row(
    project_id: str,
    group_id: Optional[str] = None,
) -> Optional[ProjectDetailRow]:
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
    return ProjectDetailRow.from_mapping(row) if row else None


def get_project(
    project_id: str,
    group_id: Optional[str] = None,
) -> Optional[Dict[str, object]]:
    row = get_project_row(project_id, group_id=group_id)
    return row.to_legacy_dict() if row else None


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


def create_project_row(
    project_id: str,
    group_id: str,
    name: str,
    project_type: str,
    connection_id: str = None,
    created_by: str = None,
) -> ProjectDetailRow:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    canvas = json.dumps({"nodes": [], "edges": []})
    execute(
        "INSERT INTO projects (id, group_id, name, project_type, canvas, connection_id, created_at, updated_at, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
        (project_id, group_id, name, project_type, canvas, connection_id, now, now, created_by)
    )
    row = get_project_row(project_id)
    if row is None:
        raise RuntimeError(f"Project write hydration failed for created project {project_id}")
    return row


def create_project(project_id: str, group_id: str, name: str, project_type: str,
                   connection_id: str = None, created_by: str = None) -> Dict:
    """KEEP_PUBLIC_COMPAT: legacy dict wrapper around typed project creation."""
    return create_project_row(
        project_id=project_id,
        group_id=group_id,
        name=name,
        project_type=project_type,
        connection_id=connection_id,
        created_by=created_by,
    ).to_legacy_dict()


def update_project_row(
    project_id: str,
    **fields: object,
) -> Optional[ProjectDetailRow]:
    from datetime import datetime, timezone
    if "canvas" in fields and isinstance(fields["canvas"], dict):
        fields["canvas"] = json.dumps(fields["canvas"])
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, vals = safe_set_clause("projects", fields)
    execute(f"UPDATE projects SET {set_clause} WHERE id = ?", vals + (project_id,))
    return get_project_row(project_id)


def update_project(project_id: str, **fields: object) -> Optional[Dict]:
    """KEEP_PUBLIC_COMPAT: legacy dict wrapper around typed project update."""
    row = update_project_row(project_id, **fields)
    return row.to_legacy_dict() if row else None


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


def list_project_dependency_rows_using_connection(
    connection_id: str,
) -> List[ProjectDependencyRow]:
    """Projects that directly reference a database connection."""
    return _project_dependency_rows(
        fetchall(
            "SELECT id, name, project_type, group_id FROM projects WHERE connection_id = ?",
            (connection_id,),
        )
    )


def list_projects_using_connection(connection_id: str) -> List[Dict[str, object]]:
    """KEEP_COMPAT: legacy dict wrapper for connection delete checks."""
    return [
        row.to_legacy_dict()
        for row in list_project_dependency_rows_using_connection(connection_id)
    ]


def list_project_canvas_check_rows() -> List[ProjectCanvasCheckRow]:
    """Project rows needed by datasource cascade checks."""
    return _project_canvas_check_rows(
        fetchall("SELECT id, name, project_type, canvas, group_id FROM projects")
    )


def list_projects_with_canvas_for_datasource_check() -> List[Dict[str, object]]:
    """KEEP_COMPAT: legacy dict wrapper for datasource delete checks."""
    return [row.to_legacy_dict() for row in list_project_canvas_check_rows()]


# ATOMIC CANVAS-KEY UPDATE
# Moved verbatim from `app.db.config_db_service` (the 7th projects-cluster
# function the original scope-split kept in the compatibility module). Production callers
# now import from this leaf directly (`app.core.projects.service`); the
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
