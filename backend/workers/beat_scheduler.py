"""
PlenoraBeatScheduler — subclass di celery.beat.Scheduler con:
- schedule caricati dal DB Postgres (tabella `schedules`)
- leader election via Redis SETNX (scheduler:beat:leader) per
  evitare doppio enqueue quando due processi beat partono in parallelo
- heartbeat + cleanup jobs integrati nel tick

Funzionamento:
- Ogni tick (default 60s), prima verifica leadership via Redis SETNX.
  Se non-leader → standby (skippa tutto, incluso super().tick()).
- Se leader: _sync_from_db legge la tabella `schedules`, popola
  self.data con ScheduleEntry, delega a super().tick() che emette
  i task Celery scaduti.

Avvio (CLI Celery):
    celery -A workers.celery_app beat -S workers.beat_scheduler.PlenoraBeatScheduler --loglevel=info
"""

import os
import socket
import time
from datetime import datetime, timezone
from typing import Optional
from celery.beat import Scheduler, ScheduleEntry
from celery.utils.log import get_logger

logger = get_logger(__name__)

# Intervallo minimo tra due check (secondi)
DEFAULT_MAX_INTERVAL = 60.0

# Leader election: chiave Redis, TTL del lock, ID processo corrente.
# TTL=120s + refresh a ogni tick (~60s) → margine 2x per sopravvivere
# a un tick lento o a un transient di rete senza perdere leadership.
_LEADER_LOCK_KEY = "scheduler:beat:leader"
_LEADER_LOCK_TTL_SECONDS = 120

# Telemetria leader transitions (C3 round 4): counter per-type + stato
# attuale pubblicato su Redis per osservabilita' cross-processo. Il beat
# gira in processo separato dalla FastAPI → no memoria condivisa per
# metriche prometheus in-process, uso Redis come backbone. Transition
# types: acquired / lost / released / standby / degraded_open.
_LEADER_METRICS_HASH = "scheduler:beat:leader:transitions"
_LEADER_METRICS_CURRENT_ID = "scheduler:beat:leader:current_leader_id"
_LEADER_METRICS_LAST_TRANSITION_AT = "scheduler:beat:leader:last_transition_at"


class PlenoraBeatScheduler(Scheduler):
    """
    Beat Scheduler custom che legge gli schedule da PostgreSQL.
    
    Ogni max_interval secondi:
    1. Carica tutti gli schedule attivi dal DB
    2. Per ognuno verifica se è scaduto (cron/interval)
    3. Lancia il task Celery se necessario
    4. Aggiorna last_run nel DB
    5. Scrive heartbeat nel DB
    """

    max_interval = DEFAULT_MAX_INTERVAL

    def __init__(self, *args, **kwargs):
        self._db_loaded = False
        # Leader election state. `_leader_id` identifica questa istanza
        # all'interno del cluster (pid@hostname). `_is_leader` tracciato
        # per loggare le transizioni. `_redis_client` lazy — inizializzato
        # al primo tick via _get_redis().
        self._leader_id = f"{os.getpid()}@{socket.gethostname()}"
        self._is_leader = False
        self._redis_client = None
        super().__init__(*args, **kwargs)
        logger.info(
            f"PlenoraBeatScheduler avviato (schedule da Postgres, "
            f"broker Redis, leader_id={self._leader_id})"
        )

    # ------------------------------------------------------------------
    # Leader election via Redis SETNX
    # ------------------------------------------------------------------

    def _get_redis(self):
        """Lazy init del client Redis. Ritorna None se non raggiungibile
        (trigger del degraded fallback single-instance)."""
        if self._redis_client is not None:
            return self._redis_client
        try:
            import redis
            from app.config import settings
            client = redis.Redis.from_url(settings.CELERY_BROKER_URL)
            # Ping per verificare che sia raggiungibile: se fallisce
            # trattiamo Redis come down invece di avere eccezioni
            # random al primo set().
            client.ping()
            self._redis_client = client
            return client
        except Exception as e:
            logger.error(f"[LEADER] Redis non raggiungibile: {e}", exc_info=True)
            return None

    def _record_leader_transition(self, transition_type: str) -> None:
        """Telemetria best-effort delle transizioni leader (C3 round 4).

        Scrive su Redis:
        - HINCRBY sulla hash counter per `transition_type`
        - SET del current_leader_id (solo su acquired/degraded_open)
        - SET dell'ISO timestamp dell'ultima transizione

        Best-effort: se Redis e' giu' o la scrittura fallisce, la
        transizione e' loggata (dal caller) ma la metrica e' persa.
        Mai rompe il flow di leader election per un errore metric.
        """
        client = self._redis_client
        if client is None:
            return
        try:
            client.hincrby(_LEADER_METRICS_HASH, transition_type, 1)
            client.set(
                _LEADER_METRICS_LAST_TRANSITION_AT,
                datetime.now(timezone.utc).isoformat(),
            )
            if transition_type in ("acquired", "degraded_open"):
                client.set(_LEADER_METRICS_CURRENT_ID, self._leader_id)
            elif transition_type in ("lost", "released"):
                # Clear current quando lasciamo la leadership
                client.delete(_LEADER_METRICS_CURRENT_ID)
        except Exception as e:
            logger.warning(
                f"[LEADER] Metric write failed for transition="
                f"{transition_type}: {e}"
            )

    def _try_become_leader(self) -> bool:
        """Tenta di acquisire o refreshare il lock leader.

        Logica:
        1. SET ... NX EX: acquisizione atomica se la chiave non esiste.
        2. Altrimenti, GET: se il valore e' il mio leader_id → sono
           ancora leader, EXPIRE per refreshare il TTL.
        3. Altrimenti → qualcun altro e' leader, standby.

        Degraded mode (Redis unavailable o errore durante acquire):
        - Default **fail-closed**: standby, `return False`. Evita che
          due beat diventino entrambi leader durante un flap Redis
          riaprendo il doppio enqueue (regressione di C3 fix 1).
        - Con `SCHEDULE_BEAT_LEADER_FAIL_OPEN=True`: comportamento
          legacy (assumi di essere leader single-instance). Utile per
          deployment mono-host dove nessun altro beat gira mai.

        Il flag e' letto a ogni chiamata dai settings (non cache), cosi'
        puo' essere cambiato runtime via env var per incident response
        senza restart del beat.

        Returns:
            True se questo processo e' (o e' diventato) leader.
        """
        from app.config import settings
        fail_open = bool(getattr(settings, "SCHEDULE_BEAT_LEADER_FAIL_OPEN", False))

        def _degraded_result() -> bool:
            if fail_open:
                was_leader = self._is_leader
                if not was_leader:
                    logger.warning(
                        "[LEADER] Redis unavailable — fallback single-instance "
                        "leader (SCHEDULE_BEAT_LEADER_FAIL_OPEN=True)"
                    )
                self._is_leader = True
                if not was_leader:
                    self._record_leader_transition("degraded_open")
                return True
            else:
                was_leader = self._is_leader
                if was_leader:
                    logger.warning(
                        "[LEADER] Redis unavailable — perso, passo in standby "
                        "(fail-closed default). Set SCHEDULE_BEAT_LEADER_FAIL_OPEN=True "
                        "per il behavior legacy single-instance."
                    )
                else:
                    logger.warning(
                        "[LEADER] Redis unavailable — beat in standby "
                        "(fail-closed default)"
                    )
                self._is_leader = False
                if was_leader:
                    self._record_leader_transition("standby")
                return False

        client = self._get_redis()
        if client is None:
            return _degraded_result()

        try:
            acquired = client.set(
                _LEADER_LOCK_KEY,
                self._leader_id,
                nx=True,
                ex=_LEADER_LOCK_TTL_SECONDS,
            )
            if acquired:
                was_leader = self._is_leader
                if not was_leader:
                    logger.info(f"[LEADER] Acquisito: {self._leader_id}")
                self._is_leader = True
                if not was_leader:
                    self._record_leader_transition("acquired")
                return True

            # Lock gia' preso — sono io o qualcun altro?
            current_raw = client.get(_LEADER_LOCK_KEY)
            current = current_raw.decode("utf-8") if current_raw else None

            if current == self._leader_id:
                # Sono ancora leader — refresh TTL
                client.expire(_LEADER_LOCK_KEY, _LEADER_LOCK_TTL_SECONDS)
                self._is_leader = True
                return True

            # Qualcun altro e' leader
            was_leader = self._is_leader
            if was_leader:
                logger.warning(
                    f"[LEADER] Perso: ora '{current}' e' leader "
                    f"(io={self._leader_id}). Passo in standby."
                )
            self._is_leader = False
            if was_leader:
                self._record_leader_transition("lost")
            return False

        except Exception as e:
            logger.error(
                f"[LEADER] Errore comunicazione Redis durante acquire: {e}",
                exc_info=True,
            )
            return _degraded_result()

    # ------------------------------------------------------------------
    # Override principale: called ogni tick da Celery Beat
    # ------------------------------------------------------------------

    _last_stuck_check: float = 0.0
    _last_eseguibili_cleanup: float = 0.0
    _last_logs_retention: float = 0.0
    _last_logs_archive: float = 0.0
    _last_audit_archive: float = 0.0
    _last_schedule_logs_retention: float = 0.0
    _last_imdf_bundles_cleanup: float = 0.0
    _last_temp_orphan_cleanup: float = 0.0
    _last_storage_reconcile: float = 0.0

    def tick(self, event_t=None, **kwargs):
        """Override tick:
        1. Leader election (SETNX Redis). Se non-leader → standby.
        2. Se leader: sync schedule dal DB + heartbeat + cleanup jobs
           + catchup dei missed runs (opt-in per schedule).
        3. Delega a super().tick() che emette i task scaduti.

        In standby il beat ritorna `max_interval` senza toccare il DB
        ne' la coda Celery: zero enqueue duplicati con un secondo
        processo beat in parallelo.
        """
        if not self._try_become_leader():
            return self.max_interval

        self._sync_from_db()
        self._emit_catchup_runs()
        self._write_heartbeat()
        self._check_stuck_tasks()
        self._cleanup_eseguibili_outputs()
        self._cleanup_imdf_bundles()
        self._archive_old_logs()
        self._cleanup_old_logs()
        self._cleanup_old_schedule_logs()
        self._archive_old_audit_actions()
        self._cleanup_orphan_temp_files()
        self._reconcile_storage()
        if event_t is not None:
            return super().tick(event_t=event_t, **kwargs)
        return super().tick(**kwargs)

    # ------------------------------------------------------------------
    # Catchup dei missed run (C3 fix 3)
    # ------------------------------------------------------------------

    # Cap anti-runaway: se uno schedule emette piu' di N catchup nei
    # `_CATCHUP_RUNAWAY_WINDOW_MIN` minuti precedenti, il beat skippa
    # ulteriori emit + logga WARNING. Protegge da loop dovuti a bug in
    # update di last_run o cron expression degenerate.
    _CATCHUP_RUNAWAY_MAX = 5
    _CATCHUP_RUNAWAY_WINDOW_MIN = 10

    # Dedupe window: se lo stesso scheduled_for e' stato gia' emesso nelle
    # ultime N ore, NON ri-emettiamo (evita spam duplicati finche' il primo
    # task e' in volo nella coda Celery). Dopo la finestra, un eventuale
    # task morto viene re-emesso al prossimo detect.
    _CATCHUP_DEDUPE_WINDOW_HOURS = 2

    def _emit_catchup_runs(self):
        """Emette al piu' un task di catchup per ciascuno schedule con
        `catchup_missed_runs=True` che ha un missed run entro la grace
        window configurata.

        Step:
        1. Query schedule enabled (parse config JSON per leggere flag).
        2. Per ciascuno: detect_missed_run() -> None se nulla da recuperare.
        3. Se c'e' missed:
           a. Check cap anti-runaway su schedule_catchup_history.
           b. Emit schedule_pipeline_task con triggered_by='catchup' +
              scheduled_for=<ISO del tick logico>.
           c. UPDATE schedules SET last_run=<expected_last> (prevent re-trigger).
           d. INSERT schedule_catchup_history (audit).

        Robustezza: errori DB non crashano il tick (swallowed + loggati).
        """
        try:
            import app.db.db_service as db
            from app.core.scheduler.schedule_service import detect_missed_run

            rows = db.fetchall(
                "SELECT id, cron_expression, last_run, config "
                "FROM schedules WHERE is_enabled = TRUE"
            )
        except Exception as e:
            logger.error(f"[CATCHUP] Errore fetch schedules: {e}", exc_info=True)
            return

        now = datetime.now(timezone.utc)

        for row in rows:
            try:
                config = self._parse_config_json(row.get("config"))
                sched_cfg = (config or {}).get("schedule") or {}
                if not sched_cfg.get("catchup_missed_runs", False):
                    continue

                grace_hours = int(sched_cfg.get("catchup_grace_hours", 24))
                last_run = self._parse_dt(row.get("last_run"))
                cron_expr = row.get("cron_expression", "")

                expected_last = detect_missed_run(
                    cron_expr, last_run, now, grace_hours=grace_hours,
                )
                if expected_last is None:
                    continue

                sid = row["id"]

                if self._catchup_runaway_exceeded(sid, now):
                    logger.warning(
                        f"[CATCHUP] Runaway cap raggiunto per schedule={sid} "
                        f"(> {self._CATCHUP_RUNAWAY_MAX} catchup in "
                        f"{self._CATCHUP_RUNAWAY_WINDOW_MIN}min) - skip"
                    )
                    continue

                # Dedupe: se lo stesso scheduled_for e' stato gia' emesso
                # nelle ultime N ore, il task precedente e' probabilmente
                # in volo o in coda — non ri-emettiamo un duplicato.
                if self._catchup_already_emitted_recently(sid, expected_last, now):
                    logger.info(
                        f"[CATCHUP] Skip re-emit per schedule={sid} "
                        f"scheduled_for={expected_last.isoformat()} "
                        f"(emit recente gia' presente in history)"
                    )
                    continue

                self._emit_single_catchup(sid, expected_last, now)

            except Exception as e:
                logger.error(
                    f"[CATCHUP] Errore processing schedule "
                    f"{row.get('id', '?')}: {e}",
                    exc_info=True,
                )

    @staticmethod
    def _parse_config_json(config_raw):
        """Parsa il JSON `config` di uno schedule. Ritorna dict o None."""
        if not config_raw:
            return None
        if isinstance(config_raw, dict):
            return config_raw
        try:
            import json
            return json.loads(config_raw)
        except Exception:
            return None

    def _catchup_already_emitted_recently(
        self, schedule_id: str, expected_last: datetime, now: datetime,
    ) -> bool:
        """True se esiste gia' una riga in schedule_catchup_history per
        lo stesso (schedule_id, scheduled_for) emessa nelle ultime
        _CATCHUP_DEDUPE_WINDOW_HOURS ore.

        Dedupe window necessario perche' non aggiorniamo piu' last_run
        al momento dell'emit (fix 2 del C3 follow-up): senza questo
        check, se un task catchup e' ancora in coda Celery quando
        arriva il tick successivo, detect_missed_run vedrebbe ancora
        il missed e ri-emetterebbe un duplicato.

        Degraded: in caso di errore DB restituisce False (preferiamo
        un eventuale duplicato al drop di un catchup legittimo).
        """
        try:
            import app.db.db_service as db
            from datetime import timedelta
            window_start = now - timedelta(hours=self._CATCHUP_DEDUPE_WINDOW_HOURS)
            rows = db.fetchall(
                "SELECT COUNT(*) AS count FROM schedule_catchup_history "
                "WHERE schedule_id = %s AND scheduled_for = %s "
                "AND emitted_at >= %s",
                (schedule_id, expected_last.isoformat(), window_start.isoformat()),
            )
            count = int(rows[0]["count"]) if rows else 0
            return count > 0
        except Exception as e:
            logger.warning(f"[CATCHUP] Errore dedupe lookup: {e}")
            return False

    def _catchup_runaway_exceeded(self, schedule_id: str, now: datetime) -> bool:
        """True se lo schedule ha >= cap catchup emessi nella finestra."""
        try:
            import app.db.db_service as db
            from datetime import timedelta
            window_start = now - timedelta(minutes=self._CATCHUP_RUNAWAY_WINDOW_MIN)
            rows = db.fetchall(
                "SELECT COUNT(*) AS count FROM schedule_catchup_history "
                "WHERE schedule_id = %s AND emitted_at >= %s",
                (schedule_id, window_start.isoformat()),
            )
            count = int(rows[0]["count"]) if rows else 0
            return count >= self._CATCHUP_RUNAWAY_MAX
        except Exception as e:
            # Degraded: senza history check, permetti l'emit (meglio
            # recuperare un run che perderlo per un glitch DB).
            logger.warning(f"[CATCHUP] Errore lookup runaway history: {e}")
            return False

    def _emit_single_catchup(self, schedule_id: str, expected_last: datetime,
                             now: datetime) -> None:
        """Emette il task di catchup + update last_run + audit insert."""
        import app.db.db_service as db
        scheduled_for_iso = expected_last.isoformat()

        logger.info(
            f"[CATCHUP] schedule={schedule_id} recovering run scheduled for "
            f"{scheduled_for_iso} (now={now.isoformat()}, leader={self._leader_id})"
        )

        self.app.send_task(
            "workers.tasks.schedule_task.schedule_pipeline_task",
            kwargs={
                "schedule_id": schedule_id,
                "triggered_by": "catchup",
                "scheduled_for": scheduled_for_iso,
            },
        )

        # C3 follow-up fix 2: NON aggiorniamo last_run qui.
        # Aggiornare last_run all'emit significa: se il task fallisce
        # o crasha prima del completamento, al tick successivo
        # detect_missed_run non vede piu' il missed (dato che last_run
        # e' avanzato a expected_last) e il run e' perso. last_run
        # resta aggiornato dal task stesso quando completa con
        # successo (via ExecutionManager); il dedupe anti-duplicato
        # tra un tick e il successivo e' garantito da
        # _catchup_already_emitted_recently() sopra, che legge la
        # history dell'audit.

        # intent: optional feature beat-safe / outcome: fail-open con
        # diagnostica elevata. Il tick beat continua (nessuna propagazione
        # — romperebbe altri catchup dello stesso tick), ma il fallimento
        # e' loggato a severity CRASH per essere captato da alerting:
        # se l'audit history resta incompleta, il prossimo tick potrebbe
        # ri-emettere il catchup duplicato (dedupe history-based non
        # trovera' la riga pertinente).
        try:
            db.execute(
                "INSERT INTO schedule_catchup_history "
                "(schedule_id, emitted_at, scheduled_for, leader_id) "
                "VALUES (%s, %s, %s, %s)",
                (schedule_id, now.isoformat(), scheduled_for_iso, self._leader_id),
            )
        except Exception as e:
            logger.error(
                f"[CRASH] [CATCHUP] Audit insert fallito per {schedule_id}: {e}. "
                f"Possibile emit duplicato al prossimo tick (dedupe history incompleta).",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Sincronizzazione DB → schedule entries
    # ------------------------------------------------------------------

    def _sync_from_db(self):
        """Carica/aggiorna gli schedule attivi dal DB."""
        try:
            import app.db.db_service as db
            rows = db.fetchall(
                "SELECT id, name, cron_expression, project_id, is_enabled, last_run FROM schedules WHERE is_enabled = TRUE"
            )

            current_ids = set()
            for row in rows:
                sid = row["id"]
                current_ids.add(sid)
                entry_name = f"urby:schedule:{sid}"

                # Crea o aggiorna entry
                schedule = self._parse_cron(row["cron_expression"])
                if schedule is None:
                    continue

                last_run_at = self._parse_dt(row.get("last_run"))

                if entry_name not in self.data:
                    self.data[entry_name] = ScheduleEntry(
                        name=entry_name,
                        task="workers.tasks.schedule_task.schedule_pipeline_task",
                        schedule=schedule,
                        kwargs={"schedule_id": sid, "triggered_by": "cron"},
                        app=self.app,
                        last_run_at=last_run_at or self.app.now(),
                    )
                    logger.info(f"Schedule aggiunto: '{row.get('name', sid)}' cron={row['cron_expression']}")
                else:
                    # Aggiorna schedule se cambiato. `ScheduleEntry.update()`
                    # non accetta `schedule=` come kwarg (signature Celery e'
                    # `update(other: ScheduleEntry)`), quindi ricreiamo l'entry
                    # preservando `last_run_at`. Senza questo fix il cambio
                    # di cron a caldo veniva swallowed come crash silenzioso
                    # (loggato "[CRASH] Errore sync") e lo schedule continuava
                    # con la vecchia cron finche' il beat non veniva riavviato.
                    entry = self.data[entry_name]
                    if str(entry.schedule) != str(schedule):
                        logger.info(f"Schedule modificato: '{row.get('name', sid)}' cron={row['cron_expression']} (era {entry.schedule})")
                        self.data[entry_name] = ScheduleEntry(
                            name=entry_name,
                            task="workers.tasks.schedule_task.schedule_pipeline_task",
                            schedule=schedule,
                            kwargs={"schedule_id": sid, "triggered_by": "cron"},
                            app=self.app,
                            last_run_at=entry.last_run_at,
                        )

            # Rimuovi schedule disattivati
            to_remove = [
                name for name in list(self.data.keys())
                if name.startswith("urby:schedule:") and
                   name.split(":")[-1] not in current_ids
            ]
            for name in to_remove:
                del self.data[name]
                logger.info(f"Schedule rimosso: {name}")

        except Exception as e:
            logger.error(f"[CRASH] Errore sync schedule dal DB: {e}", exc_info=True)

    def _parse_cron(self, cron_expr: str):
        """Parsa espressione cron in un oggetto schedule Celery."""
        try:
            from celery.schedules import crontab
            parts = cron_expr.strip().split()
            if len(parts) != 5:
                logger.warning(f"Cron non valido: {cron_expr!r}")
                return None
            minute, hour, day_of_month, month_of_year, day_of_week = parts
            return crontab(
                minute=minute,
                hour=hour,
                day_of_month=day_of_month,
                month_of_year=month_of_year,
                day_of_week=day_of_week,
            )
        except Exception as e:
            logger.warning(f"Errore parsing cron {cron_expr!r}: {e}")
            return None

    def _parse_dt(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Converte stringa ISO in datetime aware."""
        if not dt_str:
            return None
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Heartbeat su DB
    # ------------------------------------------------------------------

    def _write_heartbeat(self):
        """Scrive il timestamp heartbeat nel DB (usato dall'healthcheck)."""
        try:
            import app.db.db_service as db
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "INSERT INTO beat_heartbeat (id, ts) VALUES ('beat', %s) "
                "ON CONFLICT (id) DO UPDATE SET ts = EXCLUDED.ts",
                (now,)
            )
        except Exception as e:
            logger.warning(f"Errore scrittura heartbeat: {e}")

    # ------------------------------------------------------------------
    # Monitoring task bloccate
    # ------------------------------------------------------------------

    def _check_stuck_tasks(self):
        """Ogni 5 minuti verifica se ci sono lock scheduler scaduti o task bloccate."""
        import time
        now = time.time()
        if now - self._last_stuck_check < 300:
            return
        self._last_stuck_check = now

        try:
            import app.db.db_service as db
            # Lock con expires_at nel passato sono scaduti ma non puliti
            rows = db.fetchall(
                "SELECT key, value, expires_at FROM scheduler_locks "
                "WHERE expires_at IS NOT NULL AND expires_at < NOW()"
            )
            if rows:
                for row in rows:
                    logger.warning(
                        f"[STUCK] Lock scaduto: {row['key']} "
                        f"(valore={row['value']}, scaduto={row['expires_at']})"
                    )
                # Pulisci lock scaduti
                db.execute("DELETE FROM scheduler_locks WHERE expires_at IS NOT NULL AND expires_at < NOW()")
                logger.info(f"[STUCK] Rimossi {len(rows)} lock scaduti")

            # Controlla schedule con last_status = 'running' da più del hard limit
            from app.config import settings
            try:
                hard_limit = int(settings.CELERY_SCHEDULE_HARD_LIMIT)
            except (TypeError, ValueError):
                hard_limit = 3600
            if hard_limit <= 0:
                hard_limit = 3600
            # `INTERVAL '%s seconds'` con placeholder DENTRO il literal
            # SQL e' invalid PostgreSQL syntax. Usiamo `make_interval`
            # che accetta placeholder standard.
            stuck = db.fetchall(
                "SELECT id, name FROM schedules "
                "WHERE last_status = 'running' "
                "AND last_run IS NOT NULL "
                "AND last_run < NOW() - make_interval(secs => %s)",
                (hard_limit,)
            )
            for s in stuck:
                logger.warning(
                    f"[STUCK] Schedule '{s['name']}' ({s['id']}) in stato 'running' "
                    f"da più di {hard_limit}s — possibile task bloccata"
                )
        except Exception as e:
            logger.warning(f"Errore check task bloccate: {e}")

    # ------------------------------------------------------------------
    # Cleanup output eseguibili orfani
    # ------------------------------------------------------------------

    def _cleanup_eseguibili_outputs(self):
        """Ogni 30 minuti rimuove le directory di output eseguibili piu' vecchie di 2 ore.

        Il path /api/eseguibili/{id}/download/{task_id} cancella il file subito
        dopo l'invio, ma se l'utente abbandona il download o il frontend non fa
        mai la GET, il file resterebbe orfano — questo cleanup lo recupera.
        """
        import time as _t
        import shutil as _shutil
        now = _t.time()
        if now - self._last_eseguibili_cleanup < 1800:  # 30 minuti
            return
        self._last_eseguibili_cleanup = now

        try:
            from app.config import settings
            outputs_dir = settings.ESEGUIBILI_OUTPUTS_DIR
            if not outputs_dir.exists():
                return
            ttl_seconds = 2 * 3600  # 2 ore
            removed = 0
            for entry in outputs_dir.iterdir():
                try:
                    if not entry.is_dir():
                        continue
                    age = now - entry.stat().st_mtime
                    if age > ttl_seconds:
                        _shutil.rmtree(entry, ignore_errors=True)
                        removed += 1
                except Exception as e:
                    logger.warning(f"Cleanup: errore su {entry}: {e}")
            if removed:
                logger.info(f"Cleanup eseguibili_outputs: rimosse {removed} directory orfane")
        except Exception as e:
            logger.warning(f"Errore cleanup eseguibili_outputs: {e}")

    # ------------------------------------------------------------------
    # Cleanup bundle IMDF orfani
    # ------------------------------------------------------------------

    def _cleanup_imdf_bundles(self):
        """Ogni 30 minuti rimuove i bundle IMDF su disco piu' vecchi di
        settings.IMDF_BUNDLE_TTL_HOURS (default 48h).

        I bundle vengono persistiti dopo ogni arricchimento IMDF per permettere
        la pubblicazione ArcGIS on-demand (nodo imdf_publisher). Senza cleanup
        la cartella cresce indefinitamente (ogni bundle pesa ~10-50MB).
        """
        import time as _t
        import shutil as _shutil
        now = _t.time()
        if now - self._last_imdf_bundles_cleanup < 1800:  # 30 minuti
            return
        self._last_imdf_bundles_cleanup = now

        try:
            from app.config import settings
            bundles_dir = settings.IMDF_BUNDLES_DIR
            if not bundles_dir.exists():
                return
            ttl_seconds = settings.IMDF_BUNDLE_TTL_HOURS * 3600
            removed = 0
            for entry in bundles_dir.iterdir():
                try:
                    if not entry.is_dir():
                        continue
                    age = now - entry.stat().st_mtime
                    if age > ttl_seconds:
                        _shutil.rmtree(entry, ignore_errors=True)
                        removed += 1
                except Exception as e:
                    logger.warning(f"Cleanup IMDF bundles: errore su {entry}: {e}")
            if removed:
                logger.info(f"Cleanup imdf_bundles: rimossi {removed} bundle scaduti")
        except Exception as e:
            logger.warning(f"Errore cleanup imdf_bundles: {e}")

    # ------------------------------------------------------------------
    # Cleanup orfani DATA_DIR/temp/
    # ------------------------------------------------------------------

    def _cleanup_orphan_temp_files(self):
        """Ogni 30 min rimuove file/dir orfani in DATA_DIR/temp/ piu'
        vecchi di TTL (default 24h, env TEMP_ORPHAN_CLEANUP_HOURS).

        Copre i pattern noti di temp naming:
          - plenora_cached_* / plenora_cached_*   (result_store.get_result_as_file)
          - plenora_{pid}_{hash}/                 (result_store.get_temp_output_path)
          - tmp*                               (NamedTemporaryFile/mkdtemp generici
                                                in file_service, artifacts, ecc.)
          - eseguibili_uploads/*               (cross-worker upload staging,
                                                router.eseguibili)

        La dir `eseguibili_uploads/` resta (container fisso della cache),
        i file dentro vengono puliti individualmente.

        Motivazione: senza questo, un task che crasha fra mkdtemp/open()
        e il finally cleanup lascia residui accumulanti. Il volume temp
        per-worker e' effimero al restart del pod, ma in setup host-fisso
        (oggi `lab.plenora.it`) senza restart, il temp cresce indefinitamente.
        """
        import os as _os
        import time as _t
        import shutil as _shutil
        now = _t.time()
        if now - self._last_temp_orphan_cleanup < 1800:  # 30 min
            return
        self._last_temp_orphan_cleanup = now

        try:
            from app.config import settings
            from pathlib import Path as _Path
            temp_dir = _Path(settings.DATA_DIR) / "temp"
            if not temp_dir.exists():
                return
            # BE-CONFIG-001B: TTL temp orfani migrato a Settings (default
            # tipato 24h in `Settings.DEFAULTS["TEMP_ORPHAN_CLEANUP_HOURS"]`).
            # Override env continua a funzionare via type coercion `int`.
            get_setting = getattr(settings, "get", None)
            if callable(get_setting):
                raw_ttl_hours = settings.get("TEMP_ORPHAN_CLEANUP_HOURS")
            else:
                # Tests patch app.config.settings with lightweight stubs that
                # only expose DATA_DIR. Preserve the historical env override
                # path for those stubs without weakening the real Settings
                # boundary used in production.
                raw_ttl_hours = _os.environ.get("TEMP_ORPHAN_CLEANUP_HOURS", 24)
            ttl_hours = int(raw_ttl_hours)
            ttl_seconds = ttl_hours * 3600
            removed = 0

            for entry in temp_dir.iterdir():
                try:
                    # eseguibili_uploads e' cache permanente: scan ricorsivo
                    # dei file dentro, pulizia individuale per age.
                    if entry.is_dir() and entry.name == "eseguibili_uploads":
                        for child in entry.iterdir():
                            try:
                                age = now - child.stat().st_mtime
                                if age > ttl_seconds:
                                    if child.is_dir():
                                        _shutil.rmtree(child, ignore_errors=True)
                                    else:
                                        child.unlink(missing_ok=True)
                                    removed += 1
                            except Exception as e:
                                logger.warning(
                                    f"Cleanup temp: errore su {child}: {e}"
                                )
                        continue

                    age = now - entry.stat().st_mtime
                    if age > ttl_seconds:
                        if entry.is_dir():
                            _shutil.rmtree(entry, ignore_errors=True)
                        else:
                            entry.unlink(missing_ok=True)
                        removed += 1
                except Exception as e:
                    logger.warning(f"Cleanup temp: errore su {entry}: {e}")

            if removed:
                logger.info(
                    f"Cleanup DATA_DIR/temp: rimossi {removed} orfani "
                    f"(>{ttl_hours}h)"
                )
        except Exception as e:
            logger.warning(f"Errore cleanup temp orphans: {e}")

    # ------------------------------------------------------------------
    # Retention tabella plenora.logs
    # ------------------------------------------------------------------

    def _cleanup_old_schedule_logs(self):
        """Ogni 24h rimuove righe da plenora.schedule_logs piu' vecchie di
        SCHEDULE_LOGS_RETENTION_DAYS (default 90gg, configurabile via env).

        Tabella separata da `plenora.logs` (application logs): contiene
        gli step di esecuzione di ogni run scheduler con metadata estesi
        (steps JSONB, schedule_info, pipeline_info). Senza retention
        cresce indefinitamente — un schedule giornaliero genera ~365
        righe/anno, 100 schedule attivi → ~36k righe/anno (ok), ma
        debug verbose puo' moltiplicarle x10.

        L'API `ScheduleLogService.delete_logs_before(date)` esiste gia'
        (test coperto da test_log_service_db_contract.py): qui la
        chiamiamo periodicamente.
        """
        import time as _t
        now = _t.time()
        if now - self._last_schedule_logs_retention < 86400:  # 24h
            return
        self._last_schedule_logs_retention = now

        try:
            from app.config import settings
            # Niente `or 90` per non collassare 0 → 90 (l'utente che imposta
            # esplicitamente 0 vuole disabilitare). Il fallback al default
            # avviene solo se l'attributo manca o non e' int.
            raw = getattr(settings, "SCHEDULE_LOGS_RETENTION_DAYS", 90)
            try:
                days = int(raw)
            except (TypeError, ValueError):
                days = 90
            if days <= 0:
                return  # retention disabilitata
            from datetime import datetime, timezone, timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
            from app.core.scheduler.log_service import ScheduleLogService
            deleted = ScheduleLogService().delete_logs_before(cutoff)
            if deleted:
                logger.info(
                    f"Retention schedule_logs: rimosse {deleted} righe "
                    f"piu' vecchie di {days}gg (cutoff={cutoff.isoformat()})"
                )
        except Exception as e:
            logger.warning(f"Errore retention schedule_logs: {e}")

    def _archive_old_logs(self):
        """Ogni 24h archivia+purga plenora.logs via LogArchiveWriter
        (O-ARC Batch 3, ADR-0014).

        Gate sequenziali:
        1. 24h temporal gate (_last_logs_archive).
        2. LOG_ARCHIVE_ENABLED=False -> early return (no-op).
        3. LOG_RETENTION_DAYS <= 0 -> retention disabilitata, early return
           (simmetrico al baseline _cleanup_old_logs).

        Su successo:
          - _last_logs_archive aggiornato (il 24h gate scatta al prossimo tick).
          - logger.info con archived_rows + numero oggetti creati.

        Su failure (eccezione dal writer o dalle closures):
          - logger.warning(exc_info=True).
          - _last_logs_archive NON aggiornato: il run riparte al prossimo
            tick. La finestra 24h si "chiude" solo con un run pulito.
          - L'eccezione non propaga: il resto del tick continua.

        Il bypass PG `plenora.retention_cleanup` resta scoped alla DELETE
        callable (stesso cursor di SET LOCAL), mai esposto globalmente.
        """
        import time as _t
        now = _t.time()
        if now - self._last_logs_archive < 86400:  # 24h
            return

        from app.config import settings
        if not getattr(settings, "LOG_ARCHIVE_ENABLED", False):
            return
        # Phase 2 archive path: 0 significa disabled (coerente con triage
        # Batch 3 e con AUDIT_RETENTION_DAYS>=0 ratificato in Batch 2).
        # Non uso `... or 30` come il legacy _cleanup_old_logs: qui 0 deve
        # passare nel gate `days <= 0` e fare skip. Asimmetria volontaria
        # col cleanup baseline.
        try:
            days = int(getattr(settings, "LOG_RETENTION_DAYS", 30))
        except (TypeError, ValueError):
            days = 30
        if days <= 0:
            return  # retention disabilitata (0 o negativo)

        try:
            from datetime import timedelta
            from app.shared.cloud_backends import get_log_archive_storage
            from app.services.log_archive import LogArchiveWriter
            from app.db.pg_connection import fetchall, transaction

            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            storage = get_log_archive_storage()
            writer = LogArchiveWriter(
                storage=storage,
                key_prefix=settings.LOG_ARCHIVE_KEY_PREFIX,
                batch_size=settings.LOG_ARCHIVE_BATCH_ROWS,
            )

            def select_rows(cutoff_ts, batch_size):
                # Schema-agnostic: search_path del pool risolve `logs` a
                # plenora.logs (stesso pattern di _cleanup_old_logs).
                # ORDER BY stabile (ts, id) richiesto dal writer per non
                # saltare righe fra iterazioni successive.
                return fetchall(
                    "SELECT * FROM logs WHERE ts < %s "
                    "ORDER BY ts ASC, id ASC LIMIT %s",
                    (cutoff_ts, batch_size),
                )

            def retention_bypass_execute(sql, params):
                # Una nuova tx per batch: SET LOCAL + DELETE sullo stesso
                # cursor (pattern _cleanup_old_logs), altrimenti il trigger
                # plenora.block_log_modifications scatta sulla DELETE.
                with transaction() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SET LOCAL plenora.retention_cleanup = 'true'"
                        )
                        cur.execute(sql, params)

            result = writer.archive_and_purge(
                "logs", cutoff, select_rows, retention_bypass_execute,
            )
            self._last_logs_archive = now
            logger.info(
                f"Archive logs: {result['archived_rows']} righe "
                f"in {len(result['object_keys'])} oggetti "
                f"(cutoff={cutoff.isoformat()}, days={days})"
            )
            # B4: observability circolare su plenora.logs. INFO non passa
            # dal PostgresLogHandler (LOG_LEVEL_DB=WARNING default), quindi
            # INSERT diretta via pg_execute. Shape colonne identica al
            # handler canonico (utils/logger.py:340-344). Ratificata in
            # ADR-0014 §B. Best-effort: un fallimento observability non
            # contamina lo stato "archive ok" (slot gia' aggiornato sopra).
            try:
                import json as _json
                from app.db.pg_connection import execute as pg_execute
                obs_ts = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.%f"
                )[:-3]
                obs_details = _json.dumps({
                    "table": "logs",
                    "cutoff": cutoff.isoformat(),
                    "archived_rows": result["archived_rows"],
                    "object_keys": list(result["object_keys"]),
                    "batch_runs": len(result["object_keys"]),
                }, default=str)
                pg_execute(
                    "INSERT INTO logs (ts, level, module, op, msg, "
                    "project_id, group_id, run_id, node_id, details, error) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        obs_ts, "INFO", "workers", "log_archive",
                        f"Archive logs: {result['archived_rows']} righe in "
                        f"{len(result['object_keys'])} oggetti",
                        "", "", "", "", obs_details, "",
                    ),
                )
            except Exception as obs_err:
                logger.warning(
                    f"Observability log_archive fallita (archive logs ok): "
                    f"{obs_err}"
                )
        except Exception as e:
            logger.warning(f"Errore archive logs: {e}", exc_info=True)

    def _cleanup_old_logs(self):
        """Ogni 24h rimuove righe da plenora.logs piu' vecchie di LOG_RETENTION_DAYS.

        Senza retention la tabella cresce all'infinito. Il default (30 giorni)
        e' configurabile via settings.LOG_RETENTION_DAYS.
        """
        import time as _t
        now = _t.time()
        if now - self._last_logs_retention < 86400:  # 24h
            return
        self._last_logs_retention = now

        try:
            from app.config import settings
            # O-ARC Batch 3: con archive attivo il purge canonico passa
            # dal writer (_archive_old_logs nel tick, prima di questo
            # metodo). Skip per non duplicare la DELETE.
            if getattr(settings, "LOG_ARCHIVE_ENABLED", False):
                return
            days = int(getattr(settings, "LOG_RETENTION_DAYS", 30) or 30)
            if days <= 0:
                return  # retention disabilitata
            from app.db.pg_connection import execute, fetchall
            # Niente schema hard-coded `plenora.`: il pool apre le
            # connessioni con `search_path=<_DB_SCHEMA>,public`, quindi
            # ci riferiamo alla tabella `logs` senza prefisso e PostgreSQL
            # la risolve nello schema corrente. Cosi' se DB_SCHEMA punta
            # a `plenora_staging`, la retention lavora sullo schema giusto.
            before = fetchall(
                "SELECT COUNT(*) AS c FROM logs WHERE ts < now() - make_interval(days => %s)",
                (days,),
            )[0]["c"]
            if before:
                # SET LOCAL + DELETE devono stare nella stessa transazione:
                # `execute()` apre/committa una tx per call, azzerando SET LOCAL.
                # Il flag consente a policy/trigger PG di distinguere il
                # cleanup schedulato dalle DELETE applicative.
                from app.db.pg_connection import transaction
                with transaction() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SET LOCAL plenora.retention_cleanup = 'true'")
                        cur.execute(
                            "DELETE FROM logs WHERE ts < now() - make_interval(days => %s)",
                            (days,),
                        )
                logger.info(f"Retention logs: rimosse {before} righe piu' vecchie di {days}gg")
        except Exception as e:
            logger.warning(f"Errore retention logs: {e}")

    def _archive_old_audit_actions(self):
        """Ogni 24h archivia+purga plenora.audit_actions via LogArchiveWriter
        (O-ARC Batch 3, ADR-0014).

        Primo path di DELETE autorizzato su audit_actions: con
        LOG_ARCHIVE_ENABLED=False (default) la tabella continua a crescere
        come oggi; con ENABLED=True e AUDIT_RETENTION_DAYS>0 il writer
        archivia+purga. Non esiste un `_cleanup_old_audit_actions` legacy
        da gate-are.

        Gate sequenziali:
        1. 24h temporal gate (_last_audit_archive).
        2. LOG_ARCHIVE_ENABLED=False -> early return (no-op).
        3. AUDIT_RETENTION_DAYS <= 0 -> retention disabilitata, early return.
           Critico: AUDIT_RETENTION_DAYS=0 con ENABLED=True darebbe
           cutoff=now e il writer itererebbe finche' SELECT svuota la
           tabella. Il gate lo esclude a monte.

        Su successo:
          - _last_audit_archive aggiornato (24h gate scatta al prossimo tick).
          - logger.info con archived_rows + numero oggetti creati.

        Su failure (eccezione dal writer o dalle closures):
          - logger.warning(exc_info=True).
          - _last_audit_archive NON aggiornato: retry al prossimo tick.
          - L'eccezione non propaga: il resto del tick continua.

        Il bypass PG `plenora.retention_cleanup` resta scoped alla DELETE
        callable (stesso cursor di SET LOCAL), mai esposto globalmente.
        """
        import time as _t
        now = _t.time()
        if now - self._last_audit_archive < 86400:  # 24h
            return

        from app.config import settings
        if not getattr(settings, "LOG_ARCHIVE_ENABLED", False):
            return
        # Phase 2 archive path: 0 significa disabled (gia' ratificato per
        # AUDIT_RETENTION_DAYS in Batch 2). Niente `... or 365` per evitare
        # che lo 0 legato all'opt-out venga mascherato in 365. Il gate
        # `days <= 0` intercetta 0 e valori negativi.
        try:
            days = int(getattr(settings, "AUDIT_RETENTION_DAYS", 365))
        except (TypeError, ValueError):
            days = 365
        if days <= 0:
            return  # retention disabilitata (0 o negativo)

        try:
            from datetime import timedelta
            from app.shared.cloud_backends import get_log_archive_storage
            from app.services.log_archive import LogArchiveWriter
            from app.db.pg_connection import fetchall, transaction

            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            storage = get_log_archive_storage()
            writer = LogArchiveWriter(
                storage=storage,
                key_prefix=settings.LOG_ARCHIVE_KEY_PREFIX,
                batch_size=settings.LOG_ARCHIVE_BATCH_ROWS,
            )

            def select_rows(cutoff_ts, batch_size):
                # Schema-agnostic: search_path del pool risolve
                # `audit_actions` a plenora.audit_actions. Colonna
                # temporale = `timestamp` (O-ARC assunzione #4).
                return fetchall(
                    "SELECT * FROM audit_actions WHERE timestamp < %s "
                    "ORDER BY timestamp ASC, id ASC LIMIT %s",
                    (cutoff_ts, batch_size),
                )

            def retention_bypass_execute(sql, params):
                # Una nuova tx per batch: SET LOCAL + DELETE sullo stesso
                # cursor (pattern _cleanup_old_logs / _archive_old_logs),
                # altrimenti il trigger plenora.block_log_modifications
                # scatta sulla DELETE.
                with transaction() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SET LOCAL plenora.retention_cleanup = 'true'"
                        )
                        cur.execute(sql, params)

            result = writer.archive_and_purge(
                "audit_actions", cutoff, select_rows, retention_bypass_execute,
            )
            self._last_audit_archive = now
            logger.info(
                f"Archive audit_actions: {result['archived_rows']} righe "
                f"in {len(result['object_keys'])} oggetti "
                f"(cutoff={cutoff.isoformat()}, days={days})"
            )
            # B4: observability circolare su plenora.logs (tabella logs,
            # non audit_actions: la chiusura del cerchio log-del-log
            # va sempre su plenora.logs). Shape identica al handler
            # canonico (utils/logger.py:340-344). ADR-0014 §B.
            try:
                import json as _json
                from app.db.pg_connection import execute as pg_execute
                obs_ts = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.%f"
                )[:-3]
                obs_details = _json.dumps({
                    "table": "audit_actions",
                    "cutoff": cutoff.isoformat(),
                    "archived_rows": result["archived_rows"],
                    "object_keys": list(result["object_keys"]),
                    "batch_runs": len(result["object_keys"]),
                }, default=str)
                pg_execute(
                    "INSERT INTO logs (ts, level, module, op, msg, "
                    "project_id, group_id, run_id, node_id, details, error) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        obs_ts, "INFO", "workers", "log_archive",
                        f"Archive audit_actions: {result['archived_rows']} "
                        f"righe in {len(result['object_keys'])} oggetti",
                        "", "", "", "", obs_details, "",
                    ),
                )
            except Exception as obs_err:
                logger.warning(
                    f"Observability log_archive fallita "
                    f"(archive audit_actions ok): {obs_err}"
                )
        except Exception as e:
            logger.warning(f"Errore archive audit_actions: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Storage reconciliation — BE-GO-READY-STORAGE-ENGINE-006
    # ------------------------------------------------------------------

    def _reconcile_storage(self):
        """Beat-driven reconciler per `plenora.storage_reconciliation_log`.

        Cadence-gated da `RECONCILIATION_BEAT_INTERVAL_MIN` (default 15
        minuti). Supporta solo `operation="delete"` in questa fase
        (BE-GO-READY-STORAGE-ENGINE-006). Upload-intent (W2 race),
        storage-side scan e intermediate artifact pair restano fuori
        scope.

        Per ciascuna riga `pending` due:
          - chiama `compensating_delete(storage_key, domain=domain,
            reason="reconciler_retry")`;
          - outcome `ok` o `skipped` -> `mark_orphan_deleted`;
          - outcome `failed`:
              - se attempts dopo l'incremento >= max_attempts -> `mark_lost`;
              - altrimenti `mark_failed_attempt` + `next_retry_at`
                via backoff esponenziale.

        Best-effort: tutte le exception sono catturate e loggate; il
        tick beat continua. Il time-gate slot
        `_last_storage_reconcile` viene aggiornato SEMPRE (anche su
        errore) per evitare di re-eseguire un fetch fallito a ogni
        tick.

        Cap <= 0 sull'interval o batch_size disabilita il hook (escape
        hatch ops).
        """
        import time as _t
        now_ts = _t.time()

        try:
            from app.config import settings
        except Exception:
            return  # config non disponibile: skip silente

        # Tutti i tunables via Settings.get (ADR-0021); fallback a
        # default difensivi se i test usano un SimpleNamespace stub.
        get_setting = getattr(settings, "get", None)

        def _read_int(key: str, default: int) -> int:
            try:
                if callable(get_setting):
                    raw = get_setting(key, default)
                else:
                    raw = getattr(settings, key, default)
                return int(raw) if raw is not None else default
            except (TypeError, ValueError):
                return default

        interval_s = _read_int("STORAGE_RECONCILE_INTERVAL_SECONDS", 900)
        if interval_s <= 0:
            return  # disabled

        if now_ts - self._last_storage_reconcile < interval_s:
            return
        self._last_storage_reconcile = now_ts

        batch_size = _read_int("STORAGE_RECONCILE_BATCH_SIZE", 50)
        if batch_size <= 0:
            return  # disabled

        max_attempts = _read_int("STORAGE_RECONCILE_MAX_ATTEMPTS", 5)
        if max_attempts < 1:
            max_attempts = 1

        backoff_base = _read_int("STORAGE_RECONCILE_BACKOFF_BASE_SECONDS", 60)
        if backoff_base <= 0:
            backoff_base = 60

        try:
            from app.shared import storage_reconciliation as _recon
            from app.shared.storage_compensation import compensating_delete
        except Exception as e:
            logger.warning(
                f"Reconcile storage: import fallito: {e}", exc_info=True
            )
            return

        try:
            rows = _recon.fetch_due_pending_deletes(limit=batch_size)
        except Exception as e:
            logger.warning(
                f"Reconcile storage: fetch fallito: {e}", exc_info=True
            )
            return

        if not rows:
            return

        ok_count = 0
        failed_count = 0
        lost_count = 0
        for row in rows:
            try:
                # Stream supporta solo "delete". `fetch_due_pending_deletes`
                # gia' filtra a SQL-level; questa e' una belt-and-suspenders.
                if row.operation != "delete":
                    continue

                outcome = compensating_delete(
                    row.storage_key,
                    domain=row.domain,
                    reason="reconciler_retry",
                    extras={"reconciliation_id": row.id},
                )

                if outcome == "ok" or outcome == "skipped":
                    _recon.mark_orphan_deleted(row.id)
                    ok_count += 1
                else:
                    # outcome == "failed"
                    new_attempts = row.attempts + 1
                    if new_attempts >= max_attempts:
                        _recon.mark_lost(
                            row.id,
                            error=f"max_attempts_exceeded ({new_attempts})",
                        )
                        lost_count += 1
                    else:
                        next_retry = _recon.compute_next_retry_at(
                            new_attempts,
                            base_seconds=backoff_base,
                        )
                        _recon.mark_failed_attempt(
                            row.id,
                            error="compensating_delete_failed",
                            next_retry_at=next_retry,
                        )
                        failed_count += 1
            except Exception as e:
                logger.warning(
                    f"Reconcile storage: errore su row id={row.id}: {e}",
                    exc_info=True,
                )

        if ok_count or failed_count or lost_count:
            logger.info(
                "event=storage_reconciliation_beat_tick "
                "ok=%d failed=%d lost=%d batch=%d",
                ok_count, failed_count, lost_count, len(rows),
            )

    # ------------------------------------------------------------------
    # Persistenza — non usiamo file shelve, tutto su DB
    # ------------------------------------------------------------------

    def setup_schedule(self):
        """Inizializza schedule vuoto — verrà popolato dal DB al primo tick."""
        self.data = {}
        self._sync_from_db()
        logger.info(f"Beat avviato con {len(self.data)} schedule attivi")

    def sync(self):
        """Sync periodico richiesto da Celery — già gestito in tick()."""
        pass

    def close(self):
        """Cleanup alla chiusura.

        Rilascia il lock leader su Redis se e' ancora posseduto da questo
        processo: cosi' un altro beat puo' prenderlo subito invece di
        aspettare il TTL di 120s. La release e' "safe": controlla che il
        valore del lock sia ancora il nostro leader_id prima di cancellare
        (evita race durante failover dove il nostro TTL e' scaduto e un
        altro processo ha ripreso nel frattempo)."""
        if self._is_leader:
            try:
                client = self._get_redis()
                if client is not None:
                    current_raw = client.get(_LEADER_LOCK_KEY)
                    current = current_raw.decode("utf-8") if current_raw else None
                    if current == self._leader_id:
                        client.delete(_LEADER_LOCK_KEY)
                        logger.info(f"[LEADER] Rilasciato su close: {self._leader_id}")
                        self._record_leader_transition("released")
                    else:
                        logger.info(
                            f"[LEADER] Skip release su close: il lock e' ora "
                            f"posseduto da '{current}' (io={self._leader_id})"
                        )
            except Exception as e:
                logger.warning(f"[LEADER] Errore release su close: {e}")
        logger.info("PlenoraBeatScheduler chiuso")
        super().close()
