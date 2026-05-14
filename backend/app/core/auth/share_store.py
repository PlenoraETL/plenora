"""PostgreSQL store for auth sharing and visibility rows."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.db.pg_connection import (
    fetchone as _pg_fetchone,
    get_connection as _pg_get_conn,
    put_connection as _pg_put_conn,
)


def fetch_shareable_resource_owner(
    resource_table: str,
    resource_id: str,
) -> dict[str, Any] | None:
    """Load the owner group row for a shareable resource."""
    row = _pg_fetchone(
        f"SELECT id, group_id FROM {resource_table} WHERE id = %s",
        (resource_id,),
    )
    return dict(row) if row else None


def fetch_visible_resource_ids(
    resource_table: str,
    share_table: str,
    fk: str,
    user_group_id: str,
    magic_all: str,
    is_superadmin: bool = False,
) -> set[str]:
    """Return ids from a resource table visible to a user group."""
    conn = _pg_get_conn()
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
                (user_group_id, user_group_id, magic_all),
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


def check_resource_visible(
    resource_table: str,
    share_table: str,
    fk: str,
    resource_id: str,
    user_group_id: str,
    magic_all: str,
    resource_owner_group_id: str | None = None,
) -> bool:
    """Return whether a resource is owned by or shared with a user group."""
    conn = _pg_get_conn()
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
                (resource_id, user_group_id, magic_all),
            )
            return cur.fetchone() is not None
    finally:
        _pg_put_conn(conn)


def list_resource_share_group_ids(
    share_table: str,
    fk: str,
    resource_id: str,
) -> list[str]:
    """Return group ids for a resource share table."""
    conn = _pg_get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT group_id FROM {share_table} WHERE {fk} = %s ORDER BY group_id",
                (resource_id,),
            )
            return [row["group_id"] for row in cur.fetchall()]
    finally:
        _pg_put_conn(conn)


def insert_resource_share(
    share_table: str,
    fk: str,
    resource_id: str,
    group_id: str,
) -> bool:
    """Insert one sharing row idempotently."""
    conn = _pg_get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {share_table} ({fk}, group_id) VALUES (%s, %s) "
                f"ON CONFLICT DO NOTHING",
                (resource_id, group_id),
            )
            affected = cur.rowcount
        conn.commit()
        return affected > 0
    finally:
        _pg_put_conn(conn)


def delete_resource_share(
    share_table: str,
    fk: str,
    resource_id: str,
    group_id: str,
) -> bool:
    """Delete one sharing row."""
    conn = _pg_get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {share_table} WHERE {fk} = %s AND group_id = %s",
                (resource_id, group_id),
            )
            affected = cur.rowcount
        conn.commit()
        return affected > 0
    finally:
        _pg_put_conn(conn)


def replace_resource_shares(
    share_table: str,
    fk: str,
    resource_id: str,
    group_ids: Iterable[str],
) -> None:
    """Replace all sharing rows for a resource."""
    conn = _pg_get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {share_table} WHERE {fk} = %s", (resource_id,))
            for gid in set(group_ids):
                cur.execute(
                    f"INSERT INTO {share_table} ({fk}, group_id) VALUES (%s, %s) "
                    f"ON CONFLICT DO NOTHING",
                    (resource_id, gid),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pg_put_conn(conn)
