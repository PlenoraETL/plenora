"""PostgreSQL connection pool and DAL query helper entry point."""

import threading
from dataclasses import dataclass
from typing import Any, Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

from app.environment import get_env
from app.utils.logger import get_logger
from app.shared.contracts.payloads import JsonObject, JsonRows

logger = get_logger(__name__)

_DATABASE_URL: Optional[str] = None
_DB_SCHEMA = "plenora"


def _read_int_env(name: str, default: int) -> int:
    """Legge una env var come intero. Se la conversione fallisce
    (stringa non numerica, vuota), solleva RuntimeError con messaggio
    esplicito invece di propagare ValueError oscuro."""
    raw = get_env(name)
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

# Evita zombie da transazioni applicative leaked (codice che apre BEGIN
# e non committa/rollbacka mai) senza richiedere modifiche a
# postgresql.conf. Scope: solo le connessioni del nostro pool
# Default 300000ms = 5min. Imposta 0 per disabilitare
_IDLE_IN_TRANSACTION_MS = 300000
_POOL_CONFIG_LOADED = False


# Guardrail espliciti sui parametri del pool: valori malformati
# falliscono all'import con messaggio diagnostico.
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

    database_url = get_env("DATABASE_URL")
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
    _DB_SCHEMA = get_env("DB_SCHEMA", "plenora") or "plenora"
    _POOL_MIN = pool_min
    _POOL_MAX = pool_max
    _IDLE_IN_TRANSACTION_MS = idle_in_transaction_ms
    _POOL_CONFIG_LOADED = True

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


def discard_pool_after_fork() -> None:
    """Scarta il riferimento al pool ereditato dal parent post-fork.

    I worker prefork di Celery ereditano i socket TLS del pool aperto nel
    master: questi non sono fork-safe (la state crittografica e' condivisa
    col parent) e al primo uso producono ``SSL SYSCALL error: EOF
    detected`` o ``decryption failed or bad record mac``. Chiamare
    ``closeall()`` qui peggiorerebbe il problema (SSL teardown sui socket
    condivisi col parent), quindi azzeriamo solo il riferimento: il
    prossimo ``_get_pool()`` aprira' connessioni fresh nel PID del child.

    Idempotente. Da invocare nel signal handler
    ``celery.signals.worker_process_init``.
    """
    global _pool
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
        e mappa a fail-OPEN `(True, "introspection skipped: ...")`.
    """
    pool = _get_pool()
    in_use = len(getattr(pool, "_used", {}) or {})
    free = len(getattr(pool, "_pool", []) or [])
    return PoolHeadroom(max=_POOL_MAX, in_use=in_use, free=free)


def _adapt_params(sql: str, params: tuple) -> tuple[str, tuple]:
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


class _ExecuteResult:
    """Small legacy-compatible execute result after cursor close."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount

    def fetchone(self) -> None:
        return None


class PgSession:
    """Explicit PostgreSQL session passed through repository call chains.

    The session wraps one checked-out connection.  It exposes typed query
    helpers and transaction controls without relying on `threading.local`.
    `UnitOfWork` owns checkout/release and calls commit/rollback on exit.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("PgSession is closed")

    def close(self) -> None:
        self._closed = True

    def cursor(self) -> Any:
        """Return a raw psycopg cursor context manager for advanced callers."""
        self._ensure_open()
        return self._conn.cursor()

    def commit(self) -> None:
        self._ensure_open()
        self._conn.commit()

    def rollback(self) -> None:
        self._ensure_open()
        self._conn.rollback()

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[JsonObject]:
        """Execute a query and return the first row as a dict, or None."""
        self._ensure_open()
        sql, params = _adapt_params(sql, params)
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> JsonRows:
        """Execute a query and return all rows as a list of dicts."""
        self._ensure_open()
        sql, params = _adapt_params(sql, params)
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def execute(self, sql: str, params: tuple = ()) -> _ExecuteResult:
        """Execute a write query and return a legacy rowcount wrapper."""
        self._ensure_open()
        sql, params = _adapt_params(sql, params)
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return _ExecuteResult(cur.rowcount)

    def executemany(self, sql: str, params_list: list[tuple]) -> None:
        """Execute a query with many parameter sets."""
        self._ensure_open()
        sql, _ = _adapt_params(sql, ())
        with self._conn.cursor() as cur:
            cur.executemany(sql, params_list)


class UnitOfWork:
    """Context manager for an explicit PostgreSQL unit of work."""

    def __init__(self) -> None:
        self._conn: Any | None = None
        self._session: PgSession | None = None

    def __enter__(self) -> PgSession:
        if self._session is not None:
            raise RuntimeError("UnitOfWork is already active")
        conn = get_connection()
        self._conn = conn
        self._session = PgSession(conn)
        return self._session

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        conn = self._conn
        session = self._session
        try:
            if conn is None:
                return
            if exc_type is None:
                try:
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            else:
                conn.rollback()
        finally:
            if session is not None:
                session.close()
            if conn is not None:
                put_connection(conn)
            self._conn = None
            self._session = None


def unit_of_work() -> UnitOfWork:
    """Create an explicit PostgreSQL UnitOfWork.

    This is the Go-readiness path for repositories: pass the yielded
    `PgSession` explicitly instead of depending on module/thread state.
    """
    return UnitOfWork()


def fetchone(sql: str, params: tuple = ()) -> Optional[JsonObject]:
    """Esegue query e ritorna la prima riga come dict, o None."""
    with unit_of_work() as session:
        return session.fetchone(sql, params)


def fetchall(sql: str, params: tuple = ()) -> JsonRows:
    """Esegue query e ritorna tutte le righe come lista di dict."""
    with unit_of_work() as session:
        return session.fetchall(sql, params)


def execute(sql: str, params: tuple = ()):
    """Esegue una query di scrittura (INSERT/UPDATE/DELETE). Ritorna il cursor."""
    with unit_of_work() as session:
        return session.execute(sql, params)


def executemany(sql: str, params_list: list[tuple]) -> None:
    """Esegue una query con molti set di parametri."""
    with unit_of_work() as session:
        session.executemany(sql, params_list)


def transaction():
    """Compatibility alias for an explicit PostgreSQL UnitOfWork.

    Historical callers used ``transaction()`` as a raw-connection context
    manager.  It now yields ``PgSession`` and shares the same explicit,
    process-state-free lifecycle as ``unit_of_work()``. New code should
    depend on ``unit_of_work()`` directly.
    """
    return unit_of_work()


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
