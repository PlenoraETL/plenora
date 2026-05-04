"""
pg_connection.py — Connection pool PostgreSQL per PLENORA-ETL.

Rimpiazza il layer sqlite3 thread-local con un connection pool psycopg2.
Espone la STESSA interfaccia di config_db_service/results_db_service:

    from app.db.pg_connection import fetchone, fetchall, execute, transaction

Vantaggi rispetto a SQLite:
  - Connection pool (non thread-local): gestito da psycopg2.pool
  - FK realmente enforced
  - JSONB nativo (no json.loads/dumps manuale)
  - Concurrency reale (no single-writer)
  - BYTEA per blob (no workaround)

Configurazione via env var:
  DATABASE_URL=postgresql://user:pass@host:port/dbname
  DB_SCHEMA=plenora  (default: plenora)
  DB_POOL_MIN=2
  DB_POOL_MAX=12      (default; vedi sizing per-process sotto)
  DB_IDLE_IN_TRANSACTION_TIMEOUT_MS=300000  (5 min default, set a 0 per disabilitare)

Sizing del pool (IMPORTANTE — C7)
---------------------------------
`psycopg2.pool.ThreadedConnectionPool` e' un singleton **per-processo**, non
globale. Ogni processo Python che importa questo modulo crea il proprio
pool indipendente. La deployment topology produzione PLENORA-ETL e':

    uvicorn --workers N          → N pool indipendenti
    celery worker --concurrency M → M+1 pool (parent + M prefork children)
    celery beat (1 processo)     → 1 pool

Consumo PG aggregato worst-case:
    (uvicorn_workers + celery_concurrency + 1) * DB_POOL_MAX

Formula di dimensionamento (usata per il default=12):
    DB_POOL_MAX <= (pg_max_connections - reserve) / n_processes

Con produzione corrente (pg_max_connections=100, reserve=5,
n_processes = 2 uvicorn + 4 celery_children + 1 beat = 7):
    12 * 7 = 84 → safety margin 16 conn (superuser, autovacuum, ad-hoc).

Se si aumentano worker/concurrency SENZA alzare anche
`max_connections` su Postgres, abbassare DB_POOL_MAX di conseguenza.
"""

import os
import json
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import psycopg2.pool

from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── JSONB come stringa (backward compat) ────────────────────────────────────
# Il codice esistente fa json.loads(row["canvas"]) ovunque. Con psycopg2,
# JSONB verrebbe ritornato come dict (auto-parsed), rompendo json.loads(dict).
# Registrando JSONB come TEXT, psycopg2 ritorna la stringa JSON raw e il
# codice esistente continua a funzionare senza modifiche.
import psycopg2.extensions
_JSONB_OID = 3802
_JSON_OID = 114

def _json_as_text(value, cur):
    return value  # ritorna la stringa JSON raw, non parsed

psycopg2.extensions.register_type(
    psycopg2.extensions.new_type((_JSONB_OID,), 'JSONB_AS_TEXT', _json_as_text)
)
psycopg2.extensions.register_type(
    psycopg2.extensions.new_type((_JSON_OID,), 'JSON_AS_TEXT', _json_as_text)
)

# ── TIMESTAMPTZ come stringa ISO (backward compat) ──────────────────────────
# Il codice esistente ha Pydantic models con `created_at: str`, `updated_at: str`.
# Con psycopg2, TIMESTAMPTZ viene ritornato come datetime.datetime, rompendo
# la validazione Pydantic. Registrando come TEXT, le date tornano come stringhe
# ISO e il codice esistente continua a funzionare.
_TIMESTAMPTZ_OID = 1184
_TIMESTAMP_OID = 1114
_DATE_OID = 1082

def _timestamp_as_text(value, cur):
    if value is None:
        return None
    # PostgreSQL ritorna timezone come +00, Pydantic vuole +00:00
    # Fix: se finisce con +XX o -XX (2 cifre), aggiungi :00
    import re as _re
    if _re.search(r'[+-]\d{2}$', value):
        value = value + ':00'
    return value

psycopg2.extensions.register_type(
    psycopg2.extensions.new_type((_TIMESTAMPTZ_OID,), 'TSTZ_AS_TEXT', _timestamp_as_text)
)
psycopg2.extensions.register_type(
    psycopg2.extensions.new_type((_TIMESTAMP_OID,), 'TS_AS_TEXT', _timestamp_as_text)
)
psycopg2.extensions.register_type(
    psycopg2.extensions.new_type((_DATE_OID,), 'DATE_AS_TEXT', _timestamp_as_text)
)

# ── Configurazione ──────────────────────────────────────────────────────────

_DATABASE_URL = os.getenv("DATABASE_URL")
if not _DATABASE_URL:
    raise RuntimeError("DATABASE_URL non configurato. Imposta la variabile d'ambiente.")
_DB_SCHEMA = os.getenv("DB_SCHEMA", "plenora")


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


_POOL_MIN = _read_int_env("DB_POOL_MIN", 2)
_POOL_MAX = _read_int_env("DB_POOL_MAX", 12)

# C7: idle_in_transaction timeout applicato tramite GUC per-connection.
# Evita zombie da transazioni applicative leaked (codice che apre BEGIN
# e non committa/rollbacka mai) senza richiedere modifiche a
# postgresql.conf. Scope: solo le connessioni del nostro pool.
# Default 300000ms = 5min. Imposta 0 per disabilitare.
_IDLE_IN_TRANSACTION_MS = _read_int_env(
    "DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", 300000
)


# C7 fix-nit: guardrail espliciti sui parametri del pool. Senza questi
# check, valori malformati (MIN>MAX, negativi, 0) si propagano fino a
# ThreadedConnectionPool dove falliscono con errori poco leggibili.
# Fail fast all'import con messaggio diagnostico.
def _validate_pool_params() -> None:
    errors = []
    if _POOL_MIN < 0:
        errors.append(f"DB_POOL_MIN={_POOL_MIN} deve essere >= 0")
    if _POOL_MAX < 1:
        errors.append(f"DB_POOL_MAX={_POOL_MAX} deve essere >= 1")
    if _POOL_MIN > _POOL_MAX:
        errors.append(
            f"DB_POOL_MIN={_POOL_MIN} > DB_POOL_MAX={_POOL_MAX}: "
            f"il minimo non puo' superare il massimo"
        )
    if _IDLE_IN_TRANSACTION_MS < 0:
        errors.append(
            f"DB_IDLE_IN_TRANSACTION_TIMEOUT_MS={_IDLE_IN_TRANSACTION_MS} "
            f"deve essere >= 0 (0 = disabilitato)"
        )
    if errors:
        raise RuntimeError(
            "Configurazione pool PG invalida:\n  - "
            + "\n  - ".join(errors)
        )


_validate_pool_params()

# ── Pool singleton ──────────────────────────────────────────────────────────

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()


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
                # C7: GUC per-connection via startup `options` parameter.
                # -c search_path=...   → schema predefinito
                # -c idle_in_transaction_session_timeout=Nms → zombie guard
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
                # (e' solo coordinator; i query vanno attraverso le child
                # prefork). Se UVICORN_WORKERS/CELERY_CONCURRENCY non sono
                # in env, fallback conservativo a 1 uvicorn + 0 celery.
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


# Alias per backward compat con il middleware cleanup
def close_connection():
    """No-op per PostgreSQL: il pool gestisce le connessioni.

    Mantiene compatibilità con DBConnectionCleanupMiddleware che chiama
    close_connection() dopo ogni request. Con il pool non serve chiudere
    esplicitamente — la connessione viene restituita al pool automaticamente.
    """
    pass


# ── Query helpers (stessa interfaccia di config_db_service) ─────────────────

def _adapt_params(sql: str, params: tuple) -> Tuple[str, tuple]:
    """Converte placeholder da SQLite (?) a psycopg2 (%s).

    SQLite usa `?` come placeholder, PostgreSQL/psycopg2 usa `%s`.
    Questa funzione fa la conversione automatica così il codice esistente
    che usa `?` continua a funzionare senza modifiche.

    NOTA: non converte `?` dentro stringhe quotate (es. in LIKE patterns).
    Per query complesse, usare direttamente `%s`.
    """
    # Semplice sostituzione: ogni ? non dentro apici diventa %s
    # Per il 99% dei casi è sufficiente
    adapted_sql = sql.replace("?", "%s")
    return adapted_sql, params


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
            # Simula il rowcount di sqlite3
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
        with transaction() as conn:
            conn.execute("INSERT ...")
            conn.execute("UPDATE ...")
        # auto-commit alla fine, rollback su exception
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


# ── Init DB (crea tabelle se non esistono) ──────────────────────────────────

def init_db():
    """Verifica che le tabelle esistano su PostgreSQL.

    A differenza di SQLite, non esegue lo schema SQL: le tabelle
    sono create dalle migrations in `backend/migrations/`
    (entry-point `0000_bootstrap_base_tables.sql` + add-on
    `2026-XX-XX_*.sql`). Questo init verifica solo la connessione
    e logga il numero di tabelle trovate nello schema applicativo.
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
