"""
Gestione concorrenza per lo Scheduler URBY-ETL — su SQLite, no Redis.

Lock e coda memorizzati nella tabella `scheduler_locks` in urbyetorbit.db.

Chiavi:
    - scheduler:lock:{schedule_id}   → 'running' con expiry timestamp
    - scheduler:queue:{schedule_id}  → contatore coda (int)
"""

from datetime import datetime, timedelta, timezone
from typing import Literal
import app.db.db_service as db
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# TTL del lock scheduler. Se il worker Celery muore HARD (SIGKILL/OOM) durante
# l'esecuzione di una pipeline, il finally di ExecutionManager.execute() non
# viene eseguito e il lock resta acquisito fino allo scadere del TTL.
#
# Prima era 4 ore: troppo lungo, lasciava deadlock di 4h dopo crash.
# Ora: hard limit Celery (1h) + 50% margine = ~1.5h. Copre il caso peggiore
# di pipeline lente con buffer per cleanup, e limita il blast radius del crash.
LOCK_TTL_SECONDS = int(settings.CELERY_SCHEDULE_HARD_LIMIT * 1.5)


class ConcurrencyManager:
    """
    Gestisce lock e coda per le esecuzioni schedule su SQLite.
    """

    def __init__(self):
        self._ensure_table()
        self._available = True

    @property
    def available(self) -> bool:
        return self._available

    def _ensure_table(self):
        """Crea la tabella se non esiste."""
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
                if datetime.now(timezone.utc) > exp:
                    db.execute("DELETE FROM scheduler_locks WHERE key = ?", (key,))
                    return None
            except Exception:
                pass
        return row["value"]

    def _set_nx(self, key: str, value: str, ttl_seconds: int = None) -> bool:
        """SET NX — imposta solo se non esiste. Returns True se acquisito."""
        # Prima pulisci eventuali lock scaduti
        self._get(key)  # side effect: cancella se scaduto
        expires_at = None
        if ttl_seconds:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        try:
            db.execute(
                "INSERT INTO scheduler_locks (key, value, expires_at) VALUES (?,?,?)",
                (key, value, expires_at)
            )
            return True
        except Exception:
            return False  # chiave già esistente

    def _incr(self, key: str) -> int:
        """Incrementa contatore atomicamente, crea se non esiste.

        Implementazione atomic-safe basata su `UPDATE ... RETURNING`
        (SQLite >= 3.35). Bypassa `db.execute` perché quel helper committa
        prima che il cursor del RETURNING possa essere consumato (errore
        "cannot commit transaction - SQL statements in progress").

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

    # =========================================================================

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
