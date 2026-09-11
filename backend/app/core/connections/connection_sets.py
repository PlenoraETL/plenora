"""Persistence e policy dei Connection Set.

Un Connection Set risolve una chiave pubblica (per esempio ``cliente_a``)
in un ``connection_id`` privato. Le API e i manifest non espongono mai
host, username, password o stringhe di connessione.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Literal, TypedDict

from app.db.pg_connection import PgSession, unit_of_work

from .models import (
    ConnectionSet,
    ConnectionSetCreate,
    ConnectionSetMember,
    ConnectionSetMemberInput,
    ConnectionSetOption,
    ConnectionSetUpdate,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _member_from_row(row: Mapping[str, object]) -> ConnectionSetMember:
    return ConnectionSetMember(
        selector_key=str(row.get("selector_key") or ""),
        label=str(row.get("label") or ""),
        connection_id=str(row.get("connection_id") or ""),
        active=bool(row.get("active", True)),
        sort_order=int(row.get("sort_order") or 0),
        connection_name=str(row.get("connection_name") or ""),
        connection_type=str(row.get("connection_type") or ""),
    )


class ConnectionSetDependency(TypedDict):
    kind: Literal["project", "eseguibile"]
    id: str
    name: str


def _load_members(
    session: PgSession, set_ids: Iterable[str]
) -> Mapping[str, list[ConnectionSetMember]]:
    ordered_ids = list(dict.fromkeys(str(value) for value in set_ids if value))
    if not ordered_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ordered_ids))
    rows = session.fetchall(
        f"""
        SELECT m.*, c.name AS connection_name, c.db_type AS connection_type
        FROM connection_set_members m
        JOIN connections c ON c.id = m.connection_id
        WHERE m.connection_set_id IN ({placeholders})
        ORDER BY m.connection_set_id, m.sort_order, lower(m.label), m.selector_key
        """,
        tuple(ordered_ids),
    )
    result: dict[str, list[ConnectionSetMember]] = {
        connection_set_id: [] for connection_set_id in ordered_ids
    }
    for row in rows:
        result.setdefault(str(row["connection_set_id"]), []).append(
            _member_from_row(row)
        )
    return result


def _set_from_row(
    row: Mapping[str, object],
    members: list[ConnectionSetMember],
) -> ConnectionSet:
    return ConnectionSet(
        id=str(row["id"]),
        group_id=str(row["group_id"]),
        name=str(row["name"]),
        description=str(row.get("description") or ""),
        selector_label=str(row.get("selector_label") or "Cliente"),
        members=members,
        created_by=str(row["created_by"]) if row.get("created_by") else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_connection_sets(
    *,
    group_id: str | None,
) -> list[ConnectionSet]:
    with unit_of_work() as session:
        if group_id is None:
            rows = session.fetchall(
                "SELECT * FROM connection_sets ORDER BY lower(name), id"
            )
        else:
            rows = session.fetchall(
                """
                SELECT * FROM connection_sets
                WHERE group_id = %s
                ORDER BY lower(name), id
                """,
                (group_id,),
            )
        members_by_set = _load_members(session, [str(row["id"]) for row in rows])
    return [
        _set_from_row(row, members_by_set.get(str(row["id"]), []))
        for row in rows
    ]


def get_connection_set(connection_set_id: str) -> ConnectionSet | None:
    with unit_of_work() as session:
        row = session.fetchone(
            "SELECT * FROM connection_sets WHERE id = %s",
            (connection_set_id,),
        )
        if row is None:
            return None
        members = _load_members(session, [connection_set_id]).get(
            connection_set_id, []
        )
    return _set_from_row(row, members)


def _validate_members(
    session: PgSession,
    *,
    group_id: str,
    members: list[ConnectionSetMemberInput],
) -> None:
    keys = [member.selector_key for member in members]
    if len(keys) != len(set(keys)):
        raise ValueError("Le chiavi cliente nel Connection Set devono essere univoche")
    connection_ids = list(dict.fromkeys(member.connection_id for member in members))
    if not connection_ids:
        return
    placeholders = ", ".join(["%s"] * len(connection_ids))
    rows = session.fetchall(
        f"""
        SELECT id, group_id FROM connections
        WHERE id IN ({placeholders})
        """,
        tuple(connection_ids),
    )
    found = {str(row["id"]): str(row["group_id"]) for row in rows}
    missing = sorted(set(connection_ids) - set(found))
    if missing:
        raise ValueError("Connessioni non trovate: " + ", ".join(missing))

    # Un set puo' usare connessioni del proprio tenant. Le connessioni shared
    # restano deliberatamente escluse dal primo contratto: il runtime di un
    # eseguibile condiviso opera sempre sotto il tenant proprietario.
    foreign = sorted(
        connection_id
        for connection_id, owner_group in found.items()
        if owner_group != group_id
    )
    if foreign:
        raise ValueError(
            "Il Connection Set può contenere solo connessioni del proprio gruppo: "
            + ", ".join(foreign)
        )


def _replace_members(
    session: PgSession,
    *,
    connection_set_id: str,
    group_id: str,
    members: list[ConnectionSetMemberInput],
    now: datetime,
) -> None:
    _validate_members(session, group_id=group_id, members=members)
    session.execute(
        "DELETE FROM connection_set_members WHERE connection_set_id = %s",
        (connection_set_id,),
    )
    for index, member in enumerate(members):
        session.execute(
            """
            INSERT INTO connection_set_members (
                connection_set_id, selector_key, label, connection_id,
                active, sort_order, created_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                connection_set_id,
                member.selector_key,
                member.label,
                member.connection_id,
                member.active,
                member.sort_order if member.sort_order else index,
                now,
                now,
            ),
        )


def create_connection_set(
    data: ConnectionSetCreate,
    *,
    group_id: str,
    created_by: str | None,
) -> ConnectionSet:
    connection_set_id = f"cset_{uuid.uuid4().hex[:16]}"
    now = _now()
    with unit_of_work() as session:
        duplicate = session.fetchone(
            """
            SELECT id FROM connection_sets
            WHERE group_id = %s AND name = %s
            """,
            (group_id, data.name),
        )
        if duplicate is not None:
            raise ValueError(
                f"Esiste già un Connection Set chiamato '{data.name}'"
            )
        session.execute(
            """
            INSERT INTO connection_sets (
                id, group_id, name, description, selector_label,
                created_by, created_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                connection_set_id,
                group_id,
                data.name,
                data.description,
                data.selector_label,
                created_by,
                now,
                now,
            ),
        )
        _replace_members(
            session,
            connection_set_id=connection_set_id,
            group_id=group_id,
            members=data.members,
            now=now,
        )
    created = get_connection_set(connection_set_id)
    if created is None:
        raise RuntimeError("Connection Set non leggibile dopo la creazione")
    return created


def update_connection_set(
    connection_set_id: str,
    data: ConnectionSetUpdate,
) -> ConnectionSet | None:
    now = _now()
    with unit_of_work() as session:
        existing = session.fetchone(
            "SELECT * FROM connection_sets WHERE id = %s",
            (connection_set_id,),
        )
        if existing is None:
            return None
        fields = data.model_dump(exclude_unset=True, exclude={"members"})
        if data.name is not None:
            duplicate = session.fetchone(
                """
                SELECT id FROM connection_sets
                WHERE group_id = %s AND name = %s AND id <> %s
                """,
                (str(existing["group_id"]), data.name, connection_set_id),
            )
            if duplicate is not None:
                raise ValueError(
                    f"Esiste già un Connection Set chiamato '{data.name}'"
                )
        if fields:
            assignments: list[str] = []
            values: list[object] = []
            for field in ("name", "description", "selector_label"):
                if field in fields:
                    assignments.append(f"{field} = %s")
                    values.append(fields[field])
            assignments.append("updated_at = %s")
            values.append(now)
            values.append(connection_set_id)
            session.execute(
                f"UPDATE connection_sets SET {', '.join(assignments)} WHERE id = %s",
                tuple(values),
            )
        if data.members is not None:
            _replace_members(
                session,
                connection_set_id=connection_set_id,
                group_id=str(existing["group_id"]),
                members=data.members,
                now=now,
            )
            session.execute(
                "UPDATE connection_sets SET updated_at = %s WHERE id = %s",
                (now, connection_set_id),
            )
    return get_connection_set(connection_set_id)


def delete_connection_set(connection_set_id: str) -> bool:
    with unit_of_work() as session:
        cursor = session.execute(
            "DELETE FROM connection_sets WHERE id = %s",
            (connection_set_id,),
        )
        return cursor.rowcount > 0


def connection_set_options(
    connection_set_id: str,
    *,
    expected_group_id: str,
) -> list[ConnectionSetOption]:
    connection_set = get_connection_set(connection_set_id)
    if connection_set is None or connection_set.group_id != expected_group_id:
        raise ValueError("Connection Set non trovato o non autorizzato")
    return [
        ConnectionSetOption(key=member.selector_key, label=member.label)
        for member in connection_set.members
        if member.active
    ]


def resolve_connection(
    connection_set_id: str,
    selector_key: str,
    *,
    expected_group_id: str,
) -> str:
    """Risoluzione server-authoritative della connessione runtime."""

    with unit_of_work() as session:
        row = session.fetchone(
            """
            SELECT m.connection_id
            FROM connection_set_members m
            JOIN connection_sets s ON s.id = m.connection_set_id
            JOIN connections c ON c.id = m.connection_id
            WHERE m.connection_set_id = %s
              AND m.selector_key = %s
              AND m.active = true
              AND s.group_id = %s
              AND c.group_id = s.group_id
            """,
            (connection_set_id, selector_key, expected_group_id),
        )
    if row is None:
        raise ValueError("Cliente non valido o non attivo nel Connection Set")
    return str(row["connection_id"])


def connection_set_ids_using_connection(connection_id: str) -> list[str]:
    with unit_of_work() as session:
        rows = session.fetchall(
            """
            SELECT DISTINCT connection_set_id
            FROM connection_set_members
            WHERE connection_id = %s
            ORDER BY connection_set_id
            """,
            (connection_id,),
        )
    return [str(row["connection_set_id"]) for row in rows]


def connection_set_dependencies(
    connection_set_id: str,
) -> list[ConnectionSetDependency]:
    """Progetti/versioni attive che rendono distruttiva la cancellazione."""

    marker = f"%{connection_set_id}%"
    with unit_of_work() as session:
        projects = session.fetchall(
            """
            SELECT id, name FROM projects
            WHERE canvas::text LIKE %s
            ORDER BY name, id
            """,
            (marker,),
        )
        executables = session.fetchall(
            """
            SELECT e.id, e.name
            FROM eseguibili e
            JOIN eseguibile_versions v ON v.id = e.active_version_id
            WHERE v.manifest::text LIKE %s
            ORDER BY e.name, e.id
            """,
            (marker,),
        )
    return [
        {"kind": "project", "id": str(row["id"]), "name": str(row["name"])}
        for row in projects
    ] + [
        {"kind": "eseguibile", "id": str(row["id"]), "name": str(row["name"])}
        for row in executables
    ]


__all__ = [
    "connection_set_ids_using_connection",
    "connection_set_dependencies",
    "connection_set_options",
    "create_connection_set",
    "delete_connection_set",
    "get_connection_set",
    "list_connection_sets",
    "resolve_connection",
    "update_connection_set",
]
