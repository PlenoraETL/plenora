"""
URBY-ETL — Helper allowed_groups (cross-group sharing)

Centralizza la logica per le risorse "shareable":
    connections, datasources, eseguibili, api_connections, arcgis_connections.

Modello dati
------------
- Ogni risorsa appartiene al suo gruppo proprietario (campo `group_id`).
- Le 5 tabelle di join `*_shares` permettono di condividerla con altri gruppi.
- "ALL" è rappresentato dalla riga magica `group_id = '*'` nella join table.

Visibilità di una risorsa al gruppo X:
    resource.group_id == X
    OR EXISTS share WHERE share.group_id == X
    OR EXISTS share WHERE share.group_id == '*'

Superadmin bypassa sempre il filtro (vede tutto).

API
---
- ``share_table_for(resource_table)`` → nome della join table
- ``fk_column_for(resource_table)`` → nome FK in join table
- ``visible_resource_ids(resource_table, user_group_id, is_superadmin)``
    → set di id visibili (per ownership check rapidi)
- ``set_resource_shares(resource_table, resource_id, group_ids)``
    → sostituisce le righe di sharing per quella risorsa
- ``add_resource_share(resource_table, resource_id, group_id)``
- ``remove_resource_share(resource_table, resource_id, group_id)``
- ``list_resource_shares(resource_table, resource_id)``
    → lista group_id che condividono la risorsa
- ``build_visible_where_clause(resource_table, resource_alias='r')``
    → frammento SQL `(r.group_id = ? OR EXISTS (...))` con parametri
- ``MAGIC_ALL`` → costante "*"
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Optional

import app.config  # noqa: F401  garantisce caricamento .env
from app.config import settings


# ============================================================
# COSTANTI
# ============================================================

MAGIC_ALL = "*"

# Mapping resource_table -> (share_table, fk_column)
_SHAREABLE: dict[str, tuple[str, str]] = {
    "connections":        ("connection_shares",         "connection_id"),
    "datasources":        ("datasource_shares",         "datasource_id"),
    "eseguibili":         ("eseguibile_shares",         "eseguibile_id"),
    "api_connections":    ("api_connection_shares",     "api_connection_id"),
    "arcgis_connections": ("arcgis_connection_shares",  "arcgis_connection_id"),
    "file_sources":       ("file_source_shares",        "file_source_id"),
}


# ============================================================
# UTILS
# ============================================================

def _conn() -> sqlite3.Connection:
    db_path = settings.DATA_DIR / "config.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _validate_table(resource_table: str) -> tuple[str, str]:
    if resource_table not in _SHAREABLE:
        raise ValueError(
            f"Tabella '{resource_table}' non è shareable. "
            f"Validi: {list(_SHAREABLE)}"
        )
    return _SHAREABLE[resource_table]


def share_table_for(resource_table: str) -> str:
    """Ritorna il nome della join table per la risorsa."""
    return _validate_table(resource_table)[0]


def fk_column_for(resource_table: str) -> str:
    """Ritorna il nome della colonna FK nella join table."""
    return _validate_table(resource_table)[1]


def is_shareable(resource_table: str) -> bool:
    return resource_table in _SHAREABLE


# ============================================================
# QUERY DI VISIBILITÀ
# ============================================================

def build_visible_where_clause(
    resource_table: str,
    resource_alias: str = "r",
) -> tuple[str, list]:
    """
    Costruisce un frammento WHERE per filtrare le risorse visibili a un gruppo.

    Uso tipico:
        clause, params = build_visible_where_clause('connections', 'c')
        sql = f"SELECT * FROM connections c WHERE {clause}"
        cursor.execute(sql, [user_group_id, user_group_id, *params])

    NOTA: la clausola contiene 2 placeholder `?` per `user_group_id`,
    il chiamante deve passare il group_id 2 volte all'inizio.

    Per superadmin (no filtro): non chiamare questa funzione, fai una query
    senza WHERE su group_id.
    """
    share_table, fk = _validate_table(resource_table)
    clause = (
        f"({resource_alias}.group_id = ? "
        f"OR EXISTS (SELECT 1 FROM {share_table} s "
        f"WHERE s.{fk} = {resource_alias}.id "
        f"AND (s.group_id = ? OR s.group_id = '{MAGIC_ALL}')))"
    )
    return clause, []


def visible_resource_ids(
    resource_table: str,
    user_group_id: str,
    is_superadmin: bool = False,
) -> set[str]:
    """
    Ritorna l'insieme di tutti gli id della tabella visibili al gruppo X.

    Visibili = di proprietà del gruppo + condivisi (puntualmente o globalmente).
    Per superadmin: ritorna TUTTI gli id (nessun filtro).

    Pensato per controlli di ownership rapidi:
        if resource_id not in visible_resource_ids(...):
            raise 403
    """
    share_table, fk = _validate_table(resource_table)
    conn = _conn()
    try:
        if is_superadmin:
            cur = conn.execute(f"SELECT id FROM {resource_table}")
            return {row[0] for row in cur.fetchall()}

        # Owned + shared
        cur = conn.execute(
            f"""
            SELECT id FROM {resource_table} WHERE group_id = ?
            UNION
            SELECT {fk} FROM {share_table}
            WHERE group_id = ? OR group_id = ?
            """,
            (user_group_id, user_group_id, MAGIC_ALL),
        )
        return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def is_resource_visible(
    resource_table: str,
    resource_id: str,
    user_group_id: str,
    is_superadmin: bool = False,
    resource_owner_group_id: Optional[str] = None,
) -> bool:
    """
    True se la risorsa è visibile al gruppo dell'utente (owned o shared).

    Se hai già caricato il record e conosci il suo group_id, passalo come
    `resource_owner_group_id` per evitare una query sulla tabella sorgente.
    """
    if is_superadmin:
        return True

    share_table, fk = _validate_table(resource_table)
    conn = _conn()
    try:
        # Owner check (se non già fornito)
        if resource_owner_group_id is None:
            cur = conn.execute(
                f"SELECT group_id FROM {resource_table} WHERE id = ?",
                (resource_id,),
            )
            row = cur.fetchone()
            if not row:
                return False
            resource_owner_group_id = row[0]

        if resource_owner_group_id == user_group_id:
            return True

        # Share check
        cur = conn.execute(
            f"""
            SELECT 1 FROM {share_table}
            WHERE {fk} = ?
              AND (group_id = ? OR group_id = ?)
            LIMIT 1
            """,
            (resource_id, user_group_id, MAGIC_ALL),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


# ============================================================
# CRUD SU RIGHE DI SHARING
# ============================================================

def list_resource_shares(resource_table: str, resource_id: str) -> list[str]:
    """
    Ritorna i group_id con cui la risorsa è condivisa.
    Include "*" se condivisa globalmente.
    """
    share_table, fk = _validate_table(resource_table)
    conn = _conn()
    try:
        cur = conn.execute(
            f"SELECT group_id FROM {share_table} WHERE {fk} = ? ORDER BY group_id",
            (resource_id,),
        )
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def add_resource_share(
    resource_table: str, resource_id: str, group_id: str,
) -> bool:
    """
    Aggiunge una riga di sharing. Idempotente (INSERT OR IGNORE).
    Ritorna True se la riga è stata aggiunta, False se già presente.
    """
    share_table, fk = _validate_table(resource_table)
    conn = _conn()
    try:
        cur = conn.execute(
            f"INSERT OR IGNORE INTO {share_table} ({fk}, group_id) VALUES (?, ?)",
            (resource_id, group_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def remove_resource_share(
    resource_table: str, resource_id: str, group_id: str,
) -> bool:
    """
    Rimuove una riga di sharing. Ritorna True se rimossa, False se non esisteva.
    """
    share_table, fk = _validate_table(resource_table)
    conn = _conn()
    try:
        cur = conn.execute(
            f"DELETE FROM {share_table} WHERE {fk} = ? AND group_id = ?",
            (resource_id, group_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_resource_shares(
    resource_table: str,
    resource_id: str,
    group_ids: Iterable[str],
) -> None:
    """
    Sostituisce TUTTE le righe di sharing per la risorsa con quelle fornite.
    Pattern delete-then-insert in transazione.

    Pass `["*"]` per condivisione globale.
    Pass `[]` per rimuovere tutte le condivisioni (la risorsa resta visibile
    solo al suo gruppo proprietario).
    """
    share_table, fk = _validate_table(resource_table)
    conn = _conn()
    try:
        conn.execute("BEGIN")
        conn.execute(f"DELETE FROM {share_table} WHERE {fk} = ?", (resource_id,))
        for gid in set(group_ids):
            conn.execute(
                f"INSERT OR IGNORE INTO {share_table} ({fk}, group_id) VALUES (?, ?)",
                (resource_id, gid),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
