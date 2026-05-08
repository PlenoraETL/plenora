"""Compatibility surface plus CRUD helpers for cross-group sharing rows."""

from __future__ import annotations

from typing import Iterable

from app.core.auth.visibility import (
    MAGIC_ALL,
    ShareableResourceOwner,
    _SHAREABLE,
    _validate_table,
    build_visible_where_clause,
    fk_column_for,
    is_resource_visible,
    is_shareable,
    load_shareable_resource_owner,
    share_table_for,
    visible_resource_ids,
)
from app.db.pg_connection import get_connection as _pg_get_conn
from app.db.pg_connection import put_connection as _pg_put_conn


def _conn():
    """Ritorna una connessione dal pool PostgreSQL."""
    return _pg_get_conn()


def list_resource_shares(resource_table: str, resource_id: str) -> list[str]:
    """
    Ritorna i group_id con cui la risorsa e' condivisa.
    Include "*" se condivisa globalmente.
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
    resource_table: str, resource_id: str, group_id: str,
) -> bool:
    """
    Aggiunge una riga di sharing. Idempotente (INSERT OR IGNORE).
    Ritorna True se la riga e' stata aggiunta, False se gia' presente.
    """
    share_table, fk = _validate_table(resource_table)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {share_table} ({fk}, group_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (resource_id, group_id),
            )
            affected = cur.rowcount
        conn.commit()
        return affected > 0
    finally:
        _pg_put_conn(conn)


def remove_resource_share(
    resource_table: str, resource_id: str, group_id: str,
) -> bool:
    """
    Rimuove una riga di sharing. Ritorna True se rimossa, False se non esisteva.
    """
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
    Sostituisce TUTTE le righe di sharing per la risorsa con quelle fornite.
    Pattern delete-then-insert in transazione.

    Pass `["*"]` per condivisione globale.
    Pass `[]` per rimuovere tutte le condivisioni.
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
