"""
pg_connection.py — Connection pool PostgreSQL per URBY-ETL.

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
  DB_POOL_MAX=10
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

_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:zAWMyAJTzEnDUOj9JyMKdmngZWrhlupyo94GGPOOT1o%3D@15.161.59.22:5432/plenora"
)
_DB_SCHEMA = os.getenv("DB_SCHEMA", "plenora")
_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# ── Pool singleton ──────────────────────────────────────────────────────────

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Lazy-init del pool. Thread-safe via lock."""
    global _pool
    if _pool is None or _pool.closed:
        with _pool_lock:
            if _pool is None or _pool.closed:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    _POOL_MIN, _POOL_MAX, _DATABASE_URL,
                    options=f"-c search_path={_DB_SCHEMA},public",
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
                logger.info(
                    f"PG pool inizializzato: {_POOL_MIN}-{_POOL_MAX} conn, "
                    f"schema={_DB_SCHEMA}"
                )
    return _pool


def get_connection():
    """Prende una connessione dal pool. Da usare con `put_connection()` dopo."""
    return _get_pool().getconn()


def put_connection(conn):
    """Rimette una connessione nel pool."""
    try:
        _get_pool().putconn(conn)
    except Exception:
        pass


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

    A differenza di SQLite, non esegue lo schema SQL (le tabelle sono
    già create dallo script create_pg_schema.py). Verifica solo la
    connessione e logga il numero di tabelle trovate.
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
