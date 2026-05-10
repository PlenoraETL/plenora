"""Auth-owned visibility policy for shareable resources."""

from __future__ import annotations

from typing import Optional, TypedDict, cast

from app.db.pg_connection import (
    fetchone as _pg_fetchone,
    get_connection as _pg_get_conn,
    put_connection as _pg_put_conn,
)


MAGIC_ALL = "*"

_SHAREABLE: dict[str, tuple[str, str]] = {
    "connections": ("connection_shares", "connection_id"),
    "datasources": ("datasource_shares", "datasource_id"),
    "eseguibili": ("eseguibile_shares", "eseguibile_id"),
    "api_connections": ("api_connection_shares", "api_connection_id"),
    "arcgis_connections": ("arcgis_connection_shares", "arcgis_connection_id"),
    "file_sources": ("file_source_shares", "file_source_id"),
}


class ShareableResourceOwner(TypedDict):
    id: str
    group_id: str | None


def _conn():
    """Return a PostgreSQL connection from the pool."""
    return _pg_get_conn()


def _validate_table(resource_table: str) -> tuple[str, str]:
    if resource_table not in _SHAREABLE:
        raise ValueError(
            f"Tabella '{resource_table}' non e' shareable. "
            f"Validi: {list(_SHAREABLE)}"
        )
    return _SHAREABLE[resource_table]


def is_shareable(resource_table: str) -> bool:
    return resource_table in _SHAREABLE


def load_shareable_resource_owner(
    resource_table: str,
    resource_id: str,
) -> ShareableResourceOwner | None:
    """Load the owner group row for a shareable resource."""
    _validate_table(resource_table)
    row = _pg_fetchone(
        f"SELECT id, group_id FROM {resource_table} WHERE id = %s",
        (resource_id,),
    )
    return cast(ShareableResourceOwner, dict(row)) if row else None


def visible_resource_ids(
    resource_table: str,
    user_group_id: str,
    is_superadmin: bool = False,
) -> set[str]:
    """Return ids from a resource table visible to a user group."""
    share_table, fk = _validate_table(resource_table)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            if is_superadmin:
                cur.execute(f"SELECT id FROM {resource_table}")
                return {row["id"] for row in cur.fetchall()}

            cur.execute(
                f"""
                SELECT id FROM {resource_table} WHERE group_id = %s
                UNION
                SELECT {fk} FROM {share_table}
                WHERE group_id = %s OR group_id = %s
                """,
                (user_group_id, user_group_id, MAGIC_ALL),
            )
            return {row["id"] for row in cur.fetchall()}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        _pg_put_conn(conn)


def is_resource_visible(
    resource_table: str,
    resource_id: str,
    user_group_id: str,
    is_superadmin: bool = False,
    resource_owner_group_id: Optional[str] = None,
) -> bool:
    """Return whether a resource is owned by or shared with a user group."""
    if is_superadmin:
        return True

    share_table, fk = _validate_table(resource_table)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            if resource_owner_group_id is None:
                cur.execute(
                    f"SELECT group_id FROM {resource_table} WHERE id = %s",
                    (resource_id,),
                )
                row = cur.fetchone()
                if not row:
                    return False
                resource_owner_group_id = row["group_id"]

            if resource_owner_group_id == user_group_id:
                return True

            cur.execute(
                f"""
                SELECT 1 FROM {share_table}
                WHERE {fk} = %s
                  AND (group_id = %s OR group_id = %s)
                LIMIT 1
                """,
                (resource_id, user_group_id, MAGIC_ALL),
            )
            return cur.fetchone() is not None
    finally:
        _pg_put_conn(conn)
