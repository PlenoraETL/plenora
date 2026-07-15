"""
Auth-owned persistence boundary for users and groups.

This module owns the SQL and row contracts for auth configuration tables.
The legacy ``app.db.config.users`` and ``app.db.config.groups`` leaves were
removed after callers migrated to this auth-owned boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Mapping, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    StrictBool,
    StrictInt,
    StrictStr,
    TypeAdapter,
)
from app.db.pg_connection import unit_of_work
from app.db.sql_safety import safe_set_clause


def _coerce_timestamp_to_str(value: Any) -> Any:
    """Accept psycopg2 datetime values for TIMESTAMPTZ columns.

    The driver can return ``datetime`` objects directly for TIMESTAMPTZ.
    Hydration models keep the row shape as strings for compatibility, so
    we normalise here:
    ``datetime`` -> ISO-8601 string, anything else passes through to
    StrictStr validation untouched.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    return value


_TimestampStr = Annotated[StrictStr, BeforeValidator(_coerce_timestamp_to_str)]


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


class _HydrationModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _UserRowHydration(_HydrationModel):
    id: StrictStr
    username: StrictStr
    password_hash: StrictStr = ""
    email: StrictStr | None = ""
    group_id: StrictStr = ""
    role: StrictStr = ""
    is_active: StrictBool = True
    created_at: _TimestampStr = ""
    updated_at: _TimestampStr = ""


class _GroupRowHydration(_HydrationModel):
    id: StrictStr
    name: StrictStr = ""
    description: StrictStr | None = ""
    created_at: _TimestampStr = ""
    updated_at: _TimestampStr = ""


class _GroupSummaryRowHydration(_HydrationModel):
    projects: StrictInt = 0
    users: StrictInt = 0
    connections: StrictInt = 0
    file_sources: StrictInt = 0
    datasources: StrictInt = 0
    api_connections: StrictInt = 0
    schedules: StrictInt = 0


_USER_ROW_ADAPTER = TypeAdapter(_UserRowHydration)
_GROUP_ROW_ADAPTER = TypeAdapter(_GroupRowHydration)
_GROUP_SUMMARY_ROW_ADAPTER = TypeAdapter(_GroupSummaryRowHydration)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hydrate_user_row(row: Mapping[str, Any] | None) -> UserRow | None:
    if row is None:
        return None
    parsed = _USER_ROW_ADAPTER.validate_python(dict(row))
    return UserRow(**parsed.model_dump())


def hydrate_group_row(row: Mapping[str, Any] | None) -> GroupRow | None:
    if row is None:
        return None
    parsed = _GROUP_ROW_ADAPTER.validate_python(dict(row))
    return GroupRow(**parsed.model_dump())


def hydrate_group_summary_row(row: Mapping[str, Any] | None) -> GroupSummaryRow:
    if not row:
        return GroupSummaryRow()
    parsed = _GROUP_SUMMARY_ROW_ADAPTER.validate_python(dict(row))
    return GroupSummaryRow(**parsed.model_dump())


def _fetchone(sql: str, params: tuple = ()) -> Mapping[str, Any] | None:
    with unit_of_work() as session:
        return session.fetchone(sql, params)


def _fetchall(sql: str, params: tuple = ()) -> list[Mapping[str, Any]]:
    with unit_of_work() as session:
        return session.fetchall(sql, params)


def _execute(sql: str, params: tuple = ()):
    with unit_of_work() as session:
        return session.execute(sql, params)


def get_user(user_id: str) -> Optional[UserRow]:
    row = _fetchone("SELECT * FROM users WHERE id = %s", (user_id,))
    return hydrate_user_row(row)


def get_user_by_username(username: str) -> Optional[UserRow]:
    row = _fetchone("SELECT * FROM users WHERE username = %s", (username,))
    return hydrate_user_row(row)


def list_users(
    group_id: str, *, limit: int = 200, offset: int = 0,
) -> list[UserRow]:
    rows = _fetchall(
        "SELECT id, username, email, group_id, role, is_active, created_at "
        "FROM users WHERE group_id = %s ORDER BY username LIMIT %s OFFSET %s",
        (group_id, limit, offset),
    )
    return [row for row in (hydrate_user_row(row) for row in rows) if row is not None]


def list_all_users(*, limit: int = 200, offset: int = 0) -> list[UserRow]:
    rows = _fetchall(
        "SELECT * FROM users ORDER BY username LIMIT %s OFFSET %s",
        (limit, offset),
    )
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
    _execute(
        "INSERT INTO users (id, username, password_hash, email, group_id, "
        "role, is_active, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,TRUE,%s,%s)",
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
    _execute(f"UPDATE users SET {set_clause} WHERE id = %s", vals + (user_id,))
    return get_user(user_id)


def update_user_password(user_id: str, password_hash: str) -> None:
    _execute(
        "UPDATE users SET password_hash = %s, updated_at = %s WHERE id = %s",
        (password_hash, _now_iso(), user_id),
    )


def delete_user(user_id: str) -> bool:
    cur = _execute("DELETE FROM users WHERE id = %s", (user_id,))
    return cur.rowcount > 0


def get_group(group_id: str) -> Optional[GroupRow]:
    row = _fetchone("SELECT * FROM groups WHERE id = %s", (group_id,))
    return hydrate_group_row(row)


def list_groups(*, limit: int = 200, offset: int = 0) -> list[GroupRow]:
    rows = _fetchall(
        "SELECT * FROM groups ORDER BY name LIMIT %s OFFSET %s",
        (limit, offset),
    )
    return [row for row in (hydrate_group_row(row) for row in rows) if row is not None]


def create_group(group_id: str, name: str, description: str = "") -> GroupRow:
    now = _now_iso()
    _execute(
        "INSERT INTO groups (id, name, description, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s)",
        (group_id, name, description, now, now),
    )
    group = get_group(group_id)
    if group is None:
        raise RuntimeError(f"Inserted group {group_id} was not found")
    return group


def get_group_summary(group_id: str) -> GroupSummaryRow:
    row = _fetchone(
        "SELECT "
        "(SELECT COUNT(*) FROM projects WHERE group_id = %s) AS projects, "
        "(SELECT COUNT(*) FROM users WHERE group_id = %s) AS users, "
        "(SELECT COUNT(*) FROM connections WHERE group_id = %s) AS connections, "
        "(SELECT COUNT(*) FROM file_sources WHERE group_id = %s) AS file_sources, "
        "(SELECT COUNT(*) FROM datasources WHERE group_id = %s) AS datasources, "
        "(SELECT COUNT(*) FROM api_connections WHERE group_id = %s) AS api_connections, "
        "(SELECT COUNT(*) FROM schedules WHERE group_id = %s) AS schedules",
        tuple([group_id] * 7),
    )
    return hydrate_group_summary_row(row)


def delete_group(group_id: str) -> bool:
    cur = _execute("DELETE FROM groups WHERE id = %s", (group_id,))
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
