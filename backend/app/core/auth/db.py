"""
Auth-owned persistence boundary for users and groups.

This module owns the SQL and row contracts for auth configuration tables.
The legacy ``app.db.config.users`` and ``app.db.config.groups`` leaves were
removed after callers migrated to this auth-owned boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, TypedDict, cast

from app.db.pg_connection import execute, fetchall, fetchone
from app.db.sql_safety import safe_set_clause


class UserRow(TypedDict, total=False):
    id: str
    username: str
    password_hash: str
    email: str
    group_id: str
    role: str
    is_active: bool
    created_at: str
    updated_at: str


class GroupRow(TypedDict, total=False):
    id: str
    name: str
    description: str
    created_at: str
    updated_at: str


class GroupSummaryRow(TypedDict):
    projects: int
    users: int
    connections: int
    file_sources: int
    datasources: int
    api_connections: int
    schedules: int


_GROUP_SUMMARY_KEYS = (
    "projects",
    "users",
    "connections",
    "file_sources",
    "datasources",
    "api_connections",
    "schedules",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_user(user_id: str) -> Optional[UserRow]:
    row = fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    return cast(Optional[UserRow], row)


def get_user_by_username(username: str) -> Optional[UserRow]:
    row = fetchone("SELECT * FROM users WHERE username = ?", (username,))
    return cast(Optional[UserRow], row)


def list_users(group_id: str) -> list[UserRow]:
    rows = fetchall(
        "SELECT id, username, email, group_id, role, is_active, created_at "
        "FROM users WHERE group_id = ? ORDER BY username",
        (group_id,),
    )
    return cast(list[UserRow], rows)


def list_all_users() -> list[UserRow]:
    return cast(list[UserRow], fetchall("SELECT * FROM users ORDER BY username"))


def create_user(
    user_id: str,
    username: str,
    password_hash: str,
    group_id: str,
    role: str = "user",
    email: str = "",
) -> UserRow:
    now = _now_iso()
    execute(
        "INSERT INTO users (id, username, password_hash, email, group_id, "
        "role, is_active, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,TRUE,?,?)",
        (user_id, username, password_hash, email, group_id, role, now, now),
    )
    user = get_user(user_id)
    if user is None:
        raise RuntimeError(f"Inserted user {user_id} was not found")
    return user


def update_user(
    user_id: str,
    *,
    email: str | None = None,
    is_active: bool | None = None,
    role: str | None = None,
    group_id: str | None = None,
) -> Optional[UserRow]:
    fields: dict[str, str | bool] = {}
    if email is not None:
        fields["email"] = email
    if is_active is not None:
        fields["is_active"] = is_active
    if role is not None:
        fields["role"] = role
    if group_id is not None:
        fields["group_id"] = group_id
    fields["updated_at"] = _now_iso()
    set_clause, vals = safe_set_clause("users", fields)
    execute(f"UPDATE users SET {set_clause} WHERE id = ?", vals + (user_id,))
    return get_user(user_id)


def update_user_password(user_id: str, password_hash: str) -> None:
    execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (password_hash, _now_iso(), user_id),
    )


def delete_user(user_id: str) -> bool:
    cur = execute("DELETE FROM users WHERE id = ?", (user_id,))
    return cur.rowcount > 0


def get_group(group_id: str) -> Optional[GroupRow]:
    row = fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
    return cast(Optional[GroupRow], row)


def list_groups() -> list[GroupRow]:
    return cast(list[GroupRow], fetchall("SELECT * FROM groups ORDER BY name"))


def create_group(group_id: str, name: str, description: str = "") -> GroupRow:
    now = _now_iso()
    execute(
        "INSERT INTO groups (id, name, description, created_at, updated_at) "
        "VALUES (?,?,?,?,?)",
        (group_id, name, description, now, now),
    )
    group = get_group(group_id)
    if group is None:
        raise RuntimeError(f"Inserted group {group_id} was not found")
    return group


def get_group_summary(group_id: str) -> GroupSummaryRow:
    row = fetchone(
        "SELECT "
        "(SELECT COUNT(*) FROM projects WHERE group_id = ?) AS projects, "
        "(SELECT COUNT(*) FROM users WHERE group_id = ?) AS users, "
        "(SELECT COUNT(*) FROM connections WHERE group_id = ?) AS connections, "
        "(SELECT COUNT(*) FROM file_sources WHERE group_id = ?) AS file_sources, "
        "(SELECT COUNT(*) FROM datasources WHERE group_id = ?) AS datasources, "
        "(SELECT COUNT(*) FROM api_connections WHERE group_id = ?) AS api_connections, "
        "(SELECT COUNT(*) FROM schedules WHERE group_id = ?) AS schedules",
        tuple([group_id] * 7),
    )
    if not row:
        return cast(GroupSummaryRow, {key: 0 for key in _GROUP_SUMMARY_KEYS})
    return {
        "projects": int(row.get("projects", 0)),
        "users": int(row.get("users", 0)),
        "connections": int(row.get("connections", 0)),
        "file_sources": int(row.get("file_sources", 0)),
        "datasources": int(row.get("datasources", 0)),
        "api_connections": int(row.get("api_connections", 0)),
        "schedules": int(row.get("schedules", 0)),
    }


def delete_group_cascade(group_id: str) -> dict[str, int]:
    """
    Cascade delete a group and its auth/config-owned data.

    The existing recovery policy is preserved: non-final cleanup failures are
    logged and counted as zero; deleting the group itself remains fail-loud.
    """
    from app.utils.logger import get_logger

    log = get_logger("auth.db.delete_group_cascade")
    report = {
        "schedules": 0,
        "api_connections": 0,
        "datasources": 0,
        "file_sources": 0,
        "connections": 0,
        "projects": 0,
        "users": 0,
        "results": 0,
        "intermediate_runs": 0,
        "log_runs": 0,
    }

    for table in [
        "schedules",
        "api_connections",
        "datasources",
        "file_sources",
        "connections",
    ]:
        try:
            cur = execute(f"DELETE FROM {table} WHERE group_id = ?", (group_id,))
            report[table] = cur.rowcount
        except Exception as exc:
            log.warning(f"Cascade delete {table} fallito: {exc}")

    try:
        projects = fetchall("SELECT id FROM projects WHERE group_id = ?", (group_id,))
        project_ids = [str(project["id"]) for project in projects]

        for project_id in project_ids:
            try:
                from app.core.results.cleanup import delete_results_for_project

                report["results"] += delete_results_for_project(project_id)
            except Exception as exc:
                log.warning(f"Cascade delete results progetto {project_id}: {exc}")

            try:
                from app.core.intermediate.db import delete_all_runs_for_canvas

                report["intermediate_runs"] += delete_all_runs_for_canvas(project_id)
            except Exception as exc:
                log.warning(
                    f"Cascade delete intermediate progetto {project_id}: {exc}"
                )

        if project_ids:
            try:
                execute(
                    "DELETE FROM comparison_keys WHERE canvas_id IN "
                    "(SELECT id FROM projects WHERE group_id = ?)",
                    (group_id,),
                )
            except Exception as exc:
                log.warning(f"Cascade batch delete comparison_keys fallito: {exc}")

            try:
                cur = execute("DELETE FROM projects WHERE group_id = ?", (group_id,))
                report["projects"] = cur.rowcount if hasattr(cur, "rowcount") else len(project_ids)
            except Exception as exc:
                log.warning(f"Cascade batch delete projects fallito: {exc}")
    except Exception as exc:
        log.warning(f"Cascade delete projects fallito: {exc}")

    try:
        cur = execute("DELETE FROM users WHERE group_id = ?", (group_id,))
        report["users"] = cur.rowcount
    except Exception as exc:
        log.warning(f"Cascade delete users fallito: {exc}")

    try:
        cur = execute("DELETE FROM log_runs WHERE group_id = ?", (group_id,))
        report["log_runs"] = cur.rowcount
    except Exception as exc:
        log.warning(f"Cascade delete log_runs fallito: {exc}")

    try:
        execute("DELETE FROM groups WHERE id = ?", (group_id,))
    except Exception as exc:
        log.error(f"Eliminazione gruppo {group_id} fallita: {exc}")
        raise

    log.info(
        f"Cascade delete gruppo {group_id} completato: {report}",
        op="delete_group_cascade",
    )
    return report


def delete_group(group_id: str) -> bool:
    cur = execute("DELETE FROM groups WHERE id = ?", (group_id,))
    return cur.rowcount > 0


__all__ = [
    "GroupRow",
    "GroupSummaryRow",
    "UserRow",
    "create_group",
    "create_user",
    "delete_group",
    "delete_group_cascade",
    "delete_user",
    "get_group",
    "get_group_summary",
    "get_user",
    "get_user_by_username",
    "list_all_users",
    "list_groups",
    "list_users",
    "update_user",
    "update_user_password",
]
