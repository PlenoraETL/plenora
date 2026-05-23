"""
Gestione concorrenza per lo Scheduler PLENORA-ETL — su PostgreSQL, no Redis.

Lock e coda memorizzati nella tabella `scheduler_locks` nel database PostgreSQL.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal
from typing_extensions import TypedDict

import psycopg2
import psycopg2.errors

from app.core.scheduler import db
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Strategia heartbeat: il lock ha TTL breve (10 min default), ma
# ExecutionManager.execute avvia un thread daemon che refresha il
# TTL ogni HEARTBEAT_INTERVAL secondi finche' la pipeline e' attiva
# Se il worker muore HARD (SIGKILL/OOM) il thread daemon muore con lui
# > niente piu' refresh -> lock scade entro LOCK_TTL_SECONDS (10 min
# invece di restare appeso fino al hard limit Celery (65 min
# Il valore e' configurabile via `settings.SCHEDULE_LOCK_TTL_SECONDS`
# per deployment con pipeline eccezionalmente lunghe o heartbeat
# meno frequente
LOCK_TTL_SECONDS = settings.SCHEDULE_LOCK_TTL_SECONDS

# Intervallo del thread di heartbeat dentro ExecutionManager.execute
# Deve essere << LOCK_TTL_SECONDS / 2 per lasciare almeno un refresh
# di margine se il primo fallisce per transient. Default: 180s (3 min
# = 3 refresh nel TTL 10 min, margine 3x
LOCK_HEARTBEAT_INTERVAL_SECONDS = settings.SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS


class ConcurrencyStatusPayload(TypedDict):
    locked: bool
    queue_size: int


class ConcurrencyManager:
    """
    Gestisce lock e coda per le esecuzioni schedule su PostgreSQL.
    """

    def __init__(self):
        # NOTE: inizializzo _available=True PRIMA di _ensure_table
        # e non DOPO. Se _ensure_table fallisce e setta _available=False
        # un eventuale `self._available = True` posizionato dopo lo
        # sovrascriverebbe, mascherando la degraded mode
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
            "SELECT value, expires_at FROM scheduler_locks WHERE key = %s", (key,)
        )
        if not row:
            return None
        if row["expires_at"]:
            try:
                exp = datetime.fromisoformat(row["expires_at"])
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp:
                    db.execute("DELETE FROM scheduler_locks WHERE key = %s", (key,))
                    return None
            except Exception:
                pass
        return row["value"]

    def _set_nx(self, key: str, value: str, ttl_seconds: int = None) -> bool:
        """SET NX — imposta solo se non esiste o se scaduto. Returns True se acquisito.

        Pre-fix: `self._get(key)` cancellava il lock scaduto come side-effect,
        seguito da un INSERT separato. Tra DELETE e INSERT esisteva una
        finestra di race in cui un altro processo poteva inserire il proprio
        lock e farci ottenere UniqueViolation -> False, anche quando la
        nostra DELETE era legittima e nessuno aveva un lock valido.

        Post-fix: atomic test-and-steal-if-expired via PostgreSQL
        `INSERT ... ON CONFLICT (key) DO UPDATE` con clausola WHERE che
        permette l'update SOLO se `expires_at IS NULL` o gia' scaduto.
        Combinato con `RETURNING (xmax = 0) AS is_insert` distinguiamo:
          - row nuova (insert) -> acquisito True
          - row aggiornata (steal di scaduto) -> acquisito True
          - nessuna row tornata (WHERE non matchato, lock attivo) -> False

        Riferimento: https://stackoverflow.com/a/8702291 — la combinazione
        ON CONFLICT DO UPDATE ... WHERE e' atomica nella stessa statement.

        Il catch resta narrow su (UniqueViolation, IntegrityError) per
        sicurezza, ma con ON CONFLICT non dovrebbe piu' triggerare in
        operativita' normale.
        """
        expires_at = None
        if ttl_seconds:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        try:
            row = db.fetchone(
                "INSERT INTO scheduler_locks (key, value, expires_at) "
                "VALUES (%s,%s,%s) "
                "ON CONFLICT (key) DO UPDATE "
                "  SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at "
                "  WHERE scheduler_locks.expires_at IS NOT NULL "
                "    AND scheduler_locks.expires_at <= now() "
                "RETURNING value",
                (key, value, expires_at),
            )
            return row is not None
        except (psycopg2.errors.UniqueViolation, psycopg2.IntegrityError):
            return False  # difensivo: ON CONFLICT dovrebbe evitarlo, ma fallback

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
        with db.transaction() as conn:
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
            return int(row["value"]) if row else 0

    def _decr(self, key: str) -> int:
        """Decrementa contatore atomicamente. Rimuove se arriva a 0.

        Stesso pattern atomic di _incr: UPDATE ... RETURNING.
        """
        with db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE scheduler_locks SET value = CAST(value AS INTEGER) - 1 "
                    "WHERE key = %s AND CAST(value AS INTEGER) > 0 RETURNING value",
                    (key,)
                )
                row = cur.fetchone()
            if row is None:
                return 0
            new_val = int(row["value"])
            if new_val == 0:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM scheduler_locks "
                        "WHERE key = %s AND CAST(value AS INTEGER) = 0",
                        (key,)
                    )
            return new_val

    def _delete(self, key: str):
        db.execute("DELETE FROM scheduler_locks WHERE key = %s", (key,))


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
        # il limite, decrementiamo e skip. Questo elimina la race read+check+incr
        new_size = self._incr(queue_key)
        if new_size > max_queue_size:
            self._decr(queue_key)
            logger.warning(
                f"Coda piena, skip: {schedule_id} ({new_size - 1}/{max_queue_size})"
            )
            return "skipped"
        return "queued"

    def refresh_lock(self, schedule_id: str, ttl_seconds: int = None) -> bool:
        """Estende il TTL del lock durante heartbeat da ExecutionManager.

        Usato dal thread daemon di heartbeat durante l'esecuzione di una
        pipeline lunga: il TTL base e' corto (10 min) per permettere
        recovery veloce su SIGKILL, e il worker rinfresca periodicamente
        finche' sta lavorando.

        Race safety:
        Pre-fix: `_get(lock_key)` faceva un check con side-effect (DELETE
        se scaduto), poi un UPDATE separato non-condizionale. Finestra
        TOCTOU tra il check e l'UPDATE:
          1. heartbeat A: `_get` vede lock ancora vivo (1ms before expiry)
          2. lock scade naturalmente
          3. worker B: `try_acquire` -> `_set_nx` con WHERE expires_at <= now()
             -> aquisisce il lock (atomico, ok)
          4. heartbeat A: UPDATE non-condizionale sovrascrive il lock di B
             con il proprio TTL → due worker pensano di possedere il lock,
             entrambi scrivono sul target CARICA.
        Post-fix: UPDATE atomico con `WHERE expires_at > now()`. Se il
        lock e' scaduto o e' stato preso da un altro worker (expires_at
        diverso ma "vivo" per quel worker), rowcount = 0 e ritorna False.
        Il caller sa che non possiede piu' il lock e abortisce la run.

        Args:
            schedule_id: lo schedule il cui lock va rinfrescato.
            ttl_seconds: override opzionale del TTL (default LOCK_TTL_SECONDS).

        Returns:
            True se il lock era vivo (`expires_at > now()`) ed e' stato
            rinfrescato atomicamente; False se il lock non esiste piu'
            o e' stato preso da un altro worker.
        """
        lock_key = f"scheduler:lock:{schedule_id}"
        ttl = ttl_seconds if ttl_seconds is not None else LOCK_TTL_SECONDS
        new_expires = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl)
        ).isoformat()

        try:
            # UPDATE atomico: rinfresca SOLO se il lock e' ancora vivo.
            # Il check `expires_at > now()` e' nella stessa statement
            # del SET, quindi non c'e' finestra di race fra check e set.
            cur = db.execute(
                "UPDATE scheduler_locks SET expires_at = %s "
                "WHERE key = %s AND expires_at > now()",
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

    def get_status(self, schedule_id: str) -> ConcurrencyStatusPayload:
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
