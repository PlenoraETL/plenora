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

import psycopg
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    StrictBool,
    StrictInt,
    StrictStr,
    TypeAdapter,
)
from app.core.auth.exceptions import UsernameInvalidError, UsernameTakenError
from app.db.pg_connection import unit_of_work
from app.shared.contracts.auth import normalized_username, username_form_error
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


def find_users_by_normalized_username(username: str) -> list[UserRow]:
    """Utenti il cui username coincide con ``username`` in forma normalizzata
    (trim + casefold). Il confronto e' in Python su tutti gli username:
    ``casefold`` non ha un equivalente esatto in SQL (``lower`` lascia «ß») e
    la tabella degli utenti e' piccola."""
    target = normalized_username(username)
    rows = _fetchall("SELECT id, username FROM users")
    matches = [str(row["id"]) for row in rows if normalized_username(row["username"]) == target]
    return [user for user in (get_user(user_id) for user_id in matches) if user is not None]


def assert_username_writable(username: str) -> None:
    """Regola unica di OGNI scrittura di un utente
    (``shared.contracts.auth``): forma valida e nessun altro utente con lo
    stesso username normalizzato."""
    problem = username_form_error(username)
    if problem is not None:
        raise UsernameInvalidError(problem)
    if find_users_by_normalized_username(username):
        raise UsernameTakenError("Username già esistente")


# Vincoli del DB che esprimono l'unicita' dello username: UNIQUE esatto della
# tabella base e indice sulla forma normalizzata lower(btrim(username))
# (migrations/2026-11-05_users_username_normalized_unique.sql). La forma del
# DB e' piu' stretta di casefold (es. «ß»): il controllo applicativo resta la
# regola, l'indice chiude la finestra di concorrenza.
USERNAME_NORMALIZED_INDEX = "ux_users_username_normalized"
USERNAME_UNIQUE_CONSTRAINTS = frozenset({"users_username_key", USERNAME_NORMALIZED_INDEX})


def is_username_unique_violation(exc: BaseException) -> bool:
    """La violazione di unicita' riguarda lo username (e non, es., l'id)."""
    diag = getattr(exc, "diag", None)
    return getattr(diag, "constraint_name", None) in USERNAME_UNIQUE_CONSTRAINTS


@dataclass(frozen=True)
class UsernameIndexStatus:
    """Stato dell'indice unico sugli username normalizzati: presente, oppure
    assente con i gruppi di username che ne impediscono la creazione."""

    present: bool
    collisions: tuple[tuple[str, ...], ...] = ()


def get_username_index_status() -> UsernameIndexStatus:
    present_row = _fetchone(
        "SELECT to_regclass(%s) IS NOT NULL AS present",
        (f"plenora.{USERNAME_NORMALIZED_INDEX}",),
    )
    if present_row and present_row.get("present"):
        return UsernameIndexStatus(present=True)
    rows = _fetchall(
        "SELECT array_agg(username ORDER BY username) AS usernames FROM users "
        "GROUP BY lower(btrim(username)) HAVING count(*) > 1 "
        "ORDER BY lower(btrim(username))"
    )
    return UsernameIndexStatus(
        present=False,
        collisions=tuple(tuple(str(u) for u in row["usernames"]) for row in rows),
    )


def create_user(
    user_id: str,
    username: str,
    password_hash: str,
    group_id: str,
    role: str = "user",
    email: str = "",
) -> UserRow:
    # Unico punto di scrittura degli utenti (API, bootstrap degli installer,
    # seed): la regola dello username vale per tutti.
    assert_username_writable(username)
    now = _now_iso()
    try:
        _execute(
            "INSERT INTO users (id, username, password_hash, email, group_id, "
            "role, is_active, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,TRUE,%s,%s)",
            (user_id, username, password_hash, email, group_id, role, now, now),
        )
    except psycopg.errors.UniqueViolation as exc:
        # Creazione concorrente: il controllo sopra e' passato per entrambe,
        # il DB ne ferma una. Stesso esito del controllo applicativo.
        if is_username_unique_violation(exc):
            raise UsernameTakenError("Username già esistente") from exc
        raise
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
    "USERNAME_NORMALIZED_INDEX",
    "USERNAME_UNIQUE_CONSTRAINTS",
    "UsernameIndexStatus",
    "assert_username_writable",
    "find_users_by_normalized_username",
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
    "get_username_index_status",
    "hydrate_group_row",
    "is_username_unique_violation",
    "hydrate_group_summary_row",
    "hydrate_user_row",
    "list_all_users",
    "list_groups",
    "list_users",
    "update_user",
    "update_user_password",
]
