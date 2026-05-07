"""
Gestione concorrenza per lo Scheduler PLENORA-ETL — su PostgreSQL, no Redis.

Lock e coda memorizzati nella tabella `scheduler_locks` nel database PostgreSQL.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

import psycopg2
import psycopg2.errors

import app.db.db_service as db
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# TTL del lock scheduler (C3 fix 4).
#
# Strategia heartbeat: il lock ha TTL breve (10 min default), ma
# ExecutionManager.execute() avvia un thread daemon che refresha il
# TTL ogni HEARTBEAT_INTERVAL secondi finche' la pipeline e' attiva.
# Se il worker muore HARD (SIGKILL/OOM) il thread daemon muore con lui
# -> niente piu' refresh -> lock scade entro LOCK_TTL_SECONDS (10 min)
# invece di restare appeso fino al hard limit Celery (65 min).
#
# Il valore e' configurabile via `settings.SCHEDULE_LOCK_TTL_SECONDS`
# per deployment con pipeline eccezionalmente lunghe o heartbeat
# meno frequente.
LOCK_TTL_SECONDS = settings.SCHEDULE_LOCK_TTL_SECONDS

# Intervallo del thread di heartbeat dentro ExecutionManager.execute().
# Deve essere << LOCK_TTL_SECONDS / 2 per lasciare almeno un refresh
# di margine se il primo fallisce per transient. Default: 180s (3 min)
# = 3 refresh nel TTL 10 min, margine 3x.
LOCK_HEARTBEAT_INTERVAL_SECONDS = settings.SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS


class ConcurrencyManager:
    """
    Gestisce lock e coda per le esecuzioni schedule su PostgreSQL.
    """

    def __init__(self):
        # NOTE: inizializzo _available=True PRIMA di _ensure_table()
        # e non DOPO. Se _ensure_table() fallisce e setta _available=False,
        # un eventuale `self._available = True` posizionato dopo lo
        # sovrascriverebbe, mascherando la degraded mode.
        self._available = True
        self._ensure_table()

    @property
    def available(self) -> bool:
        return self._available

    def _ensure_table(self):
        """Crea la tabella se non esiste. Se fallisce, setta available=False
        cosi' il manager segnala degraded mode ai caller (che dovrebbero
        fallback a esecuzione senza lock)."""
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS scheduler_locks (
                    key         TEXT PRIMARY KEY,
                    value       TEXT NOT NULL,
                    expires_at  TEXT
                )
            """)
        except Exception as e:
            logger.warning(f"ConcurrencyManager: impossibile creare tabella ({e})")
            self._available = False

    def _get(self, key: str) -> str | None:
        """Legge un valore dal lock store, rispettando TTL."""
        row = db.fetchone(
            "SELECT value, expires_at FROM scheduler_locks WHERE key = ?", (key,)
        )
        if not row:
            return None
        # Verifica scadenza
        if row["expires_at"]:
            try:
                exp = datetime.fromisoformat(row["expires_at"])
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp:
                    db.execute("DELETE FROM scheduler_locks WHERE key = ?", (key,))
                    return None
            except Exception:
                pass
        return row["value"]

    def _set_nx(self, key: str, value: str, ttl_seconds: int = None) -> bool:
        """SET NX — imposta solo se non esiste. Returns True se acquisito.

        Il catch e' narrow a (UniqueViolation, IntegrityError) per preservare
        la semantica control-flow "chiave gia' esistente" SOLO sui casi
        legittimi di conflitto UNIQUE. Altri errori DB (pool exhausted,
        connection down, bug logici) propagano al caller `try_acquire`, che
        a sua volta propaga al boundary `ExecutionManager.execute` — cosi'
        il vero problema emerge come status failure esplicito invece di
        "queued" misleading (pre-fix: qualsiasi errore -> False silent ->
        coda corrotta su DB down).
        """
        # Prima pulisci eventuali lock scaduti
        self._get(key)  # side effect: cancella se scaduto
        expires_at = None
        if ttl_seconds:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        # intent: exception-as-control-flow tipizzata (UNIQUE -> False)
        # outcome: fail-closed sugli errori non-UNIQUE (propagano)
        try:
            db.execute(
                "INSERT INTO scheduler_locks (key, value, expires_at) VALUES (?,?,?)",
                (key, value, expires_at)
            )
            return True
        except (psycopg2.errors.UniqueViolation, psycopg2.IntegrityError):
            return False  # semantica UNIQUE constraint — lock gia' preso

    def _incr(self, key: str) -> int:
        """Incrementa contatore atomicamente, crea se non esiste.

        Implementazione atomic-safe basata su `UPDATE ... RETURNING`
        (PostgreSQL). Bypassa `db.execute` perché quel helper committa
        prima che il cursor del RETURNING possa essere consumato.

        Algoritmo:
          1. INSERT OR IGNORE crea la riga a '0' se non esiste (idempotente).
          2. UPDATE ... RETURNING applica +1 e ritorna il NUOVO valore in
             una sola statement → race-free anche fra N thread paralleli.

        Prima: SELECT + INSERT OR REPLACE non atomici → due thread che
        leggevano value=2 sovrascrivevano entrambi a value=3 invece di 4.
        """
        # PostgreSQL: usa pool connection con cursor esplicito
        from app.db.pg_connection import get_connection, put_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO scheduler_locks (key, value, expires_at) "
                    "VALUES (%s, '0', NULL) ON CONFLICT DO NOTHING",
                    (key,)
                )
                cur.execute(
                    "UPDATE scheduler_locks SET value = CAST(value AS INTEGER) + 1 "
                    "WHERE key = %s RETURNING value",
                    (key,)
                )
                row = cur.fetchone()
            conn.commit()
            return int(row["value"]) if row else 0
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            put_connection(conn)

    def _decr(self, key: str) -> int:
        """Decrementa contatore atomicamente. Rimuove se arriva a 0.

        Stesso pattern atomic di _incr: UPDATE ... RETURNING.
        """
        from app.db.pg_connection import get_connection, put_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE scheduler_locks SET value = CAST(value AS INTEGER) - 1 "
                    "WHERE key = %s AND CAST(value AS INTEGER) > 0 RETURNING value",
                    (key,)
                )
                row = cur.fetchone()
            if row is None:
                conn.commit()
                return 0
            new_val = int(row["value"])
            if new_val == 0:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM scheduler_locks "
                        "WHERE key = %s AND CAST(value AS INTEGER) = 0",
                        (key,)
                    )
            conn.commit()
            return new_val
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            put_connection(conn)

    def _delete(self, key: str):
        db.execute("DELETE FROM scheduler_locks WHERE key = ?", (key,))


    def try_acquire(self, schedule_id: str, max_queue_size: int = 3) -> Literal["acquired", "queued", "skipped"]:
        """Acquisisce il lock per lo schedule, o lo accoda, o lo skippa.

        Pattern atomic-safe contro race condition:
          1. _set_nx (UNIQUE constraint → atomic)
          2. Se lock occupato: _incr PRIMA di check (atomic ora con D-FIX-1)
          3. Se il nuovo valore SUPERA max_queue_size: _decr e skip
        Prima: read + check + incr non atomici → la coda poteva superare il
        limite (es. 3 thread concorrenti che leggono current=2 e tutti
        decidono "ok" → coda finisce a 5).
        """
        lock_key  = f"scheduler:lock:{schedule_id}"
        queue_key = f"scheduler:queue:{schedule_id}"

        if self._set_nx(lock_key, "running", LOCK_TTL_SECONDS):
            return "acquired"

        # Lock occupato. Incrementiamo PRIMA, poi verifichiamo: se siamo oltre
        # il limite, decrementiamo e skip. Questo elimina la race read+check+incr.
        new_size = self._incr(queue_key)
        if new_size > max_queue_size:
            self._decr(queue_key)
            logger.warning(
                f"Coda piena, skip: {schedule_id} ({new_size - 1}/{max_queue_size})"
            )
            return "skipped"
        return "queued"

    def refresh_lock(self, schedule_id: str, ttl_seconds: int = None) -> bool:
        """Estende il TTL del lock (C3 fix 4, heartbeat da ExecutionManager).

        Usato dal thread daemon di heartbeat durante l'esecuzione di una
        pipeline lunga: il TTL base e' corto (10 min) per permettere
        recovery veloce su SIGKILL, e il worker rinfresca periodicamente
        finche' sta lavorando.

        Race safety: se il lock e' stato rilasciato tra due chiamate,
        ritorna False (il caller sa che non deve piu' rinfrescare).

        Args:
            schedule_id: lo schedule il cui lock va rinfrescato.
            ttl_seconds: override opzionale del TTL (default LOCK_TTL_SECONDS).

        Returns:
            True se il lock era vivo ed e' stato rinfrescato;
            False se il lock non esiste piu' (race o gia' rilasciato).
        """
        lock_key = f"scheduler:lock:{schedule_id}"
        # _get fa anche auto-cleanup se scaduto: se ritorna None, il
        # lock e' morto e non dobbiamo ri-crearlo da zero.
        if self._get(lock_key) is None:
            return False

        ttl = ttl_seconds if ttl_seconds is not None else LOCK_TTL_SECONDS
        new_expires = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl)
        ).isoformat()

        try:
            cur = db.execute(
                "UPDATE scheduler_locks SET expires_at = %s WHERE key = %s",
                (new_expires, lock_key),
            )
            rowcount = getattr(cur, "rowcount", 0) or 0
            return rowcount > 0
        except Exception as e:
            logger.warning(f"refresh_lock {schedule_id}: {e}")
            return False

    def release(self, schedule_id: str) -> bool:
        lock_key  = f"scheduler:lock:{schedule_id}"
        queue_key = f"scheduler:queue:{schedule_id}"

        self._delete(lock_key)
        remaining = self._decr(queue_key)
        logger.info(f"Lock rilasciato: {schedule_id} (coda rimanente: {remaining})")
        return True

    def get_status(self, schedule_id: str) -> dict:
        lock_key  = f"scheduler:lock:{schedule_id}"
        queue_key = f"scheduler:queue:{schedule_id}"
        return {
            "locked": self._get(lock_key) is not None,
            "queue_size": int(self._get(queue_key) or 0),
        }

    def is_running(self, schedule_id: str) -> bool:
        return self._get(f"scheduler:lock:{schedule_id}") is not None

    def force_release(self, schedule_id: str) -> None:
        self._delete(f"scheduler:lock:{schedule_id}")
        self._delete(f"scheduler:queue:{schedule_id}")
        logger.warning(f"Lock forzato rilasciato: {schedule_id}")

    def cleanup_all(self) -> int:
        cur = db.execute("DELETE FROM scheduler_locks WHERE key LIKE 'scheduler:%'")
        count = cur.rowcount
        if count:
            logger.info(f"Cleanup concorrenza: {count} chiavi rimosse")
        return count
