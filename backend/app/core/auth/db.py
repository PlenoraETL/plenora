"""
Auth-owned persistence boundary for users and groups.

This module owns the SQL and row contracts for auth configuration tables.
The legacy ``app.db.config.users`` and ``app.db.config.groups`` leaves were
removed after callers migrated to this auth-owned boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from app.db.pg_connection import execute, fetchall, fetchone
from app.db.sql_safety import safe_set_clause


@dataclass(frozen=True)
class UserRow:
    id: str
    username: str
    password_hash: str = ""
    email: str | None = ""
    group_id: str = ""
    role: str = ""
    is_active: bool = True
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class GroupRow:
    id: str
    name: str = ""
    description: str | None = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class GroupSummaryRow:
    projects: int = 0
    users: int = 0
    connections: int = 0
    file_sources: int = 0
    datasources: int = 0
    api_connections: int = 0
    schedules: int = 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_str(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _as_bool(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    return bool(value)


def _as_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    return int(value)


def hydrate_user_row(row: Mapping[str, Any] | None) -> UserRow | None:
    if not row:
        return None
    return UserRow(
        id=_as_str(row.get("id")),
        username=_as_str(row.get("username")),
        password_hash=_as_str(row.get("password_hash")),
        email=row.get("email") if row.get("email") is None else _as_str(row.get("email")),
        group_id=_as_str(row.get("group_id")),
        role=_as_str(row.get("role")),
        is_active=_as_bool(row.get("is_active")),
        created_at=_as_str(row.get("created_at")),
        updated_at=_as_str(row.get("updated_at")),
    )


def hydrate_group_row(row: Mapping[str, Any] | None) -> GroupRow | None:
    if not row:
        return None
    return GroupRow(
        id=_as_str(row.get("id")),
        name=_as_str(row.get("name")),
        description=row.get("description") if row.get("description") is None else _as_str(row.get("description")),
        created_at=_as_str(row.get("created_at")),
        updated_at=_as_str(row.get("updated_at")),
    )


def hydrate_group_summary_row(row: Mapping[str, Any] | None) -> GroupSummaryRow:
    if not row:
        return GroupSummaryRow()
    return GroupSummaryRow(
        projects=_as_int(row.get("projects")),
        users=_as_int(row.get("users")),
        connections=_as_int(row.get("connections")),
        file_sources=_as_int(row.get("file_sources")),
        datasources=_as_int(row.get("datasources")),
        api_connections=_as_int(row.get("api_connections")),
        schedules=_as_int(row.get("schedules")),
    )


def get_user(user_id: str) -> Optional[UserRow]:
    row = fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    return hydrate_user_row(row)


def get_user_by_username(username: str) -> Optional[UserRow]:
    row = fetchone("SELECT * FROM users WHERE username = ?", (username,))
    return hydrate_user_row(row)


def list_users(group_id: str) -> list[UserRow]:
    rows = fetchall(
        "SELECT id, username, email, group_id, role, is_active, created_at "
        "FROM users WHERE group_id = ? ORDER BY username",
        (group_id,),
    )
    return [row for row in (hydrate_user_row(row) for row in rows) if row is not None]


def list_all_users() -> list[UserRow]:
    rows = fetchall("SELECT * FROM users ORDER BY username")
    return [row for row in (hydrate_user_row(row) for row in rows) if row is not None]


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
    return hydrate_group_row(row)


def list_groups() -> list[GroupRow]:
    rows = fetchall("SELECT * FROM groups ORDER BY name")
    return [row for row in (hydrate_group_row(row) for row in rows) if row is not None]


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
    return hydrate_group_summary_row(row)


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
    "delete_user",
    "get_group",
    "get_group_summary",
    "get_user",
    "get_user_by_username",
    "hydrate_group_row",
    "hydrate_group_summary_row",
    "hydrate_user_row",
    "list_all_users",
    "list_groups",
    "list_users",
    "update_user",
    "update_user_password",
]
