"""Domain-owned project persistence access.

This module owns project SQL, typed project rows, and row hydration for the
projects domain. Generic DB helpers remain in ``app.db``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.pg_connection import execute, fetchall, fetchone, transaction
from app.db.sql_safety import safe_set_clause
from app.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "ProjectCanvasCheckRow",
    "ProjectDetailRow",
    "ProjectDependencyRow",
    "ProjectRepository",
    "ProjectRow",
    "create_project_row",
    "delete_comparison_keys",
    "delete_project",
    "get_project_retention_n",
    "get_project_row",
    "list_project_canvas_check_rows",
    "list_project_dependency_rows_using_connection",
    "list_owned_project_ids",
    "list_project_ids_for_group",
    "list_projects",
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


def delete_comparison_keys(canvas_id: str, node_id: str | None = None) -> None:
    """Delete comparison-key config rows owned by a project canvas."""
    project_repository.delete_comparison_keys(canvas_id, node_id=node_id)


@dataclass(frozen=True)
class ProjectRow:
    """Read model for the project list row shape."""

    id: str
    group_id: str | None
    name: str
    project_type: str
    connection_id: str | None
    created_at: datetime | str | None
    updated_at: datetime | str | None
    created_by: str | None

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
    group_id: str | None
    name: str
    project_type: str
    canvas: object
    connection_id: str | None
    created_at: datetime | str | None
    updated_at: datetime | str | None
    created_by: str | None
    description: str | None = None
    retention_n: int | None = None

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
            canvas=row.get("canvas"),
            group_id=_optional_str(row, "group_id"),
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
            execute(
                "DELETE FROM comparison_keys WHERE canvas_id = ? AND node_id = ?",
                (canvas_id, node_id),
            )
        else:
            execute("DELETE FROM comparison_keys WHERE canvas_id = ?", (canvas_id,))

    def get_project_row(
        self,
        project_id: str,
        group_id: str | None = None,
    ) -> ProjectDetailRow | None:
        if group_id:
            row = fetchone(
                "SELECT * FROM projects WHERE id = ? AND group_id = ?",
                (project_id, group_id),
            )
        else:
            row = fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        if row and row.get("canvas"):
            try:
                row["canvas"] = json.loads(row["canvas"])
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning(
                    "Corrupted canvas in DB for project %s: %s. "
                    "Falling back to empty canvas.",
                    project_id,
                    exc,
                )
                row["canvas"] = {"nodes": [], "edges": []}
        return ProjectDetailRow.from_mapping(row) if row else None

    def list_projects(
        self,
        group_id: str | None = None,
        project_type: str | None = None,
    ) -> list[ProjectRow]:
        if group_id is None:
            if project_type:
                return _project_rows(
                    fetchall(
                        "SELECT id, group_id, name, project_type, connection_id, "
                        "created_at, updated_at, created_by FROM projects "
                        "WHERE project_type = ? ORDER BY updated_at DESC",
                        (project_type,),
                    )
                )
            return _project_rows(
                fetchall(
                    "SELECT id, group_id, name, project_type, connection_id, "
                    "created_at, updated_at, created_by FROM projects "
                    "ORDER BY updated_at DESC"
                )
            )
        if project_type:
            return _project_rows(
                fetchall(
                    "SELECT id, group_id, name, project_type, connection_id, "
                    "created_at, updated_at, created_by FROM projects "
                    "WHERE group_id = ? AND project_type = ? ORDER BY updated_at DESC",
                    (group_id, project_type),
                )
            )
        return _project_rows(
            fetchall(
                "SELECT id, group_id, name, project_type, connection_id, "
                "created_at, updated_at, created_by FROM projects "
                "WHERE group_id = ? ORDER BY updated_at DESC",
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
        now = datetime.now(timezone.utc).isoformat()
        canvas = json.dumps({"nodes": [], "edges": []})
        execute(
            "INSERT INTO projects (id, group_id, name, project_type, canvas, "
            "connection_id, created_at, updated_at, created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                project_id,
                group_id,
                name,
                project_type,
                canvas,
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
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause, vals = safe_set_clause("projects", fields)
        execute(f"UPDATE projects SET {set_clause} WHERE id = ?", vals + (project_id,))
        return get_project_row(project_id)

    def delete_project(self, project_id: str, group_id: str | None = None) -> bool:
        if group_id:
            cur = execute(
                "DELETE FROM projects WHERE id = ? AND group_id = ?",
                (project_id, group_id),
            )
        else:
            cur = execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cur.rowcount > 0

    def get_project_retention_n(self, project_id: str) -> int | None:
        row = fetchone("SELECT retention_n FROM projects WHERE id = ?", (project_id,))
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
        placeholders = ",".join(["?"] * len(ids))
        rows = fetchall(
            f"SELECT id FROM projects WHERE id IN ({placeholders}) AND group_id = ?",
            tuple(ids) + (owner_group_id,),
        )
        return {_required_str(row, "id") for row in rows}

    def list_project_ids_for_group(self, group_id: str) -> set[str]:
        if not group_id:
            return set()
        rows = fetchall(
            "SELECT id FROM projects WHERE group_id = ?",
            (group_id,),
        )
        return {_required_str(row, "id") for row in rows}

    def list_project_dependency_rows_using_connection(
        self,
        connection_id: str,
    ) -> list[ProjectDependencyRow]:
        return _project_dependency_rows(
            fetchall(
                "SELECT id, name, project_type, group_id "
                "FROM projects WHERE connection_id = ?",
                (connection_id,),
            )
        )

    def list_project_canvas_check_rows(self) -> list[ProjectCanvasCheckRow]:
        return _project_canvas_check_rows(
            fetchall("SELECT id, name, project_type, canvas, group_id FROM projects")
        )

    def update_project_canvas_key_atomic(
        self,
        project_id: str,
        key: str,
        value: object,
    ) -> bool:
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
