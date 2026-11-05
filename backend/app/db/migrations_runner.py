"""
migrations_runner.py — Applicazione e tracking delle migration PostgreSQL.

Motivazione (C8-nit)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional

from psycopg import sql as _sql

from app.db import pg_connection as pg_connection_module
from app.config import settings
from app.utils.logger import get_logger


logger = get_logger(__name__)


# Directory relativa al backend root (parent di app/
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"


# Identifier safety: lo schema name viene da env (DB_SCHEMA), quindi non e'
# input utente, ma applichiamo comunque whitelist + psycopg identifier
# quoting per non string-formattare mai identifier SQL
_SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SUPPORTED_SCHEMA = "plenora"
_PG_UNDEFINED_TABLE = "42P01"
_PG_INVALID_SCHEMA = "3F000"


@dataclass(frozen=True)
class MigrationStatus:
    """Deterministic view of migration alignment for bootstrap/readiness."""

    state: str
    migration_files: List[str]
    applied_migrations: List[str]
    pending_migrations: List[str]
    unknown_applied_migrations: List[str]
    tracking_table_present: bool

    @property
    def aligned(self) -> bool:
        return self.state == "migrated"


def _resolved_schema() -> str:
    """Schema applicativo come da env (default `plenora`)."""
    return str(settings.get("DB_SCHEMA") or settings.DEFAULTS["DB_SCHEMA"])


def _assert_supported_schema(name: str) -> None:
    if name != _SUPPORTED_SCHEMA:
        raise RuntimeError(
            f"DB_SCHEMA={name!r} non supportato: le migration SQL attuali "
            f"referenziano esplicitamente lo schema {_SUPPORTED_SCHEMA!r}. "
            "Imposta DB_SCHEMA=plenora oppure rendi le migration schema-parametriche."
        )


def ensure_schema(schema: Optional[str] = None) -> str:
    """Crea lo schema applicativo se non esiste.

    Bootstrap clean-install: senza questo step, su DB vuoto il successivo
    `CREATE TABLE <DB_SCHEMA>.schema_migrations` fallisce con
    `psycopg.errors.InvalidSchemaName` quando lo schema non esiste.
    Lo schema deve esistere prima della creazione di `schema_migrations`
    e prima delle migration `.sql` che lo referenziano.

    Identifier quoting via `psycopg.sql.Identifier`: anche se DB_SCHEMA
    non viene da input utente, evitiamo string-format di identifier SQL.
    """
    name = schema if schema is not None else _resolved_schema()
    if not _SCHEMA_NAME_PATTERN.match(name):
        raise RuntimeError(
            f"DB_SCHEMA={name!r} non valido: atteso "
            f"[A-Za-z_][A-Za-z0-9_]*"
        )
    _assert_supported_schema(name)

    stmt = _sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
        _sql.Identifier(name)
    )
    conn = pg_connection_module.get_connection()
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
        pg_connection_module.put_connection(conn)
    return name


def _schema_name(schema: Optional[str] = None) -> str:
    """Ritorna lo schema validato da usare per oggetti schema-qualified."""
    name = schema if schema is not None else _resolved_schema()
    if not _SCHEMA_NAME_PATTERN.match(name):
        raise RuntimeError(
            f"DB_SCHEMA={name!r} non valido: atteso "
            f"[A-Za-z_][A-Za-z0-9_]*"
        )
    _assert_supported_schema(name)
    return name


def _schema_migrations_table(schema: Optional[str] = None):
    """Identifier composto `<schema>.schema_migrations` con quoting sicuro."""
    return _sql.Identifier(_schema_name(schema), "schema_migrations")


def _create_tracking_table_statement(schema: Optional[str] = None):
    """Statement CREATE TABLE per la tracking table nello schema configurato."""
    return _sql.SQL(
        """
        CREATE TABLE IF NOT EXISTS {} (
            filename   TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    ).format(_schema_migrations_table(schema))


def _is_missing_tracking_table_error(exc: Exception) -> bool:
    """Return True for clean-install pre-tracking-table SQL errors."""
    pgcode = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    return pgcode in {_PG_UNDEFINED_TABLE, _PG_INVALID_SCHEMA}


def list_migration_files() -> List[str]:
    """Ritorna i nomi file `.sql` in MIGRATIONS_DIR, ordinati lessicograficamente."""
    if not MIGRATIONS_DIR.is_dir():
        return []
    return sorted(
        p.name for p in MIGRATIONS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() == ".sql"
    )


def _read_applied_migrations() -> tuple[List[str], bool]:
    """Read applied migrations and whether tracking exists.

    Missing schema/table is the deterministic empty-DB bootstrap state.
    Other DB errors are propagated so callers do not silently collapse
    introspection failures into "nothing applied".
    """
    conn = pg_connection_module.get_connection()
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
        if not _is_missing_tracking_table_error(e):
            raise
        # Se la tabella/schema non esiste ancora (primo bootstrap),
        # ritorna un empty state esplicito. Il runner creera' schema
        # e tracking table prima di applicare le migration.
        logger.warning(
            f"schema_migrations non leggibile (primo bootstrap?): {e}"
        )
        return [], False
    finally:
        pg_connection_module.put_connection(conn)
    return [r["filename"] for r in rows], True


def list_applied_migrations() -> List[str]:
    """Legge le migration gia' applicate da `<DB_SCHEMA>.schema_migrations`."""
    applied, _tracking_table_present = _read_applied_migrations()
    return applied


def list_pending_migrations() -> List[str]:
    """Ritorna i file non ancora registrati in schema_migrations."""
    files = set(list_migration_files())
    applied = set(list_applied_migrations())
    return sorted(files - applied)


def get_migration_status() -> MigrationStatus:
    """Return empty/pending/misaligned/migrated migration state.

    State meanings:
      - ``empty``: tracking table/schema does not exist yet or has no
        rows while migration files exist;
      - ``pending``: at least one migration file is not recorded;
      - ``misaligned``: tracking contains rows for files no longer on disk;
      - ``migrated``: all files are recorded and no unknown rows exist.
    """
    files = list_migration_files()
    applied, tracking_table_present = _read_applied_migrations()
    file_set = set(files)
    applied_set = set(applied)
    pending = sorted(file_set - applied_set)
    unknown_applied = sorted(applied_set - file_set)

    if unknown_applied:
        state = "misaligned"
    elif pending:
        state = "empty" if not tracking_table_present or not applied else "pending"
    else:
        state = "migrated"

    return MigrationStatus(
        state=state,
        migration_files=files,
        applied_migrations=applied,
        pending_migrations=pending,
        unknown_applied_migrations=unknown_applied,
        tracking_table_present=tracking_table_present,
    )


def _migration_notice_logger(filename: str) -> Callable[[Any], None]:
    """Handler psycopg dei NOTICE/WARNING emessi da una migration.

    Senza handler psycopg scarta i messaggi del server: una migration che
    salta un passo per sicurezza (es. ``RAISE WARNING`` quando i dati
    esistenti impedirebbero un indice unico) resterebbe invisibile. Qui
    finiscono nel log del backend, cioe' nel log dell'installer.
    """

    def _log(diag: Any) -> None:
        severity = str(getattr(diag, "severity_nonlocalized", None)
                       or getattr(diag, "severity", None) or "NOTICE").upper()
        text = f"[MIGRATIONS] {filename}: {severity}: {getattr(diag, 'message_primary', '')}"
        if severity in {"WARNING", "ERROR"}:
            logger.warning(text)
        else:
            logger.info(text)

    return _log


def apply_pending_migrations() -> List[str]:
    """Applica e registra tutte le migration pending. Return l'elenco
    dei file applicati in questa chiamata.

    Raises:
        Exception se una migration fallisce (l'app non deve partire
        con schema inconsistente).
    """
    # Bootstrap clean-install: lo schema applicativo deve esistere prima
    # del CREATE TABLE schema_migrations (che e' schema-qualified). Su DB
    # vuoto senza questo step il runner fallisce con InvalidSchemaName
    schema = ensure_schema()

    # Crea la tabella di tracking se non esiste (idempotente
    conn = pg_connection_module.get_connection()
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
        pg_connection_module.put_connection(conn)

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
        # connection con commit atomico. Se la migration fallisce
        # il rollback copre anche il tracking (non marchiamo come
        # applicata una migration che ha fallito
        conn = pg_connection_module.get_connection()
        notice_handler = _migration_notice_logger(filename)
        try:
            conn.autocommit = False
            conn.add_notice_handler(notice_handler)
            with conn.cursor() as cur:
                cur.execute(sql)
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
            # Ripristina autocommit=False prima di restituire la connection
            # al pool; i caller successivi (es. unit_of_work) devono ricevere conn
            # con autocommit=True, dove `commit()` e' no-op e
            # `rollback()` post-errore non rollbackava nulla —
            # transazionalita' silenziosamente persa. Test runtime:
            # `pool.getconn() → autocommit=True → putconn() →
            # getconn() → autocommit STILL True`.
            # psycopg3 non resetta questo stato per noi.
            try:
                conn.autocommit = False
            except Exception:
                pass
            try:
                conn.remove_notice_handler(notice_handler)
            except ValueError:
                pass
            pg_connection_module.put_connection(conn)

    logger.info(
        f"[MIGRATIONS] Completato: {len(applied_now)} migration applicate"
    )
    return applied_now


_REAL_APPLY_PENDING_MIGRATIONS = apply_pending_migrations
