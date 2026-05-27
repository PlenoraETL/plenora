"""Domain-owned project persistence access.

This module owns project SQL, typed project rows, and row hydration for the
projects domain. Generic DB helpers remain in ``app.db``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.pg_connection import unit_of_work
from app.db.sql_safety import safe_set_clause
from app.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "ProjectCanvasCheckRow",
    "ProjectDetailRow",
    "ProjectDependencyRow",
    "ProjectRepository",
    "ProjectRawCanvasRow",
    "ProjectRow",
    "create_project_row",
    "delete_comparison_keys",
    "delete_project",
    "execute",
    "fetchall",
    "fetchone",
    "get_project_retention_n",
    "get_project_raw_canvas_row",
    "get_project_row",
    "list_project_canvas_check_rows",
    "list_project_dependency_rows_using_connection",
    "list_owned_project_ids",
    "list_project_ids_for_group",
    "list_projects",
    "update_project_output_contract_row",
    "update_project_row",
    "update_project_canvas_key_atomic",
]


def _required_str(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Malformed project DB row: {key} must be a string")
    return value


def _optional_str(row: Mapping[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Malformed project DB row: {key} must be a string or null")
    return value


def _optional_datetime_or_str(
    row: Mapping[str, object],
    key: str,
) -> datetime | str | None:
    """Accetta sia `datetime` sia ISO `str` per i timestamp project.

    Divergenza intenzionale rispetto al `_optional_datetime` canonico di
    `tickets/db.py` che ritorna `datetime | None`. Motivo: psycopg2
    builds storici (pre-rimozione del custom `JSONB_AS_TEXT`
    typecaster) potevano ritornare `created_at`/`updated_at` come
    `str` ISO invece che come `datetime` aware, in funzione del
    binding driver. Il pattern stricter spaccava `from_mapping` su
    rows legacy.

    Per allineare a `_optional_datetime` canonico bisognerebbe
    garantire che il binding psycopg2 corrente restituisca SEMPRE
    `datetime` (verificato ok in produzione attuale ma non testato
    per ogni driver version supportata). Lasciamo `datetime | str`
    come back-compat policy esplicita; gli helper a valle che fanno
    `value.isoformat()` accettano entrambi (i `str` sono no-op se
    gia' ISO).
    """
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, (datetime, str)):
        return value
    raise ValueError(
        f"Malformed project DB row: {key} must be a datetime, string, or null"
    )


def _optional_int(row: Mapping[str, object], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Malformed project DB row: {key} must be an int or null")
    return value


def _normalize_json_column(
    row: Mapping[str, object],
    key: str,
    *,
    fallback: object,
) -> object:
    """Hydrate JSON/JSONB columns into Python objects with fallback.

    Psycopg2 può ritornare colonne JSONB come dict (typecaster attivo) o
    come str (rows persistite prima del cambio typecaster, oppure
    psycopg2 build con `JSONB_AS_TEXT`). Questo helper accetta entrambe
    e fallisce soft su payload corrotto, allineato al pattern di
    `ProjectRepository.get_project_row`. Usato dai `from_mapping`
    cosi' un consumer che istanzia `ProjectDetailRow` o
    `ProjectCanvasCheckRow` da una mapping qualsiasi (test, replay,
    list query non-detail) ottiene sempre `canvas`/`output_contract`
    come dict/None, non come str.
    """
    value = row.get(key)
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "Corrupted JSON column %s in DB row %s: %s. Falling back.",
                key, row.get("id"), exc,
            )
            return fallback
    return fallback


def _fetchone(sql: str, params: tuple = ()) -> Mapping[str, object] | None:
    return fetchone(sql, params)


def _fetchall(sql: str, params: tuple = ()) -> list[Mapping[str, object]]:
    return fetchall(sql, params)


def _execute(sql: str, params: tuple = ()):
    return execute(sql, params)


def fetchone(sql: str, params: tuple = ()) -> Mapping[str, object] | None:
    with unit_of_work() as session:
        return session.fetchone(sql, params)


def fetchall(sql: str, params: tuple = ()) -> list[Mapping[str, object]]:
    with unit_of_work() as session:
        return session.fetchall(sql, params)


def execute(sql: str, params: tuple = ()):
    with unit_of_work() as session:
        return session.execute(sql, params)


def delete_comparison_keys(canvas_id: str, node_id: str | None = None) -> None:
    """Delete comparison-key config rows owned by a project canvas."""
    project_repository.delete_comparison_keys(canvas_id, node_id=node_id)


@dataclass(frozen=True)
class ProjectRow:
    """Read model for the project list row shape.

    Pre-fix (P1 entities): `description` mancava da ProjectRow + dalle
    SELECT (era solo in `ProjectDetailRow`). Il caller
    `ProjectService.project_row_to_metadata` hardcodava `description=""`
    nella risposta `GET /projects` → tutti i progetti esposti senza
    description anche se popolata in DB. Post-fix: campo aggiunto qui +
    SELECT estese a `description` nei 4 callsite di `list_project_rows`.
    """

    id: str
    group_id: str | None
    name: str
    project_type: str
    connection_id: str | None
    created_at: datetime | str | None
    updated_at: datetime | str | None
    created_by: str | None
    description: str | None = None

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
            description=_optional_str(row, "description"),
        )


@dataclass(frozen=True)
class ProjectDetailRow:
    """Read model for the full project row shape."""

    id: str
    group_id: str | None
    name: str
    project_type: str
    canvas: object
    connection_id: str | None
    created_at: datetime | str | None
    updated_at: datetime | str | None
    created_by: str | None
    output_contract: object | None = None
    description: str | None = None
    retention_n: int | None = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ProjectDetailRow":
        return cls(
            id=_required_str(row, "id"),
            group_id=_optional_str(row, "group_id"),
            name=_required_str(row, "name"),
            project_type=_required_str(row, "project_type"),
            canvas=row.get("canvas") or '{"nodes": [], "edges": []}',
            output_contract=_normalize_json_column(
                row, "output_contract", fallback=None
            ),
            connection_id=_optional_str(row, "connection_id"),
            created_at=_optional_datetime_or_str(row, "created_at"),
            updated_at=_optional_datetime_or_str(row, "updated_at"),
            created_by=_optional_str(row, "created_by"),
            description=_optional_str(row, "description"),
            retention_n=_optional_int(row, "retention_n"),
        )


@dataclass(frozen=True)
class ProjectDependencyRow:
    """Read model for lightweight project dependency checks."""

    id: str
    name: str
    project_type: str
    group_id: str | None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ProjectDependencyRow":
        return cls(
            id=_required_str(row, "id"),
            name=_required_str(row, "name"),
            project_type=_required_str(row, "project_type"),
            group_id=_optional_str(row, "group_id"),
        )


@dataclass(frozen=True)
class ProjectCanvasCheckRow:
    """Read model for datasource cascade checks over persisted canvases."""

    id: str
    name: str
    project_type: str
    canvas: object
    group_id: str | None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ProjectCanvasCheckRow":
        return cls(
            id=_required_str(row, "id"),
            name=_required_str(row, "name"),
            project_type=_required_str(row, "project_type"),
            canvas=row.get("canvas") or '{"nodes": [], "edges": []}',
            group_id=_optional_str(row, "group_id"),
        )


@dataclass(frozen=True)
class ProjectRawCanvasRow:
    """Typed raw canvas read used by recovery/debug service paths."""

    id: str
    canvas: object

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ProjectRawCanvasRow":
        return cls(
            id=_required_str(row, "id"),
            canvas=row.get("canvas"),
        )


def _project_rows(rows: Iterable[Mapping[str, object]]) -> list[ProjectRow]:
    return [ProjectRow.from_mapping(row) for row in rows]


def _project_dependency_rows(
    rows: Iterable[Mapping[str, object]],
) -> list[ProjectDependencyRow]:
    return [ProjectDependencyRow.from_mapping(row) for row in rows]


def _project_canvas_check_rows(
    rows: Iterable[Mapping[str, object]],
) -> list[ProjectCanvasCheckRow]:
    return [ProjectCanvasCheckRow.from_mapping(row) for row in rows]


class ProjectRepository:
    """Repository boundary for project-owned SQL persistence."""

    def delete_comparison_keys(
        self,
        canvas_id: str,
        node_id: str | None = None,
    ) -> None:
        if node_id:
            _execute(
                "DELETE FROM comparison_keys WHERE canvas_id = %s AND node_id = %s",
                (canvas_id, node_id),
            )
        else:
            _execute("DELETE FROM comparison_keys WHERE canvas_id = %s", (canvas_id,))

    def get_project_row(
        self,
        project_id: str,
        group_id: str | None = None,
    ) -> ProjectDetailRow | None:
        if group_id:
            row = _fetchone(
                "SELECT * FROM projects WHERE id = %s AND group_id = %s",
                (project_id, group_id),
            )
        else:
            row = _fetchone("SELECT * FROM projects WHERE id = %s", (project_id,))
        if not row:
            return None
        # Costruisci un dict mutabile dalla mapping ritornata da psycopg2.
        # `RealDictRow` in alcune versioni e' read-only: `row["canvas"] = ...`
        # solleva TypeError silenziosamente catturato dal try/except qui
        # sotto, con conseguente fallback a canvas vuoto su dati sani.
        # Pre-fix questo bug era P1 in plenora-bug-entities (mutation
        # in-place su Mapping non garantito mutabile).
        normalized = dict(row)
        canvas = normalized.get("canvas")
        if canvas:
            # psycopg2 auto-deserializes JSONB columns into Python dicts after
            # the custom JSONB_AS_TEXT typecaster was removed by the
            # "Harden PostgreSQL boundary" commit. Older rows persisted as
            # text still come back as str. Accept both transparently.
            if isinstance(canvas, (dict, list)):
                normalized["canvas"] = canvas
            else:
                try:
                    normalized["canvas"] = json.loads(canvas)
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning(
                        "Corrupted canvas in DB for project %s: %s. "
                        "Falling back to empty canvas.",
                        project_id,
                        exc,
                    )
                    normalized["canvas"] = {"nodes": [], "edges": []}
        contract = normalized.get("output_contract")
        if contract:
            if isinstance(contract, (dict, list)):
                normalized["output_contract"] = contract
            else:
                try:
                    normalized["output_contract"] = json.loads(contract)
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning(
                        "Corrupted output_contract in DB for project %s: %s. "
                        "Falling back to null contract.",
                        project_id,
                        exc,
                    )
                    normalized["output_contract"] = None
        return ProjectDetailRow.from_mapping(normalized)

    def get_project_raw_canvas_row(
        self,
        project_id: str,
        group_id: str | None = None,
    ) -> ProjectRawCanvasRow | None:
        if group_id:
            row = _fetchone(
                "SELECT id, canvas FROM projects WHERE id = %s AND group_id = %s",
                (project_id, group_id),
            )
        else:
            row = _fetchone(
                "SELECT id, canvas FROM projects WHERE id = %s",
                (project_id,),
            )
        return ProjectRawCanvasRow.from_mapping(row) if row else None

    def list_projects(
        self,
        group_id: str | None = None,
        project_type: str | None = None,
    ) -> list[ProjectRow]:
        # P1 (2026-05-26): `description` aggiunta a TUTTI i 4 SELECT.
        # Pre-fix solo l'ultima query (group_id senza project_type) la
        # selezionava: superadmin (group_id=None) e i path con
        # project_type filter vedevano description NULL in UI per ogni
        # progetto. Helper `_PROJECT_LIST_COLUMNS` centralizza la
        # colonna list per evitare drift se aggiungiamo altri campi.
        _COLS = (
            "id, group_id, name, project_type, connection_id, "
            "created_at, updated_at, created_by, description"
        )
        if group_id is None:
            if project_type:
                return _project_rows(
                    _fetchall(
                        f"SELECT {_COLS} FROM projects "
                        "WHERE project_type = %s ORDER BY updated_at DESC",
                        (project_type,),
                    )
                )
            return _project_rows(
                _fetchall(
                    f"SELECT {_COLS} FROM projects "
                    "ORDER BY updated_at DESC"
                )
            )
        if project_type:
            return _project_rows(
                _fetchall(
                    f"SELECT {_COLS} FROM projects "
                    "WHERE group_id = %s AND project_type = %s ORDER BY updated_at DESC",
                    (group_id, project_type),
                )
            )
        return _project_rows(
            _fetchall(
                f"SELECT {_COLS} FROM projects "
                "WHERE group_id = %s ORDER BY updated_at DESC",
                (group_id,),
            )
        )

    def create_project_row(
        self,
        project_id: str,
        group_id: str,
        name: str,
        project_type: str,
        connection_id: str | None = None,
        created_by: str | None = None,
    ) -> ProjectDetailRow:
        # Pre-fix (P1 entities): `datetime.now(...).isoformat()` passava
        # un ISO string a psycopg2 invece di un `datetime` nativo. Postgres
        # accetta entrambi (cast string→timestamptz), ma il roundtrip
        # ritorna `datetime` aware, mentre se l'INSERT fosse fallito su
        # un trigger con NOW() server-side avremmo avuto mismatch fra
        # ProjectDetailRow.created_at (string atteso) e il datetime
        # caricato. Allineato al boundary B1 Pydantic (sessione precedente)
        # che ora normalizza datetime|str. Mantiene compat callsite che
        # legge ProjectRow.created_at via _optional_datetime_or_str.
        now = datetime.now(timezone.utc)
        canvas = json.dumps({"nodes": [], "edges": []})
        _execute(
            "INSERT INTO projects (id, group_id, name, project_type, canvas, "
            "output_contract, connection_id, created_at, updated_at, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                project_id,
                group_id,
                name,
                project_type,
                canvas,
                None,
                connection_id,
                now,
                now,
                created_by,
            ),
        )
        row = get_project_row(project_id)
        if row is None:
            raise RuntimeError(
                f"Project write hydration failed for created project {project_id}"
            )
        return row

    def update_project_row(
        self,
        project_id: str,
        **fields: object,
    ) -> ProjectDetailRow | None:
        if "canvas" in fields and isinstance(fields["canvas"], dict):
            fields["canvas"] = json.dumps(fields["canvas"])
        if "output_contract" in fields and isinstance(fields["output_contract"], dict):
            fields["output_contract"] = json.dumps(fields["output_contract"])
        # Pre-fix (P1 entities): isoformat string invece di datetime nativo.
        # Vedi nota in create_project_row sopra.
        fields["updated_at"] = datetime.now(timezone.utc)
        set_clause, vals = safe_set_clause("projects", fields)
        _execute(f"UPDATE projects SET {set_clause} WHERE id = %s", vals + (project_id,))
        return get_project_row(project_id)

    def update_project_output_contract_row(
        self,
        project_id: str,
        output_contract: Mapping[str, object],
    ) -> ProjectDetailRow | None:
        """Persist runtime output schema without touching editor updated_at."""
        _execute(
            "UPDATE projects SET output_contract = %s WHERE id = %s",
            (json.dumps(output_contract), project_id),
        )
        return get_project_row(project_id)

    def delete_project(self, project_id: str, group_id: str | None = None) -> bool:
        if group_id:
            cur = _execute(
                "DELETE FROM projects WHERE id = %s AND group_id = %s",
                (project_id, group_id),
            )
        else:
            cur = _execute("DELETE FROM projects WHERE id = %s", (project_id,))
        return cur.rowcount > 0

    def get_project_retention_n(self, project_id: str) -> int | None:
        row = _fetchone("SELECT retention_n FROM projects WHERE id = %s", (project_id,))
        if not row:
            return None
        return _optional_int(row, "retention_n")

    def list_owned_project_ids(
        self,
        project_ids: Iterable[str],
        *,
        owner_group_id: str,
    ) -> set[str]:
        ids = {pid for pid in (project_ids or ()) if pid}
        if not ids or not owner_group_id:
            return set()
        placeholders = ",".join(["%s"] * len(ids))
        rows = _fetchall(
            f"SELECT id FROM projects WHERE id IN ({placeholders}) AND group_id = %s",
            tuple(ids) + (owner_group_id,),
        )
        return {_required_str(row, "id") for row in rows}

    def list_project_ids_for_group(self, group_id: str) -> set[str]:
        if not group_id:
            return set()
        rows = _fetchall(
            "SELECT id FROM projects WHERE group_id = %s",
            (group_id,),
        )
        return {_required_str(row, "id") for row in rows}

    def list_project_dependency_rows_using_connection(
        self,
        connection_id: str,
    ) -> list[ProjectDependencyRow]:
        return _project_dependency_rows(
            _fetchall(
                "SELECT id, name, project_type, group_id "
                "FROM projects WHERE connection_id = %s",
                (connection_id,),
            )
        )

    def list_project_canvas_check_rows(self) -> list[ProjectCanvasCheckRow]:
        return _project_canvas_check_rows(
            _fetchall("SELECT id, name, project_type, canvas, group_id FROM projects")
        )

    def update_project_canvas_key_atomic(
        self,
        project_id: str,
        key: str,
        value: object,
    ) -> bool:
        # Pre-fix (P1 entities): isoformat string. Vedi note sopra.
        now = datetime.now(timezone.utc)
        with unit_of_work() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT canvas FROM projects WHERE id = %s FOR UPDATE",
                    (project_id,),
                )
                row = cur.fetchone()
                if not row:
                    return False
                # Accesso uniforme via mapping: psycopg2 e' configurato
                # con RealDictCursor di default (vedi pg_connection).
                # Il fallback posizionale `row[0]` era fragile rispetto
                # a cambio cursor factory o aggiunta di colonne alla
                # SELECT. Pre-fix bug P1 in plenora-bug-entities.
                canvas_raw = row["canvas"]
                if isinstance(canvas_raw, str):
                    try:
                        canvas_obj = json.loads(canvas_raw) if canvas_raw else {}
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"Corrupted canvas for project {project_id}: JSON is not "
                            "parseable. Refusing to overwrite it."
                        ) from exc
                elif canvas_raw is None:
                    canvas_obj = {}
                elif isinstance(canvas_raw, dict):
                    canvas_obj = canvas_raw
                else:
                    raise ValueError(
                        f"Unexpected canvas type for project {project_id}: "
                        f"{type(canvas_raw).__name__}"
                    )

                canvas_obj[key] = value
                cur.execute(
                    "UPDATE projects SET canvas = %s, updated_at = %s WHERE id = %s",
                    (json.dumps(canvas_obj), now, project_id),
                )
        return True


project_repository = ProjectRepository()


def get_project_row(
    project_id: str,
    group_id: str | None = None,
) -> ProjectDetailRow | None:
    return project_repository.get_project_row(project_id, group_id=group_id)


def get_project_raw_canvas_row(
    project_id: str,
    group_id: str | None = None,
) -> ProjectRawCanvasRow | None:
    return project_repository.get_project_raw_canvas_row(
        project_id,
        group_id=group_id,
    )


def list_projects(
    group_id: str | None = None,
    project_type: str | None = None,
) -> list[ProjectRow]:
    return project_repository.list_projects(group_id=group_id, project_type=project_type)


def create_project_row(
    project_id: str,
    group_id: str,
    name: str,
    project_type: str,
    connection_id: str | None = None,
    created_by: str | None = None,
) -> ProjectDetailRow:
    return project_repository.create_project_row(
        project_id=project_id,
        group_id=group_id,
        name=name,
        project_type=project_type,
        connection_id=connection_id,
        created_by=created_by,
    )


def update_project_row(
    project_id: str,
    **fields: object,
) -> ProjectDetailRow | None:
    return project_repository.update_project_row(project_id, **fields)


def update_project_output_contract_row(
    project_id: str,
    output_contract: Mapping[str, object],
) -> ProjectDetailRow | None:
    return project_repository.update_project_output_contract_row(
        project_id,
        output_contract,
    )


def delete_project(project_id: str, group_id: str | None = None) -> bool:
    return project_repository.delete_project(project_id, group_id=group_id)


def get_project_retention_n(project_id: str) -> int | None:
    return project_repository.get_project_retention_n(project_id)


def list_owned_project_ids(
    project_ids: Iterable[str],
    *,
    owner_group_id: str,
) -> set[str]:
    return project_repository.list_owned_project_ids(
        project_ids,
        owner_group_id=owner_group_id,
    )


def list_project_ids_for_group(group_id: str) -> set[str]:
    return project_repository.list_project_ids_for_group(group_id)


def list_project_dependency_rows_using_connection(
    connection_id: str,
) -> list[ProjectDependencyRow]:
    return project_repository.list_project_dependency_rows_using_connection(
        connection_id
    )


def list_project_canvas_check_rows() -> list[ProjectCanvasCheckRow]:
    return project_repository.list_project_canvas_check_rows()


def update_project_canvas_key_atomic(project_id: str, key: str, value: object) -> bool:
    """Atomically merge one key into a project's persisted canvas."""
    return project_repository.update_project_canvas_key_atomic(project_id, key, value)
