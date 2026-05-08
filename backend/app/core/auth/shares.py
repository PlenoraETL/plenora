"""Auth-owned CRUD helpers for cross-group sharing rows."""

from __future__ import annotations

from typing import Iterable

from app.core.auth.visibility import _validate_table
from app.db.pg_connection import get_connection as _pg_get_conn
from app.db.pg_connection import put_connection as _pg_put_conn


def _conn():
    """Return a PostgreSQL connection from the pool."""
    return _pg_get_conn()


def list_resource_shares(resource_table: str, resource_id: str) -> list[str]:
    """
    Return the group ids a resource is shared with.
    Includes "*" when shared globally.
    """
    share_table, fk = _validate_table(resource_table)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT group_id FROM {share_table} WHERE {fk} = %s ORDER BY group_id",
                (resource_id,),
            )
            return [row["group_id"] for row in cur.fetchall()]
    finally:
        _pg_put_conn(conn)


def add_resource_share(
    resource_table: str,
    resource_id: str,
    group_id: str,
) -> bool:
    """
    Add one sharing row idempotently.
    Returns True if the row was added, False if it already existed.
    """
    share_table, fk = _validate_table(resource_table)
    conn = _conn()
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


def remove_resource_share(
    resource_table: str,
    resource_id: str,
    group_id: str,
) -> bool:
    """Remove one sharing row. Returns True if a row was removed."""
    share_table, fk = _validate_table(resource_table)
    conn = _conn()
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


def set_resource_shares(
    resource_table: str,
    resource_id: str,
    group_ids: Iterable[str],
) -> None:
    """
    Replace all sharing rows for a resource.
    Pass ["*"] for global sharing, [] to remove all shares.
    """
    share_table, fk = _validate_table(resource_table)
    conn = _conn()
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

