"""
migrations_runner.py — Applicazione e tracking delle migration PostgreSQL.

Motivazione (C8-nit)
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional

import psycopg

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

# Migration fuori transazione (conferma UI A-K11, 06/11/2026): un indice su
# una tabella grande e scritta di continuo (log_runs) si crea con
# CREATE INDEX CONCURRENTLY, che non blocca le scritture ma non puo' girare
# dentro una transazione. Il file lo dichiara nella PRIMA riga; ogni
# istruzione gira da sola in autocommit e puo' essere solo
# CREATE INDEX CONCURRENTLY IF NOT EXISTS / DROP INDEX CONCURRENTLY IF EXISTS
# (rieseguibili). Il tracking si scrive dopo l'ultima: un'installazione
# interrotta riesegue il file. Un indice rimasto INVALID da un'interruzione
# si toglie prima di ricrearlo (IF NOT EXISTS lo salterebbe).
#
# Piu' istanze e attese (UI conferma finale F3/F4, 06/11/2026). Tutto il giro
# (schema, tracking, lettura delle pending, controllo degli indici INVALID,
# applicazione) gira sotto UN advisory lock di sessione (`migrations_lock`):
# una seconda istanza che parte insieme aspetta, poi rilegge le pending e non
# trova piu' niente da fare; non vede mai come INVALID l'indice che l'altra
# sta ancora costruendo, perche' l'INVALID si guarda solo col lock in mano.
# Attese dichiarate (config.py DEFAULTS): il lock al piu'
# MIGRATIONS_LOCK_WAIT_SECONDS; un CREATE INDEX CONCURRENTLY aspetta le
# transazioni aperte al piu' MIGRATIONS_INDEX_WAIT_SECONDS (`lock_timeout`),
# poi l'indice rimasto INVALID si toglie (subito, o al prossimo avvio col lock
# se anche la rimozione aspetta) e l'avvio fallisce col motivo:
# nessuna attesa senza fine e mai un indice INVALID saltato da IF NOT EXISTS.
NO_TRANSACTION_DIRECTIVE = "-- plenora:migration no-transaction"
_NO_TRANSACTION_STATEMENT = re.compile(
    r"^(CREATE\s+(UNIQUE\s+)?INDEX\s+CONCURRENTLY\s+IF\s+NOT\s+EXISTS"
    r"|DROP\s+INDEX\s+CONCURRENTLY\s+IF\s+EXISTS)\s",
    re.IGNORECASE,
)
_CREATE_INDEX_NAME = re.compile(
    r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+CONCURRENTLY\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)\s",
    re.IGNORECASE,
)


# Chiave dell'advisory lock delle migrazioni ("plenora" in ASCII, int64).
MIGRATIONS_LOCK_KEY = 0x706C656E6F7261
_PG_LOCK_NOT_AVAILABLE = "55P03"
_PG_QUERY_CANCELED = "57014"


def _seconds_setting(name: str) -> int:
    raw = settings.get(name, settings.DEFAULTS[name])
    return max(1, int(float(raw if raw is not None else settings.DEFAULTS[name])))


def _is_lock_timeout(exc: BaseException) -> bool:
    pgcode = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    return pgcode in {_PG_LOCK_NOT_AVAILABLE, _PG_QUERY_CANCELED}


def _set_lock_timeout(cur: Any, seconds: Optional[int]) -> None:
    """`lock_timeout` della sessione (None = nessun limite, il default)."""
    value = f"{int(seconds) * 1000}ms" if seconds else "0"
    cur.execute("SELECT set_config('lock_timeout', %s, false)", (value,))


@contextmanager
def migrations_lock() -> Iterator[None]:
    """Advisory lock di sessione: una sola istanza applica le migrazioni.

    Connessione propria in autocommit, tenuta per tutto il giro; il lock si
    rilascia alla fine (anche su errore) e comunque muore con la sessione.
    Attesa al piu' MIGRATIONS_LOCK_WAIT_SECONDS, poi RuntimeError.
    """
    wait = _seconds_setting("MIGRATIONS_LOCK_WAIT_SECONDS")
    conn = pg_connection_module.get_connection()
    locked = False
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            _set_lock_timeout(cur, wait)
            try:
                cur.execute("SELECT pg_advisory_lock(%s)", (MIGRATIONS_LOCK_KEY,))
            except psycopg.Error as exc:
                if _is_lock_timeout(exc):
                    raise RuntimeError(
                        "[MIGRATIONS] un'altra istanza sta applicando le migrazioni "
                        f"da piu' di {wait} s (MIGRATIONS_LOCK_WAIT_SECONDS): "
                        "avvio interrotto, riprovare quando ha finito"
                    ) from exc
                raise
            locked = True
            _set_lock_timeout(cur, None)
        logger.info("[MIGRATIONS] lock delle migrazioni preso")
        yield
    finally:
        try:
            with conn.cursor() as cur:
                if locked:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (MIGRATIONS_LOCK_KEY,))
                _set_lock_timeout(cur, None)
        except psycopg.Error:
            logger.warning("[MIGRATIONS] rilascio del lock non riuscito (muore con la sessione)", exc_info=True)
        try:
            conn.autocommit = False
        except psycopg.Error:
            pass  # connessione rotta: il pool la scarta
        pg_connection_module.put_connection(conn)


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


def is_no_transaction_migration(sql: str) -> bool:
    """True se la prima riga del file dichiara la migration fuori transazione."""
    first = sql.lstrip("\ufeff").splitlines()[0].strip() if sql.strip() else ""
    return first == NO_TRANSACTION_DIRECTIVE


def no_transaction_statements(sql: str) -> List[str]:
    """Le istruzioni di una migration fuori transazione (commenti tolti).

    Solo istruzioni ammesse (``_NO_TRANSACTION_STATEMENT``): altrimenti
    ``RuntimeError`` prima di eseguire qualunque cosa.
    """
    code = re.sub(r"--[^\n]*", "", sql)
    statements = [" ".join(part.split()) for part in code.split(";")]
    statements = [stmt for stmt in statements if stmt]
    for stmt in statements:
        if not _NO_TRANSACTION_STATEMENT.match(stmt):
            raise RuntimeError(
                "[MIGRATIONS] istruzione non ammessa fuori transazione "
                f"(solo CREATE/DROP INDEX CONCURRENTLY IF [NOT] EXISTS): {stmt[:120]}"
            )
    return statements


def _drop_if_invalid(cur: Any, index_name: str) -> bool:
    """Toglie l'indice se e' INVALID (CONCURRENTLY interrotto). Solo col lock."""
    cur.execute(
        "SELECT count(*) AS invalid FROM pg_index i "
        "WHERE i.indexrelid = to_regclass(%s) AND NOT i.indisvalid",
        (index_name,),
    )
    row = cur.fetchone()
    invalid = row.get("invalid") if hasattr(row, "get") else (row[0] if row else 0)
    if invalid:
        cur.execute(
            _sql.SQL("DROP INDEX CONCURRENTLY IF EXISTS {}").format(
                _sql.Identifier(index_name)
            )
        )
    return bool(invalid)


def _apply_no_transaction_migration(conn: Any, sql: str, schema: str, filename: str) -> None:
    """Esegue ogni istruzione da sola in autocommit, poi il tracking.

    Chiamata solo dentro `migrations_lock` (apply_pending_migrations).
    """
    statements = no_transaction_statements(sql)
    wait = _seconds_setting("MIGRATIONS_INDEX_WAIT_SECONDS")
    conn.autocommit = True
    with conn.cursor() as cur:
        _set_lock_timeout(cur, wait)
        try:
            for stmt in statements:
                index = _CREATE_INDEX_NAME.match(stmt)
                try:
                    if index:
                        # Un CONCURRENTLY interrotto lascia l'indice INVALID:
                        # si toglie, poi si ricrea.
                        _drop_if_invalid(cur, index.group(1))
                    cur.execute(stmt)
                except psycopg.Error as exc:
                    if not _is_lock_timeout(exc):
                        raise
                    # Transazioni aperte oltre l'attesa dichiarata. L'indice
                    # rimasto INVALID si prova a togliere subito; anche la
                    # rimozione CONCURRENTLY aspetta le stesse transazioni,
                    # quindi se non riesce lo toglie il prossimo avvio (col
                    # lock) prima di ricrearlo. Ogni attesa ha il suo tetto.
                    leftover = "indice tolto"
                    if index:
                        try:
                            _drop_if_invalid(cur, index.group(1))
                        except psycopg.Error as drop_exc:
                            if not _is_lock_timeout(drop_exc):
                                raise
                            leftover = "indice INVALID lasciato (lo toglie il prossimo avvio)"
                    name = index.group(1) if index else stmt[:60]
                    raise RuntimeError(
                        f"[MIGRATIONS] {filename}: {name} aspetta transazioni aperte "
                        f"da piu' di {wait} s (MIGRATIONS_INDEX_WAIT_SECONDS); "
                        f"{leftover}; si riprova al prossimo avvio: chiudere le "
                        "transazioni lunghe"
                    ) from exc
        finally:
            _set_lock_timeout(cur, None)
        cur.execute(
            _sql.SQL(
                "INSERT INTO {} (filename) VALUES (%s) "
                "ON CONFLICT (filename) DO NOTHING"
            ).format(_schema_migrations_table(schema)),
            (filename,),
        )


def apply_pending_migrations() -> List[str]:
    """Applica e registra tutte le migration pending. Return l'elenco
    dei file applicati in questa chiamata.

    Tutto sotto `migrations_lock` (F3/F4): con piu' istanze una sola applica,
    le altre aspettano e poi rileggono le pending.

    Raises:
        Exception se una migration fallisce (l'app non deve partire
        con schema inconsistente).
    """
    with migrations_lock():
        return _apply_pending_migrations_locked()


def _apply_pending_migrations_locked() -> List[str]:
    """Il giro delle migrazioni; solo con il lock in mano."""
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
            conn.add_notice_handler(notice_handler)
            if is_no_transaction_migration(sql):
                _apply_no_transaction_migration(conn, sql, schema, filename)
            else:
                conn.autocommit = False
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
