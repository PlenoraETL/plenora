"""
pg_connection.py — PostgreSQL connection pool per PLENORA-ETL.

Canonical entry point per i query helper DAL (`fetchone`, `fetchall`,
"""

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import psycopg2.pool

from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Configurazione ──────────────────────────────────────────────────────────

_DATABASE_URL: Optional[str] = None
_DB_SCHEMA = "plenora"


def _read_int_env(name: str, default: int) -> int:
    """Legge una env var come intero. Se la conversione fallisce
    (stringa non numerica, vuota), solleva RuntimeError con messaggio
    esplicito invece di propagare ValueError oscuro."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(
            f"Env var {name}={raw!r} non e' un intero valido"
        )


_POOL_MIN = 2
_POOL_MAX = 12

# C7: idle_in_transaction timeout applicato tramite GUC per-connection
# Evita zombie da transazioni applicative leaked (codice che apre BEGIN
# e non committa/rollbacka mai) senza richiedere modifiche a
# postgresql.conf. Scope: solo le connessioni del nostro pool
# Default 300000ms = 5min. Imposta 0 per disabilitare
_IDLE_IN_TRANSACTION_MS = 300000
_POOL_CONFIG_LOADED = False


# C7 fix-nit: guardrail espliciti sui parametri del pool. Senza questi
# check, valori malformati (MIN>MAX, negativi, 0) si propagano fino a
# ThreadedConnectionPool dove falliscono con errori poco leggibili
# Fail fast all'import con messaggio diagnostico
def _validate_pool_params(
    *,
    pool_min: int,
    pool_max: int,
    idle_in_transaction_ms: int,
) -> None:
    errors = []
    if pool_min < 0:
        errors.append(f"DB_POOL_MIN={pool_min} deve essere >= 0")
    if pool_max < 1:
        errors.append(f"DB_POOL_MAX={pool_max} deve essere >= 1")
    if pool_min > pool_max:
        errors.append(
            f"DB_POOL_MIN={pool_min} > DB_POOL_MAX={pool_max}: "
            f"il minimo non puo' superare il massimo"
        )
    if idle_in_transaction_ms < 0:
        errors.append(
            f"DB_IDLE_IN_TRANSACTION_TIMEOUT_MS={idle_in_transaction_ms} "
            f"deve essere >= 0 (0 = disabilitato)"
        )
    if errors:
        raise RuntimeError(
            "Configurazione pool PG invalida:\n  - "
            + "\n  - ".join(errors)
        )


def _load_pool_config() -> None:
    """Load and validate PostgreSQL pool config lazily.

    Importing this module must be passive: unit tests and import-time
    dependency checks can load `app.db.pg_connection` without a
    DATABASE_URL. A clear error is raised only when a DB operation tries
    to initialize the pool.
    """
    global _DATABASE_URL, _DB_SCHEMA, _POOL_MIN, _POOL_MAX
    global _IDLE_IN_TRANSACTION_MS, _POOL_CONFIG_LOADED

    if _POOL_CONFIG_LOADED:
        return

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato. Imposta la variabile d'ambiente.")

    pool_min = _read_int_env("DB_POOL_MIN", 2)
    pool_max = _read_int_env("DB_POOL_MAX", 12)
    idle_in_transaction_ms = _read_int_env(
        "DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", 300000
    )
    _validate_pool_params(
        pool_min=pool_min,
        pool_max=pool_max,
        idle_in_transaction_ms=idle_in_transaction_ms,
    )

    _DATABASE_URL = database_url
    _DB_SCHEMA = os.getenv("DB_SCHEMA", "plenora")
    _POOL_MIN = pool_min
    _POOL_MAX = pool_max
    _IDLE_IN_TRANSACTION_MS = idle_in_transaction_ms
    _POOL_CONFIG_LOADED = True

# ── Pool singleton ──────────────────────────────────────────────────────────

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()
_transaction_state = threading.local()


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Lazy-init del pool. Thread-safe via lock.

    NB: il pool e' singleton **per-processo**. Multiple uvicorn worker
    / celery children → pool separati. Vedi docstring modulo per il
    sizing aggregato.
    """
    global _pool
    if _pool is None or _pool.closed:
        with _pool_lock:
            if _pool is None or _pool.closed:
                _load_pool_config()
                # C7: GUC per-connection via startup `options` parameter
                # c search_path=... → schema predefinito
                # c idle_in_transaction_session_timeout=Nms → zombie guard
                opts = [f"-c search_path={_DB_SCHEMA},public"]
                if _IDLE_IN_TRANSACTION_MS > 0:
                    opts.append(
                        f"-c idle_in_transaction_session_timeout="
                        f"{_IDLE_IN_TRANSACTION_MS}"
                    )
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    _POOL_MIN, _POOL_MAX, _DATABASE_URL,
                    options=" ".join(opts),
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
                # Log aggregato stimato per dare visibilita' all'operatore
                # sulle connessioni PG consumate al picco. Usa la stessa
                # formula del docstring modulo: N_processi = uvicorn_workers
                # + celery_concurrency + 1 (beat). Il parent celery NON
                # viene conteggiato perche' non emette query PG in prod
                # e' solo coordinator; i query vanno attraverso le child
                # prefork). Se UVICORN_WORKERS/CELERY_CONCURRENCY non sono
                # in env, fallback conservativo a 1 uvicorn + 0 celery
                try:
                    uvi = _read_int_env("UVICORN_WORKERS", 1)
                    cel = _read_int_env("CELERY_CONCURRENCY", 0)
                    n_proc = uvi + cel + 1  # +1 per beat
                    aggregate_max = n_proc * _POOL_MAX
                    logger.info(
                        f"PG pool inizializzato: {_POOL_MIN}-{_POOL_MAX} "
                        f"conn (per-process), schema={_DB_SCHEMA}, "
                        f"idle_in_trans_timeout={_IDLE_IN_TRANSACTION_MS}ms, "
                        f"aggregato stimato ~{aggregate_max} conn "
                        f"({n_proc} proc × {_POOL_MAX}) "
                        f"[uvicorn={uvi} + celery={cel} + beat=1]"
                    )
                except Exception:
                    logger.info(
                        f"PG pool inizializzato: {_POOL_MIN}-{_POOL_MAX} "
                        f"conn (per-process), schema={_DB_SCHEMA}, "
                        f"idle_in_trans_timeout={_IDLE_IN_TRANSACTION_MS}ms"
                    )
    return _pool


def get_connection():
    """Prende una connessione dal pool. Da usare con `put_connection()` dopo."""
    return _get_pool().getconn()


def put_connection(conn):
    """Rimette una connessione nel pool."""
    try:
        _get_pool().putconn(conn)
    except Exception as e:
        logger.warning(
            "putconn() fallito, connessione non restituita al pool: %s: %s",
            type(e).__name__,
            e,
        )


def close_pool():
    """Chiude il pool (shutdown). Chiamare solo allo stop dell'app."""
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        _pool = None


def close_connection():
    """No-op per PostgreSQL: il pool gestisce le connessioni.

    Mantiene compatibilità con DBConnectionCleanupMiddleware che chiama
    close_connection dopo ogni request. Con il pool non serve chiudere
    esplicitamente — la connessione viene restituita al pool automaticamente.
    """
    pass


@dataclass(frozen=True)
class PoolHeadroom:
    """Snapshot tipato del pool psycopg2 per /ready diagnostic.

     incapsula i campi prima esposti
    via accesso ai simboli privati `_get_pool` / `_POOL_MAX`. Il
    chiamante (`app.observability.health.check_pool_headroom`) consuma
    questo dataclass invece di toccare gli internals di psycopg2.

    Fields:
        max: dimensione massima del pool (`DB_POOL_MAX`).
        in_use: numero di connessioni attualmente checked-out
            (`pool._used`). Best-effort: psycopg2 non espone un'API
            ufficiale, accediamo all'attribute interno con fallback
            a 0 se l'introspezione fallisce.
        free: numero di connessioni idle nel pool (`pool._pool`).
            Stesso caveat di `in_use`.
    """

    max: int
    in_use: int
    free: int

    @property
    def saturated(self) -> bool:
        """True se non c'e' headroom (`in_use >= max`)."""
        return self.in_use >= self.max


def get_pool_headroom() -> PoolHeadroom:
    """Public typed accessor sul pool headroom diagnostic.

    Lazy-init del pool al primo uso (delegato a `_get_pool`).
    Best-effort sull'introspezione: usa `getattr(...)` con default
    vuoto sui due attribute privati di `psycopg2.pool.ThreadedConnectionPool`
    (`_used`, `_pool`) — psycopg2 non espone API ufficiale per
    queste counts; ci adattiamo all'invariante del modulo.

    Raises:
        Qualunque errore di lazy-init del pool (DB unreachable,
        config invalida) propaga al chiamante. Il consumer in
        `app.observability.health.check_pool_headroom` lo cattura
        e mappa a fail-OPEN `(True, "introspection skipped: ...")`
        per preservare il behavior pre-batch-010.
    """
    pool = _get_pool()
    in_use = len(getattr(pool, "_used", {}) or {})
    free = len(getattr(pool, "_pool", []) or [])
    return PoolHeadroom(max=_POOL_MAX, in_use=in_use, free=free)


# ── Query helpers (canonical DAL surface) ────────────────────────────────────

def _adapt_params(sql: str, params: tuple) -> Tuple[str, tuple]:
    """Reject legacy SQLite-style placeholders.

    `pg_connection` is the PostgreSQL boundary and must receive native
    psycopg2 placeholders (`%s`). A global `?` -> `%s` rewrite is unsafe:
    it also rewrites literal question marks and PostgreSQL operators.
    """
    if "?" in sql:
        raise ValueError(
            "pg_connection accepts only native psycopg2 placeholders (%s); "
            "legacy '?' placeholders must be migrated at the caller"
        )
    return sql, params


def fetchone(sql: str, params: tuple = ()) -> Optional[Dict]:
    """Esegue query e ritorna la prima riga come dict, o None."""
    sql, params = _adapt_params(sql, params)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def fetchall(sql: str, params: tuple = ()) -> List[Dict]:
    """Esegue query e ritorna tutte le righe come lista di dict."""
    sql, params = _adapt_params(sql, params)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            conn.commit()
            return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def execute(sql: str, params: tuple = ()):
    """Esegue una query di scrittura (INSERT/UPDATE/DELETE). Ritorna il cursor."""
    sql, params = _adapt_params(sql, params)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            # Wrapper minimale che espone solo `rowcount` + `fetchone`
            # ai chiamanti che storicamente usavano un cursor
            # post-commit. La connessione viene rilasciata dal `finally`
            class _Result:
                def __init__(self, rc):
                    self.rowcount = rc
                def fetchone(self):
                    return None
            return _Result(cur.rowcount)
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def executemany(sql: str, params_list: List[tuple]) -> None:
    """Esegue una query con molti set di parametri."""
    sql, _ = _adapt_params(sql, ())
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, params_list)
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


@contextmanager
def transaction():
    """Context manager per transazioni esplicite.

    Uso:
        with transaction as conn:
            conn.execute("INSERT ...")
            conn.execute("UPDATE ...")
        # auto-commit alla fine, rollback su exception
    """
    if getattr(_transaction_state, "active", False):
        raise RuntimeError(
            "Nested pg_connection.transaction() is not supported. "
            "Pass the existing transaction connection through the call chain "
            "or add explicit savepoint support at this boundary."
        )
    conn = get_connection()
    _transaction_state.active = True
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            put_connection(conn)
        finally:
            _transaction_state.active = False


# ── Init DB (crea tabelle se non esistono) ──────────────────────────────────

def init_db():
    """Verifica che le tabelle esistano su PostgreSQL.

    Non esegue schema SQL inline: le tabelle sono create dalle
    migrations in `backend/migrations/` (entry-point
    `0000_bootstrap_base_tables.sql` + add-on `2026-XX-XX_*.sql`,
    eseguite da `migrations_runner.apply_pending_migrations`).
    Questo init verifica solo la connessione e logga il numero di
    tabelle trovate nello schema applicativo.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = %s",
                (_DB_SCHEMA,)
            )
            count = cur.fetchone()["count"]
            logger.info(f"PostgreSQL DB inizializzato: {count} tabelle in schema '{_DB_SCHEMA}'")
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"[CRASH] PostgreSQL init fallito: {e}", exc_info=True)
        raise
    finally:
        put_connection(conn)
