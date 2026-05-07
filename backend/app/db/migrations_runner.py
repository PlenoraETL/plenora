"""
migrations_runner.py — Applicazione e tracking delle migration PostgreSQL.

Motivazione (C8-nit)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional

from app.utils.logger import get_logger


logger = get_logger(__name__)


# Directory relativa al backend root (parent di app/)
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"


# Identifier safety: lo schema name viene da env (DB_SCHEMA), quindi non e'
# input utente, ma applichiamo comunque whitelist + psycopg2 identifier
# quoting per non string-formattare mai identifier SQL.
_SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _resolved_schema() -> str:
    """Schema applicativo come da env (default `plenora`)."""
    return os.getenv("DB_SCHEMA", "plenora")


def ensure_schema(schema: Optional[str] = None) -> str:
    """Crea lo schema applicativo se non esiste.

    Bootstrap clean-install: senza questo step, su DB vuoto il successivo
    `CREATE TABLE <DB_SCHEMA>.schema_migrations` fallisce con
    `psycopg2.errors.InvalidSchemaName` quando lo schema non esiste.
    Lo schema deve esistere prima della creazione di `schema_migrations`
    e prima delle migration `.sql` che lo referenziano.

    Identifier quoting via `psycopg2.sql.Identifier`: anche se DB_SCHEMA
    non viene da input utente, evitiamo string-format di identifier SQL.
    """
    from psycopg2 import sql as _sql

    from app.db.pg_connection import get_connection, put_connection

    name = schema if schema is not None else _resolved_schema()
    if not _SCHEMA_NAME_PATTERN.match(name):
        raise RuntimeError(
            f"DB_SCHEMA={name!r} non valido: atteso "
            f"[A-Za-z_][A-Za-z0-9_]*"
        )

    stmt = _sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
        _sql.Identifier(name)
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(stmt)
        conn.commit()
        logger.info(f"[MIGRATIONS] Schema garantito: {name}")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        put_connection(conn)
    return name


def _schema_name(schema: Optional[str] = None) -> str:
    """Ritorna lo schema validato da usare per oggetti schema-qualified."""
    name = schema if schema is not None else _resolved_schema()
    if not _SCHEMA_NAME_PATTERN.match(name):
        raise RuntimeError(
            f"DB_SCHEMA={name!r} non valido: atteso "
            f"[A-Za-z_][A-Za-z0-9_]*"
        )
    return name


def _schema_migrations_table(schema: Optional[str] = None):
    """Identifier composto `<schema>.schema_migrations` con quoting sicuro."""
    from psycopg2 import sql as _sql

    return _sql.Identifier(_schema_name(schema), "schema_migrations")


def _create_tracking_table_statement(schema: Optional[str] = None):
    """Statement CREATE TABLE per la tracking table nello schema configurato."""
    from psycopg2 import sql as _sql

    return _sql.SQL(
        """
        CREATE TABLE IF NOT EXISTS {} (
            filename   TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    ).format(_schema_migrations_table(schema))


def list_migration_files() -> List[str]:
    """Ritorna i nomi file `.sql` in MIGRATIONS_DIR, ordinati lessicograficamente."""
    if not MIGRATIONS_DIR.is_dir():
        return []
    return sorted(
        p.name for p in MIGRATIONS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() == ".sql"
    )


def list_applied_migrations() -> List[str]:
    """Legge le migration gia' applicate da `<DB_SCHEMA>.schema_migrations`."""
    # Import locale per evitare side-effect al load del modulo
    from app.db.pg_connection import get_connection, put_connection
    from psycopg2 import sql as _sql

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                _sql.SQL("SELECT filename FROM {} ORDER BY filename").format(
                    _schema_migrations_table()
                )
            )
            rows = cur.fetchall()
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        # Se la tabella non esiste ancora (primo bootstrap), ritorna [].
        # Il runner la creera' subito dopo.
        logger.warning(
            f"schema_migrations non leggibile (primo bootstrap?): {e}"
        )
        return []
    finally:
        put_connection(conn)
    return [r["filename"] for r in rows]


def list_pending_migrations() -> List[str]:
    """Ritorna i file non ancora registrati in schema_migrations."""
    files = set(list_migration_files())
    applied = set(list_applied_migrations())
    return sorted(files - applied)


def apply_pending_migrations() -> List[str]:
    """Applica e registra tutte le migration pending. Return l'elenco
    dei file applicati in questa chiamata.

    Raises:
        Exception se una migration fallisce (l'app non deve partire
        con schema inconsistente).
    """
    # Import locale (pg_connection potrebbe non essere init-ato al module load)
    from app.db.pg_connection import get_connection, put_connection

    # Bootstrap clean-install: lo schema applicativo deve esistere prima
    # del CREATE TABLE schema_migrations (che e' schema-qualified). Su DB
    # vuoto senza questo step il runner fallisce con InvalidSchemaName.
    schema = ensure_schema()

    # Crea la tabella di tracking se non esiste (idempotente)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_create_tracking_table_statement(schema))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        put_connection(conn)

    pending = list_pending_migrations()
    if not pending:
        logger.info("[MIGRATIONS] Nessuna migration pending")
        return []

    applied_now: List[str] = []
    for filename in pending:
        filepath = MIGRATIONS_DIR / filename
        try:
            sql = filepath.read_text(encoding="utf-8")
        except Exception as e:
            raise RuntimeError(
                f"[MIGRATIONS] impossibile leggere {filename}: {e}"
            ) from e

        logger.info(f"[MIGRATIONS] Applico: {filename}")
        # Eseguiamo la migration + l'INSERT di tracking in UNA sola
        # connection con commit atomico. Se la migration fallisce,
        # il rollback copre anche il tracking (non marchiamo come
        # applicata una migration che ha fallito).
        conn = get_connection()
        try:
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute(sql)
                from psycopg2 import sql as _sql
                cur.execute(
                    _sql.SQL(
                        "INSERT INTO {} (filename) VALUES (%s) "
                        "ON CONFLICT (filename) DO NOTHING"
                    ).format(_schema_migrations_table(schema)),
                    (filename,),
                )
            conn.commit()
            applied_now.append(filename)
            logger.info(f"[MIGRATIONS] OK: {filename}")
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            raise RuntimeError(
                f"[MIGRATIONS] fallita {filename}: {e}"
            ) from e
        finally:
            try:
                conn.autocommit = True
            except Exception:
                pass
            put_connection(conn)

    logger.info(
        f"[MIGRATIONS] Completato: {len(applied_now)} migration applicate"
    )
    return applied_now
