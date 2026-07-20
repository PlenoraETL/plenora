"""
PlenoraBeatScheduler: scheduler del beat celery-free.

- schedule caricati dal DB Postgres (tabella `schedules`)
- date-math cron/interval su croniter (app.core.scheduler.cron_schedule)
- emit dei task via layer dispatch (Dramatiq), loop guidato da APScheduler
"""

import json
import importlib
import logging
import os
import os as _os
import socket
import time
import time as _t
from datetime import datetime, timezone, timedelta
from datetime import datetime as _dt, timedelta as _td, timezone as _tz
from pathlib import Path as _Path
from typing import Any, Optional
from app.core.scheduler.cron_schedule import (
    BeatEntry,
    CronSchedule,
    beat_now,
    beat_timezone,
)
from app.core.scheduler.beat_schedule_config import beat_schedule_config
from app import config as app_config
from app.core.eseguibili import download_claim_store, output_store
from app.core.io import object_storage as object_storage_module
from app.core.scheduler.job_dispatcher import (
    SCHEDULE_PIPELINE_TASK_NAME,
    scheduled_run_kwargs,
)
from app.core.scheduler import log_archive as scheduler_log_archive
from app.core.scheduler import log_service as scheduler_log_service
from app.core.scheduler import schedule_service as schedule_service_module
from app.core.scheduler import beat_leader_store
from app.core.resources import storage_reconciliation as storage_reconciliation_module
from app.core.task_runtime.runtime_leases import (
    cleanup_runtime_leases,
    recover_stale_task_claims,
)
from app.core.io.local_cleanup import rmtree_safe
from app.infrastructure.storage import compensation as storage_compensation

# Logger stdlib propagante (era celery.utils.log.get_logger): i record
# raggiungono root, cosi' caplog e gli handler globali li catturano. NON
# usare app.utils.logger qui (wrapper con propagate=False -> caplog cieco).
logger = logging.getLogger(__name__)

DEFAULT_MAX_INTERVAL = 60.0

# TTL=120s + refresh a ogni tick (~60s): margine 2x per sopravvivere
# a un tick lento o a un transient di rete senza perdere leadership.
# Il lock leader vive su PostgreSQL (beat_leader_locks).
_LEADER_LOCK_TTL_SECONDS = 120

_PG_CONNECTION_MODULE = importlib.import_module("app.db.pg_connection")
_ORIGINAL_PG_FETCHALL = getattr(_PG_CONNECTION_MODULE, "fetchall")
_ORIGINAL_PG_EXECUTE = getattr(_PG_CONNECTION_MODULE, "execute")
_ORIGINAL_PG_UNIT_OF_WORK = getattr(_PG_CONNECTION_MODULE, "unit_of_work")


def _pg_fetchall(sql: str, params: tuple = ()):
    patched = getattr(_PG_CONNECTION_MODULE, "fetchall")
    if patched is not _ORIGINAL_PG_FETCHALL:
        return patched(sql, params)

    with _ORIGINAL_PG_UNIT_OF_WORK() as session:
        return session.fetchall(sql, params)


def _pg_execute(sql: str, params: tuple = ()):
    patched = getattr(_PG_CONNECTION_MODULE, "execute")
    if patched is not _ORIGINAL_PG_EXECUTE:
        return patched(sql, params)

    with _ORIGINAL_PG_UNIT_OF_WORK() as session:
        return session.execute(sql, params)


def _pg_transaction():
    patched = getattr(_PG_CONNECTION_MODULE, "unit_of_work")
    if patched is not _ORIGINAL_PG_UNIT_OF_WORK:
        return patched()

    return _ORIGINAL_PG_UNIT_OF_WORK()


class PlenoraBeatScheduler:
    """
    Beat Scheduler custom che legge gli schedule da PostgreSQL.

    La date-math degli schedule (cron/interval) e' su croniter (vedi
    app.core.scheduler.cron_schedule), il trasporto dei task passa dal
    layer dispatch Dramatiq. Il loop e' guidato da APScheduler
    (workers/beat.py).

    Ogni max_interval secondi:
    1. Carica tutti gli schedule attivi dal DB
    2. Per ognuno verifica se è scaduto (cron/interval)
    3. Dispatcha il task se necessario
    4. Aggiorna last_run nel DB
    5. Scrive heartbeat nel DB
    """

    max_interval = DEFAULT_MAX_INTERVAL

    def __init__(self) -> None:
        self._db_loaded = False
        self._leader_id = f"{os.getpid()}@{socket.gethostname()}"
        self._is_leader = False
        self.data: dict[str, BeatEntry] = {}
        self.max_interval = DEFAULT_MAX_INTERVAL
        # Lock leader su PostgreSQL, iniettabile nei test.
        self._leader_store = beat_leader_store
        logger.info(
            f"PlenoraBeatScheduler avviato (schedule + leader lock da "
            f"Postgres, leader_id={self._leader_id})"
        )

    def _try_become_leader(self) -> bool:
        """Tenta di acquisire o refreshare il lock leader su PostgreSQL.

        Un singolo upsert atomico (beat_leader_store.acquire_or_refresh)
        acquisisce se il lock e' libero/scaduto o lo rinnova se sono gia'
        owner; altrimenti standby.

        Degraded mode (PostgreSQL unavailable o errore durante acquire):
        - Default **fail-closed**: standby, `return False`. Evita che due
          beat diventino entrambi leader durante un flap DB riaprendo il
          doppio enqueue.
        - Con `SCHEDULE_BEAT_LEADER_FAIL_OPEN=True`: assume leadership
          single-instance. Utile per deployment mono-host dove nessun altro
          beat gira mai.

        Il flag e' letto a ogni chiamata dai settings (non cache), cosi'
        puo' essere cambiato runtime via env var per incident response
        senza restart del beat.

        Returns:
            True se questo processo e' (o e' diventato) leader.
        """
        try:
            acquired = self._leader_store.acquire_or_refresh_beat_leader(
                owner_token=self._leader_id,
                ttl_seconds=_LEADER_LOCK_TTL_SECONDS,
            )
        except Exception as e:
            logger.error(
                f"[LEADER] Errore PostgreSQL durante acquire leader: {e}",
                exc_info=True,
            )
            return self._degraded_leader_result()

        was_leader = self._is_leader
        self._is_leader = acquired
        if acquired and not was_leader:
            logger.info(f"[LEADER] Acquisito: {self._leader_id}")
        elif not acquired and was_leader:
            logger.warning(
                f"[LEADER] Perso (lock scaduto e ripreso da un altro beat, "
                f"io={self._leader_id}). Passo in standby."
            )
        return acquired

    def _degraded_leader_result(self) -> bool:
        """Esito della leader election quando PostgreSQL non risponde."""
        fail_open = bool(
            getattr(app_config.settings, "SCHEDULE_BEAT_LEADER_FAIL_OPEN", False)
        )
        was_leader = self._is_leader
        if fail_open:
            if not was_leader:
                logger.warning(
                    "[LEADER] PostgreSQL unavailable — fallback single-instance "
                    "leader (SCHEDULE_BEAT_LEADER_FAIL_OPEN=True)"
                )
            self._is_leader = True
            return True
        if was_leader:
            logger.warning(
                "[LEADER] PostgreSQL unavailable — perso, passo in standby "
                "(fail-closed default). Set SCHEDULE_BEAT_LEADER_FAIL_OPEN=True "
                "per assumere leadership single-instance."
            )
        else:
            logger.warning(
                "[LEADER] PostgreSQL unavailable — beat in standby "
                "(fail-closed default)"
            )
        self._is_leader = False
        return False

    # Entry-point principale: chiamato a intervallo fisso dal runner
    # APScheduler (workers/beat.py)

    _last_stuck_check: float = 0.0
    _last_eseguibili_cleanup: float = 0.0
    _last_logs_retention: float = 0.0
    _last_logs_archive: float = 0.0
    _last_audit_archive: float = 0.0
    _last_schedule_logs_retention: float = 0.0
    _last_temp_orphan_cleanup: float = 0.0
    _last_storage_reconcile: float = 0.0
    _last_task_results_cleanup: float = 0.0

    def tick(self, event_t: Optional[object] = None, **kwargs: object) -> float:
        """Tick del beat:
        1. Leader election (lock su PostgreSQL). Se non-leader → standby.
        2. Se leader: sync schedule dal DB + heartbeat + cleanup jobs
           + catchup dei missed runs (opt-in per schedule).
        3. Emette i task scaduti via dispatch layer.

        In standby il beat ritorna `max_interval` senza toccare il DB
        ne' il broker: zero enqueue duplicati con un secondo
        processo beat in parallelo.
        """
        if not self._try_become_leader():
            return self.max_interval

        self._sync_from_db()
        self._emit_catchup_runs()
        self._write_heartbeat()
        self._check_stuck_tasks()
        self._cleanup_eseguibili_outputs()
        self._archive_old_logs()
        self._cleanup_old_logs()
        self._cleanup_old_schedule_logs()
        self._archive_old_audit_actions()
        self._cleanup_orphan_temp_files()
        self._reconcile_storage()
        self._cleanup_task_results()
        self._drain_task_dispatch_outbox()
        self._recover_runtime_leases()
        # Gli schedule dovuti passano dal layer dispatch mantenendo la stessa
        # valutazione di scadenza di BeatEntry.is_due.
        return self._emit_due_via_dispatch()

    def _drain_task_dispatch_outbox(self) -> None:
        """Retry API task submissions durably persisted before RabbitMQ."""
        try:
            from app.core.orchestrator.task_dispatch_outbox import (
                drain_task_dispatch_outbox,
            )

            published, failed = drain_task_dispatch_outbox(limit=100)
            if published or failed:
                logger.info(
                    "[OUTBOX] published=%d failed=%d",
                    published,
                    failed,
                )
        except Exception as exc:
            logger.error(
                "[OUTBOX] drain fallito: %s",
                type(exc).__name__,
                exc_info=True,
            )

    def _recover_runtime_leases(self) -> None:
        """Finalize tasks abandoned by a dead worker process."""
        try:
            stale = recover_stale_task_claims(
                limit=int(
                    app_config.settings.get(
                        "TASK_EXECUTION_STALE_BATCH_SIZE",
                        100,
                    )
                ),
                result_ttl_seconds=int(app_config.settings.TASK_RESULT_EXPIRES),
                recovery_grace_seconds=int(
                    app_config.settings.get(
                        "TASK_EXECUTION_RECOVERY_GRACE_SECONDS",
                        30,
                    )
                ),
            )
            removed_workers, removed_claims = cleanup_runtime_leases(
                retention_seconds=int(
                    app_config.settings.get(
                        "RUNTIME_LEASE_RETENTION_SECONDS",
                        app_config.settings.TASK_RESULT_EXPIRES,
                    )
                )
            )
            if stale or removed_workers or removed_claims:
                logger.warning(
                    "[RUNTIME-LEASE] stale_failed=%d workers_pruned=%d "
                    "claims_pruned=%d",
                    len(stale),
                    removed_workers,
                    removed_claims,
                )
        except Exception:
            logger.exception("[RUNTIME-LEASE] recovery fallita")

    def _dispatch_schedule_task(self, name: str, kwargs: dict) -> None:
        """Dispatcha un task schedule via il layer runtime-agnostico.

        Import locale: evita un edge top-level workers->task_runtime nel
        grafo e mantiene il dispatch lazy (serve solo a emit-time)."""
        from app.core.task_runtime.dispatch import dispatch_task_by_name

        dispatch_task_by_name(name, kwargs=kwargs)

    def _emit_due_via_dispatch(self) -> float:
        """Emette gli schedule dovuti, rimpiazzo Celery-agnostico di
        super().tick().

        Riusa ``ScheduleEntry.is_due()`` (stessa matematica cron di Celery
        beat) per decidere cosa e' dovuto e avanza ``last_run_at`` in-memory
        via ``next(entry)`` esattamente come Celery — comportamento
        identico, niente doppi-fire. Cambia solo il trasporto: il task va
        sul layer dispatch invece che su self.app.send_task.

        Ritorna l'intervallo di sleep fino al prossimo schedule dovuto
        (clampato a max_interval)."""
        remaining: list[float] = []
        for name, entry in list(getattr(self, "data", {}).items()):
            try:
                is_due, next_time = entry.is_due()
            except Exception as e:
                logger.error(
                    f"[BEAT] is_due fallita per entry {name}: {e}", exc_info=True
                )
                continue
            if is_due:
                try:
                    self._dispatch_schedule_task(
                        entry.task, dict(entry.kwargs or {})
                    )
                    logger.info(f"[BEAT] emesso {entry.task} (entry={name})")
                except Exception as e:
                    logger.error(
                        f"[CRASH] [BEAT] emit fallito per {name}: {e}",
                        exc_info=True,
                    )
                # Avanza last_run_at in-memory come Celery (anche se il
                # dispatch fallisce: il retry e' demandato a catchup/policy,
                # non a un re-emit immediato che duplicherebbe).
                self.data[name] = next(entry)
            if next_time is not None:
                remaining.append(next_time)
        return min([self.max_interval, *remaining])

    # Cap anti-runaway: se uno schedule emette piu' di N catchup nei
    # `_CATCHUP_RUNAWAY_WINDOW_MIN` minuti precedenti, il beat skippa
    # ulteriori emit + logga WARNING. Protegge da loop dovuti a bug in
    # update di last_run o cron expression degenerate
    _CATCHUP_RUNAWAY_MAX = 5
    _CATCHUP_RUNAWAY_WINDOW_MIN = 10

    # Dedupe window: se lo stesso scheduled_for e' stato gia' emesso nelle
    # ultime N ore, NON ri-emettiamo (evita spam duplicati finche' il primo
    # task e' in volo nella coda del broker). Dopo la finestra, un eventuale
    # task morto viene re-emesso al prossimo detect
    _CATCHUP_DEDUPE_WINDOW_HOURS = 2

    def _emit_catchup_runs(self) -> None:
        """Emette al piu' un task di catchup per ciascuno schedule con
        `catchup_missed_runs=True` che ha un missed run entro la grace
        window configurata.

        Step:
        1. Query schedule enabled (parse config JSON per leggere flag).
        2. Per ciascuno: detect_missed_run -> None se nulla da recuperare.
        3. Se c'e' missed:
           a. Check cap anti-runaway su schedule_catchup_history.
           b. Emit schedule_pipeline_task con triggered_by='catchup' +
              scheduled_for=<ISO del tick logico>.
           c. UPDATE schedules SET last_run=<expected_last> (prevent re-trigger).
           d. INSERT schedule_catchup_history (audit).

        Robustezza: errori DB non crashano il tick (swallowed + loggati).
        """
        try:
            rows = _pg_fetchall(
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

                expected_last = schedule_service_module.detect_missed_run(
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
    def _parse_config_json(config_raw: object) -> Optional[dict[str, object]]:
        """Parsa il JSON `config` di uno schedule. Ritorna dict o None."""
        if not config_raw:
            return None
        if isinstance(config_raw, dict):
            return config_raw
        try:
            return json.loads(config_raw)
        except Exception:
            return None

    def _catchup_already_emitted_recently(
        self, schedule_id: str, expected_last: datetime, now: datetime,
    ) -> bool:
        """True se esiste gia' una riga in schedule_catchup_history per
        lo stesso (schedule_id, scheduled_for) emessa nelle ultime
        _CATCHUP_DEDUPE_WINDOW_HOURS ore.

        Dedupe window necessario perche' last_run viene aggiornato dal task
        completato, non al momento dell'emit.

        Degraded: in caso di errore DB restituisce False (preferiamo
        un eventuale duplicato al drop di un catchup legittimo).
        """
        try:
            window_start = now - timedelta(hours=self._CATCHUP_DEDUPE_WINDOW_HOURS)
            rows = _pg_fetchall(
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
            window_start = now - timedelta(minutes=self._CATCHUP_RUNAWAY_WINDOW_MIN)
            rows = _pg_fetchall(
                "SELECT COUNT(*) AS count FROM schedule_catchup_history "
                "WHERE schedule_id = %s AND emitted_at >= %s",
                (schedule_id, window_start.isoformat()),
            )
            count = int(rows[0]["count"]) if rows else 0
            return count >= self._CATCHUP_RUNAWAY_MAX
        except Exception as e:
            # Degraded: senza history check, permetti l'emit (meglio
            # recuperare un run che perderlo per un glitch DB
            logger.warning(f"[CATCHUP] Errore lookup runaway history: {e}")
            return False

    def _emit_single_catchup(self, schedule_id: str, expected_last: datetime,
                             now: datetime) -> None:
        """Emette il task di catchup + audit insert + last_run update.

        INSERT audit history PRIMA di `send_task`: se l'INSERT fallisce,
        abortiamo l'emit e il tick successivo ritenta lo stesso missed run.
        Se il task viene inviato solo dopo history persistita, il dedupe
        history-based evita catchup duplicati per lo stesso `scheduled_for`.
        """
        scheduled_for_iso = expected_last.isoformat()

        logger.info(
            f"[CATCHUP] schedule={schedule_id} recovering run scheduled for "
            f"{scheduled_for_iso} (now={now.isoformat()}, leader={self._leader_id})"
        )

        # FIRST: audit insert. Se fallisce, abort emit. Il missed run
        # verra' detectato di nuovo al prossimo tick e ri-tentato con
        # INSERT atomico — meglio un retry che un duplicato.
        try:
            _pg_execute(
                "INSERT INTO schedule_catchup_history "
                "(schedule_id, emitted_at, scheduled_for, leader_id) "
                "VALUES (%s, %s, %s, %s)",
                (schedule_id, now.isoformat(), scheduled_for_iso, self._leader_id),
            )
        except Exception as e:
            logger.error(
                f"[CRASH] [CATCHUP] Audit insert fallito per {schedule_id}: {e}. "
                f"Abort emit per evitare duplicati al prossimo tick. "
                f"Il missed run verra' detectato e ri-tentato.",
                exc_info=True,
            )
            return

        # SECOND: send_task (audit gia' registrato → dedupe history-based
        # opera correttamente al prossimo tick).
        # Se `send_task` fallisce dopo l'INSERT della history, rollbackiamo
        # la row: altrimenti il dedupe vedrebbe "gia' emesso" anche se il
        # task non e' mai entrato in coda.
        try:
            self._dispatch_schedule_task(
                SCHEDULE_PIPELINE_TASK_NAME,
                {
                    "schedule_id": schedule_id,
                    **scheduled_run_kwargs(
                        triggered_by="catchup",
                        scheduled_for=scheduled_for_iso,
                    ),
                },
            )
        except Exception as e_send:
            logger.error(
                f"[CRASH] [CATCHUP] send_task fallito dopo INSERT audit "
                f"per {schedule_id} (scheduled_for={scheduled_for_iso}): "
                f"{type(e_send).__name__}: {e_send}. Rollback row "
                f"history per consentire ri-emit al prossimo tick.",
                exc_info=True,
            )
            try:
                _pg_execute(
                    "DELETE FROM schedule_catchup_history "
                    "WHERE schedule_id = %s AND scheduled_for = %s "
                    "AND leader_id = %s",
                    (schedule_id, scheduled_for_iso, self._leader_id),
                )
            except Exception as e_rb:
                logger.error(
                    f"[CRASH] [CATCHUP] Rollback row history fallito "
                    f"per {schedule_id}: {type(e_rb).__name__}: {e_rb}. "
                    "Il run di catchup resta perso (history dice 'gia' "
                    "emesso' ma task non in coda).",
                    exc_info=True,
                )
            return

        # Aggiornare last_run all'emit significa: se il task fallisce
        # o crasha prima del completamento, al tick successivo
        # detect_missed_run non vede piu' il missed (dato che last_run
        # e' avanzato a expected_last) e il run e' perso. last_run
        # resta aggiornato dal task stesso quando completa con
        # successo (via ExecutionManager); il dedupe anti-duplicato
        # tra un tick e il successivo e' garantito da
        # _catchup_already_emitted_recently sopra, che legge la
        # history dell'audit.

    # Sincronizzazione DB → schedule entries

    def _sync_from_db(self) -> None:
        """Carica/aggiorna gli schedule attivi dal DB."""
        try:
            rows = _pg_fetchall(
                "SELECT id, name, cron_expression, project_id, is_enabled, "
                "last_run, config FROM schedules WHERE is_enabled = TRUE"
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

                # Celery crontab usa la timezone globale del beat. Se lo
                # schedule dichiara una timezone divergente, logghiamo un
                # warning al sync-time invece di lasciarlo invisibile.
                self._warn_if_timezone_divergent(sid, row.get("config"))

                last_run_at = self._parse_dt(row.get("last_run"))

                if entry_name not in self.data:
                    self.data[entry_name] = BeatEntry(
                        name=entry_name,
                        task=SCHEDULE_PIPELINE_TASK_NAME,
                        schedule=schedule,
                        kwargs={
                            "schedule_id": sid,
                            "triggered_by": "cron",
                            # Non cache-are `scheduled_for` dentro BeatEntry:
                            # il task lo determina al ricevimento, cosi'
                            # l'idempotency key CARICA cambia per ogni tick.
                        },
                        last_run_at=last_run_at or beat_now(),
                    )
                    logger.info(f"Schedule aggiunto: '{row.get('name', sid)}' cron={row['cron_expression']}")
                else:
                    # Aggiorna schedule se cambiato: ricrea l'entry preservando
                    # `last_run_at`, cosi' i cambi cron a caldo diventano
                    # effettivi senza riavviare il beat.
                    entry = self.data[entry_name]
                    if str(entry.schedule) != str(schedule):
                        logger.info(f"Schedule modificato: '{row.get('name', sid)}' cron={row['cron_expression']} (era {entry.schedule})")
                        self.data[entry_name] = BeatEntry(
                            name=entry_name,
                            task=SCHEDULE_PIPELINE_TASK_NAME,
                            schedule=schedule,
                            kwargs={
                                "schedule_id": sid,
                                "triggered_by": "cron",
                                # scheduled_for determinato a tick-time
                                # dal task wrapper (vedi nota sopra a
                                # riga ~515 sull'entry-creation path).
                            },
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

    def _warn_if_timezone_divergent(self, schedule_id: str, config: Any) -> None:
        """Loud warning se schedule.timezone diverge dalla beat tz
        (BEAT_TIMEZONE, default Europe/Rome).

        Celery emette secondo la timezone globale del beat. Questo helper
        rende esplicito il mismatch cosi' si puo' configurare il beat con la
        timezone corretta o normalizzare i cron alla timezone globale.
        """
        try:
            cfg = config if isinstance(config, dict) else (
                json.loads(config) if isinstance(config, str) else None
            )
            if not cfg:
                return
            schedule_section = cfg.get("schedule") or {}
            tz_name = schedule_section.get("timezone")
            if not tz_name:
                return
            beat_tz = str(beat_timezone())
            if tz_name != beat_tz:
                logger.warning(
                    "[Scheduler] schedule %s configurato con "
                    "timezone='%s' ma beat tz='%s' — il cron "
                    "verra' interpretato in '%s', NON in '%s'. "
                    "Allineare BEAT_TIMEZONE oppure normalizzare "
                    "l'espressione cron a '%s'.",
                    schedule_id, tz_name, beat_tz, beat_tz, tz_name, beat_tz,
                )
        except Exception as exc:
            logger.debug(
                "[Scheduler] timezone-divergence check fallito per %s: %s",
                schedule_id, exc,
            )

    def _parse_cron(self, cron_expr: str) -> Optional[object]:
        """Parsa espressione cron (5 campi) in un CronSchedule croniter."""
        try:
            return CronSchedule(cron_expr)
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

    # Heartbeat su DB

    def _write_heartbeat(self) -> None:
        """Scrive il timestamp heartbeat nel DB (usato dall'healthcheck)."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            _pg_execute(
                "INSERT INTO beat_heartbeat (id, ts) VALUES ('beat', %s) "
                "ON CONFLICT (id) DO UPDATE SET ts = EXCLUDED.ts",
                (now,)
            )
        except Exception as e:
            logger.warning(f"Errore scrittura heartbeat: {e}")

    # Monitoring task bloccate

    def _check_stuck_tasks(self) -> None:
        """Ogni 5 minuti verifica se ci sono lock scheduler scaduti o task bloccate."""
        now = time.time()
        if now - self._last_stuck_check < 300:
            return
        self._last_stuck_check = now

        try:
            # Lock con expires_at nel passato sono scaduti ma non puliti
            rows = _pg_fetchall(
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
                _pg_execute("DELETE FROM scheduler_locks WHERE expires_at IS NOT NULL AND expires_at < NOW()")
                logger.info(f"[STUCK] Rimossi {len(rows)} lock scaduti")

            # Controlla schedule con last_status = 'running' da più del hard limit
            try:
                hard_limit = int(app_config.settings.TASK_SCHEDULE_HARD_LIMIT)
            except (TypeError, ValueError):
                hard_limit = 3600
            if hard_limit <= 0:
                hard_limit = 3600
            # `INTERVAL '%s seconds'` con placeholder DENTRO il literal
            # SQL e' invalid PostgreSQL syntax. Usiamo `make_interval`
            # che accetta placeholder standard
            stuck = _pg_fetchall(
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

    # Cleanup output eseguibili orfani

    def _cleanup_eseguibili_outputs(self) -> None:
        """Ogni 30 minuti pulisce output locali e lease download scadute.

        Gli artifact remoti hanno una lifecycle S3; questo janitor copre i
        residui filesystem legacy e limita la crescita della tabella lease in
        caso di crash del processo durante un download.
        """
        now = _t.time()
        if now - self._last_eseguibili_cleanup < 1800:  # 30 minuti
            return
        self._last_eseguibili_cleanup = now

        try:
            ttl_seconds = 2 * 3600  # 2 ore
            removed = output_store.purge_expired_local_outputs(
                now_ts=now,
                ttl_seconds=ttl_seconds,
            )
            if removed:
                logger.info(f"Cleanup eseguibili_outputs: rimosse {removed} directory orfane")
        except Exception as e:
            logger.warning(f"Errore cleanup eseguibili_outputs: {e}")

        try:
            removed_claims = download_claim_store.purge_expired()
            if removed_claims:
                logger.info(
                    "Cleanup lease download eseguibili: rimosse %s righe scadute",
                    removed_claims,
                )
        except Exception as e:
            logger.warning(f"Errore cleanup lease download eseguibili: {e}")

    # Cleanup orfani DATA_DIR/temp/

    def _cleanup_orphan_temp_files(self) -> None:
        """Ogni 30 min rimuove file/dir orfani in DATA_DIR/temp/ piu'
        vecchi di TTL (default 24h, env TEMP_ORPHAN_CLEANUP_HOURS).

        Copre i pattern noti di temp naming:
          - plenora_cached_* / plenora_cached_* (results.result_reader materialization)
          - tmp* (NamedTemporaryFile/mkdtemp generici
                                                in file_service, artifacts, ecc.)
          - eseguibili_uploads/* (cross-worker upload staging,
                                                router.eseguibili)
          - temp generici: il cleanup resta tollerante via il prefix `tmp*`.

        La dir `eseguibili_uploads/` resta (container fisso della cache),
        i file dentro vengono puliti individualmente.

        Motivazione: senza questo, un task che crasha fra mkdtemp/open
        e il finally cleanup lascia residui accumulanti. Il volume temp
        per-worker e' effimero al restart del pod, ma in setup host-fisso
        (oggi `lab.plenora.it`) senza restart, il temp cresce indefinitamente.
        """
        now = _t.time()
        if now - self._last_temp_orphan_cleanup < 1800:  # 30 min
            return
        self._last_temp_orphan_cleanup = now

        try:
            settings = app_config.settings
            temp_dir = _Path(settings.DATA_DIR) / "temp"
            if not temp_dir.exists():
                return
            get_setting = getattr(settings, "get", None)
            if callable(get_setting):
                raw_ttl_hours = settings.get("TEMP_ORPHAN_CLEANUP_HOURS")
            else:
                # Tests patch app.config.settings with lightweight stubs that
                # only expose DATA_DIR. Preserve the historical env override
                # path for those stubs without weakening the real Settings
                # boundary used in production
                raw_ttl_hours = _os.environ.get("TEMP_ORPHAN_CLEANUP_HOURS", 24)
            ttl_hours = int(raw_ttl_hours)
            ttl_seconds = ttl_hours * 3600
            removed = 0

            for entry in temp_dir.iterdir():
                try:
                    # eseguibili_uploads e' cache permanente: scan ricorsivo
                    # dei file dentro, pulizia individuale per age
                    if entry.is_dir() and entry.name == "eseguibili_uploads":
                        for child in entry.iterdir():
                            try:
                                age = now - child.stat().st_mtime
                                if age > ttl_seconds:
                                    rmtree_safe(child, logger=logger)
                                    removed += 1
                            except Exception as e:
                                logger.warning(
                                    f"Cleanup temp: errore su {child}: {e}"
                                )
                        continue

                    age = now - entry.stat().st_mtime
                    if age > ttl_seconds:
                        rmtree_safe(entry, logger=logger)
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

    # Retention tabella plenora.logs

    def _cleanup_old_schedule_logs(self) -> None:
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
        now = _t.time()
        if now - self._last_schedule_logs_retention < 86400:  # 24h
            return
        self._last_schedule_logs_retention = now

        try:
            # Niente `or 90` per non collassare 0 → 90 (l'utente che imposta
            # esplicitamente 0 vuole disabilitare). Il fallback al default
            # avviene solo se l'attributo manca o non e' int
            raw = getattr(app_config.settings, "SCHEDULE_LOGS_RETENTION_DAYS", 90)
            try:
                days = int(raw)
            except (TypeError, ValueError):
                days = 90
            if days <= 0:
                return  # retention disabilitata
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()

            deleted = scheduler_log_service.ScheduleLogService().delete_logs_before(cutoff)
            if deleted:
                logger.info(
                    f"Retention schedule_logs: rimosse {deleted} righe "
                    f"piu' vecchie di {days}gg (cutoff={cutoff.isoformat()})"
                )
        except Exception as e:
            logger.warning(f"Errore retention schedule_logs: {e}")

    def _archive_old_logs(self) -> None:
        """Ogni 24h archivia+purga plenora.logs via LogArchiveWriter.

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
        now = _t.time()
        if now - self._last_logs_archive < 86400:  # 24h
            return

        settings = app_config.settings
        if not getattr(settings, "LOG_ARCHIVE_ENABLED", False):
            return
        try:
            days = int(getattr(settings, "LOG_RETENTION_DAYS", 30))
        except (TypeError, ValueError):
            days = 30
        if days <= 0:
            return  # retention disabilitata (0 o negativo

        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            storage = object_storage_module.get_log_archive_storage()
            writer = scheduler_log_archive.LogArchiveWriter(
                storage=storage,
                key_prefix=settings.LOG_ARCHIVE_KEY_PREFIX,
                batch_size=settings.LOG_ARCHIVE_BATCH_ROWS,
            )

            def select_rows(cutoff_ts: datetime, batch_size: int) -> object:
                # Schema-agnostic: search_path del pool risolve `logs` a
                # plenora.logs (stesso pattern di _cleanup_old_logs
                # ORDER BY stabile (ts, id) richiesto dal writer per non
                # saltare righe fra iterazioni successive
                return _pg_fetchall(
                    "SELECT * FROM logs WHERE ts < %s "
                    "ORDER BY ts ASC, id ASC LIMIT %s",
                    (cutoff_ts, batch_size),
                )

            def retention_bypass_execute(sql: str, params: object) -> None:
                # Una nuova tx per batch: SET LOCAL + DELETE sullo stesso
                # cursor (pattern _cleanup_old_logs), altrimenti il trigger
                # plenora.block_log_modifications scatta sulla DELETE
                with _pg_transaction() as conn:
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
            # Observability circolare su plenora.logs. INFO non passa
            # dal PostgresLogHandler (LOG_LEVEL_DB=WARNING default), quindi
            # usiamo una INSERT diretta con la stessa shape colonne
            # dell'handler canonico. Best-effort: un fallimento
            # observability non contamina lo stato "archive ok".
            try:
                obs_ts = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.%f"
                )[:-3]
                obs_details = json.dumps({
                    "table": "logs",
                    "cutoff": cutoff.isoformat(),
                    "archived_rows": result["archived_rows"],
                    "object_keys": list(result["object_keys"]),
                    "batch_runs": len(result["object_keys"]),
                }, default=str)
                _pg_execute(
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

    def _cleanup_old_logs(self) -> None:
        """Ogni 24h rimuove righe da plenora.logs piu' vecchie di LOG_RETENTION_DAYS.

        Senza retention la tabella cresce all'infinito. Il default (30 giorni)
        e' configurabile via settings.LOG_RETENTION_DAYS.
        """
        now = _t.time()
        if now - self._last_logs_retention < 86400:  # 24h
            return
        self._last_logs_retention = now

        try:
            settings = app_config.settings
            if getattr(settings, "LOG_ARCHIVE_ENABLED", False):
                return
            days = int(getattr(settings, "LOG_RETENTION_DAYS", 30) or 30)
            if days <= 0:
                return  # retention disabilitata
            # Niente schema hard-coded `plenora.`: il pool apre le
            # connessioni con `search_path=<_DB_SCHEMA>,public`, quindi
            # ci riferiamo alla tabella `logs` senza prefisso e PostgreSQL
            # la risolve nello schema corrente. Cosi' se DB_SCHEMA punta
            # a `plenora_staging`, la retention lavora sullo schema giusto
            before = _pg_fetchall(
                "SELECT COUNT(*) AS c FROM logs WHERE ts < now() - make_interval(days => %s)",
                (days,),
            )[0]["c"]
            if before:
                # SET LOCAL + DELETE devono stare nella stessa transazione
                # `execute` apre/committa una tx per call, azzerando SET LOCAL
                # Il flag consente a policy/trigger PG di distinguere il
                # cleanup schedulato dalle DELETE applicative
                with _pg_transaction() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SET LOCAL plenora.retention_cleanup = 'true'")
                        cur.execute(
                            "DELETE FROM logs WHERE ts < now() - make_interval(days => %s)",
                            (days,),
                        )
                logger.info(f"Retention logs: rimosse {before} righe piu' vecchie di {days}gg")
        except Exception as e:
            logger.warning(f"Errore retention logs: {e}")

    def _archive_old_audit_actions(self) -> None:
        """Ogni 24h archivia+purga plenora.audit_actions via LogArchiveWriter.

        Primo path di DELETE autorizzato su audit_actions: con
        LOG_ARCHIVE_ENABLED=False (default) la tabella continua a crescere
        come oggi; con ENABLED=True e AUDIT_RETENTION_DAYS>0 il writer
        archivia+purga.

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
        now = _t.time()
        if now - self._last_audit_archive < 86400:  # 24h
            return

        settings = app_config.settings
        if not getattr(settings, "LOG_ARCHIVE_ENABLED", False):
            return
        try:
            days = int(getattr(settings, "AUDIT_RETENTION_DAYS", 365))
        except (TypeError, ValueError):
            days = 365
        if days <= 0:
            return  # retention disabilitata (0 o negativo

        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            storage = object_storage_module.get_log_archive_storage()
            writer = scheduler_log_archive.LogArchiveWriter(
                storage=storage,
                key_prefix=settings.LOG_ARCHIVE_KEY_PREFIX,
                batch_size=settings.LOG_ARCHIVE_BATCH_ROWS,
            )

            def select_rows(cutoff_ts: datetime, batch_size: int) -> object:
                # Schema-agnostic: search_path del pool risolve
                # `audit_actions` a plenora.audit_actions. Colonna
                # temporale = `timestamp`.
                return _pg_fetchall(
                    "SELECT * FROM audit_actions WHERE timestamp < %s "
                    "ORDER BY timestamp ASC, id ASC LIMIT %s",
                    (cutoff_ts, batch_size),
                )

            def retention_bypass_execute(sql: str, params: object) -> None:
                # Una nuova tx per batch: SET LOCAL + DELETE sullo stesso
                # cursor (pattern _cleanup_old_logs / _archive_old_logs
                # altrimenti il trigger plenora.block_log_modifications
                # scatta sulla DELETE
                with _pg_transaction() as conn:
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
            # Observability circolare su plenora.logs (tabella logs
            # non audit_actions: la chiusura del cerchio log-del-log
            # va sempre su plenora.logs). Shape identica al handler canonico.
            try:
                obs_ts = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.%f"
                )[:-3]
                obs_details = json.dumps({
                    "table": "audit_actions",
                    "cutoff": cutoff.isoformat(),
                    "archived_rows": result["archived_rows"],
                    "object_keys": list(result["object_keys"]),
                    "batch_runs": len(result["object_keys"]),
                }, default=str)
                _pg_execute(
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

    # Storage reconciliation —

    def _reconcile_storage(self) -> None:
        """Beat-driven reconciler per `plenora.storage_reconciliation_log`.

        Cadence-gated da `RECONCILIATION_BEAT_INTERVAL_MIN` (default 15
        minuti). Supporta solo `operation="delete"`; upload-intent,
        storage-side scan e intermediate artifact pair restano fuori scope.

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
        now_ts = _t.time()

        try:
            settings = app_config.settings
        except Exception:
            return  # config non disponibile: skip silente

        # Tutti i tunables via Settings.get; fallback a default difensivi se i
        # test usano un SimpleNamespace stub.
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
            rows = storage_reconciliation_module.fetch_due_pending_deletes(
                limit=batch_size
            )
        except Exception as e:
            logger.warning(
                f"Reconcile storage: fetch fallito: {e}", exc_info=True
            )
            # Continue to the operational guardrails section: summary
            # + prune are independent of the fetch result. A fetch
            # failure must NOT silence the backlog visibility — that
            # is precisely the scenario where ops needs to see counts
            rows = []

        ok_count = 0
        failed_count = 0
        lost_count = 0
        for row in rows:
            try:
                # Stream supporta solo "delete". `fetch_due_pending_deletes`
                # gia' filtra a SQL-level; questa e' una belt-and-suspenders
                if row.operation != "delete":
                    continue

                outcome = storage_compensation.compensating_delete(
                    row.storage_key,
                    domain=row.domain,
                    reason="reconciler_retry",
                    extras={"reconciliation_id": row.id},
                )

                if outcome == "ok":
                    storage_reconciliation_module.mark_orphan_deleted(row.id)
                    ok_count += 1
                else:
                    # outcome == "failed" or "skipped": object not confirmed
                    # deleted, keep the row retryable instead of terminal.
                    new_attempts = row.attempts + 1
                    if new_attempts >= max_attempts:
                        storage_reconciliation_module.mark_lost(
                            row.id,
                            error=f"max_attempts_exceeded ({new_attempts})",
                        )
                        lost_count += 1
                    else:
                        next_retry = storage_reconciliation_module.compute_next_retry_at(
                            new_attempts,
                            base_seconds=backoff_base,
                        )
                        storage_reconciliation_module.mark_failed_attempt(
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

        # second branch of the reconciler — chiude R-021.1
        # Processa righe `operation='upload_intent'` stantie (older than
        # grace window). Per ciascuna
        # se `is_storage_key_referenced(storage_key)` is True →
        # caller's DB save committed → `mark_done`
        # se False → orphan candidate → `compensating_delete` +
        # `mark_orphan_deleted`
        # se None → read-error → `mark_failed_attempt` (skip questa
        # row, riprova al prossimo tick); promuovi a `lost` dopo
        # max_attempts
        # Cap <= 0 su grace o il branch (escape hatch
        # ops; il delete branch sopra resta attivo
        ui_grace_s = _read_int("STORAGE_RECONCILE_UPLOAD_INTENT_GRACE_SECONDS", 600)
        ui_batch_size = _read_int("STORAGE_RECONCILE_UPLOAD_INTENT_BATCH_SIZE", 50)
        if ui_grace_s > 0 and ui_batch_size > 0:
            try:
                ui_rows = storage_reconciliation_module.fetch_due_stale_upload_intents(
                    limit=ui_batch_size,
                    grace_seconds=ui_grace_s,
                )
            except Exception as e:
                logger.warning(
                    f"Reconcile storage: upload_intent fetch fallito: {e}",
                    exc_info=True,
                )
                ui_rows = []

            ui_done_count = 0
            ui_orphan_count = 0
            ui_failed_count = 0
            ui_lost_count = 0
            for row in ui_rows:
                try:
                    if row.operation != "upload_intent":
                        # belt-and-suspenders: SQL gia' filtra
                        continue

                    referenced = storage_reconciliation_module.is_storage_key_referenced(
                        row.storage_key,
                        domain=row.domain,
                    )
                    if referenced is True:
                        # caller's DB save committed -> intent satisfied
                        storage_reconciliation_module.mark_done(row.id)
                        ui_done_count += 1
                        continue
                    if referenced is None:
                        # read-error: skip delete (data-loss risk
                        # Increment attempts + backoff -> retry next tick
                        new_attempts = row.attempts + 1
                        if new_attempts >= max_attempts:
                            storage_reconciliation_module.mark_lost(
                                row.id,
                                error=(
                                    "upload_intent_ref_check_failed "
                                    f"max_attempts ({new_attempts})"
                                ),
                            )
                            ui_lost_count += 1
                        else:
                            next_retry = storage_reconciliation_module.compute_next_retry_at(
                                new_attempts,
                                base_seconds=backoff_base,
                            )
                            storage_reconciliation_module.mark_failed_attempt(
                                row.id,
                                error="upload_intent_ref_check_failed",
                                next_retry_at=next_retry,
                            )
                            ui_failed_count += 1
                        continue

                    # referenced is False -> orphan candidate
                    outcome = storage_compensation.compensating_delete(
                        row.storage_key,
                        domain=row.domain,
                        reason="upload_intent_orphan",
                        extras={"reconciliation_id": row.id},
                    )
                    if outcome == "ok":
                        storage_reconciliation_module.mark_orphan_deleted(row.id)
                        ui_orphan_count += 1
                    else:
                        new_attempts = row.attempts + 1
                        if new_attempts >= max_attempts:
                            storage_reconciliation_module.mark_lost(
                                row.id,
                                error=f"upload_intent_max_attempts ({new_attempts})",
                            )
                            ui_lost_count += 1
                        else:
                            next_retry = storage_reconciliation_module.compute_next_retry_at(
                                new_attempts,
                                base_seconds=backoff_base,
                            )
                            storage_reconciliation_module.mark_failed_attempt(
                                row.id,
                                error="upload_intent_compensating_delete_failed",
                                next_retry_at=next_retry,
                            )
                            ui_failed_count += 1
                except Exception as e:
                    logger.warning(
                        f"Reconcile storage: errore upload_intent id={row.id}: {e}",
                        exc_info=True,
                    )

            if ui_done_count or ui_orphan_count or ui_failed_count or ui_lost_count:
                logger.info(
                    "event=storage_reconciliation_upload_intent_tick "
                    "done=%d orphan=%d failed=%d lost=%d batch=%d",
                    ui_done_count, ui_orphan_count, ui_failed_count,
                    ui_lost_count, len(ui_rows),
                )

        # Eseguiti DOPO la passa di reconcile per non interferire con il
        # set di righe lette da `fetch_due_pending_deletes`. Entrambi
        # best-effort: errori loggati, il tick non crasha
        try:
            counts = storage_reconciliation_module.count_active_by_state_domain()
            if counts:
                # Aggregato totale per livello (utile per alerting su
                # threshold) + per-row breakdown per dashboard manuale
                pending_total = sum(c.count for c in counts if c.state == "pending")
                lost_total = sum(c.count for c in counts if c.state == "lost")
                breakdown = " ".join(
                    f"{c.state}.{c.domain}={c.count}" for c in counts
                )
                logger.info(
                    "event=storage_reconciliation_summary "
                    "pending_total=%d lost_total=%d %s",
                    pending_total, lost_total, breakdown,
                )
        except Exception as e:
            logger.warning(
                f"Reconcile storage: summary count fallito: {e}",
                exc_info=True,
            )

        retention_days = _read_int(
            "STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS", 30,
        )
        prune_batch = _read_int(
            "STORAGE_RECONCILE_PRUNE_BATCH_SIZE", 500,
        )
        if retention_days > 0 and prune_batch > 0:
            try:
                cutoff = _dt.now(_tz.utc) - _td(days=retention_days)
                pruned = storage_reconciliation_module.prune_terminal_older_than(
                    older_than=cutoff,
                    limit=prune_batch,
                )
                if pruned:
                    logger.info(
                        "event=storage_reconciliation_prune_tick "
                        "pruned=%d cutoff=%s retention_days=%d limit=%d",
                        pruned, cutoff.isoformat(), retention_days,
                        prune_batch,
                    )
            except Exception as e:
                logger.warning(
                    f"Reconcile storage: prune fallito: {e}", exc_info=True,
                )

    # Persistenza su DB

    def _cleanup_task_results(self) -> None:
        """Sweep TTL della tabella task_results.

        Rimpiazza la scadenza automatica che Redis faceva via
        TASK_RESULT_EXPIRES: cancella le righe di stato/esito task con
        expires_at scaduto. Throttle 1h: e' una semplice DELETE indicizzata
        su expires_at, non serve a ogni tick."""
        now = _t.time()
        if now - self._last_task_results_cleanup < 3600:  # 1h
            return
        self._last_task_results_cleanup = now
        try:
            from app.core.orchestrator.task_result_store import (
                default_task_result_store,
            )

            deleted = default_task_result_store().cleanup_expired()
            if deleted:
                logger.info(f"[CLEANUP] task_results scaduti rimossi: {deleted}")

            # Preview rows/columns live on the runtime object storage, not in
            # task_results JSONB. Sweep by age as a provider-independent
            # fallback to bucket lifecycle rules.
            from app.core.orchestrator.preview_payload_store import (
                cleanup_expired_preview_payloads,
            )

            preview_deleted = cleanup_expired_preview_payloads(
                ttl_seconds=int(app_config.settings.TASK_RESULT_EXPIRES)
            )
            if preview_deleted:
                logger.info(
                    "[CLEANUP] preview object scaduti rimossi: %s",
                    preview_deleted,
                )
        except Exception as e:
            logger.warning(f"[CLEANUP] sweep task_results fallito: {e}")

    def setup_schedule(self) -> None:
        """Inizializza schedule vuoto — verrà popolato dal DB al primo tick.

        Dopo il sync DB, merge anche le entry config-based. DB-driven
        schedules vivono nel namespace
        `urby:schedule:<id>`; le config entry usano nomi qualunque
        ALTRI da quel prefix per coesistere senza collisioni.
        """
        self.data = {}
        self._sync_from_db()
        self._merge_config_beat_schedule()
        logger.info(f"Beat avviato con {len(self.data)} schedule attivi")

    def _merge_config_beat_schedule(self) -> None:
        """Merge le entry config-based (NON DB) in `self.data`.

        La sorgente e' `beat_schedule_config()` (celery-free,
        app.core.scheduler.beat_schedule_config). Gli schedule DB-driven
        (namespace `urby:schedule:<id>`) restano di `_sync_from_db`; le
        entry config (qualunque altro nome) sopravvivono ai sync DB
        successivi perche' il cleanup loop filtra solo per quel prefix.

        Idempotente; best-effort per-entry.
        """
        try:
            config_schedule = beat_schedule_config() or {}
        except Exception as e:
            logger.warning(f"[CONFIG-BEAT] Errore lettura beat_schedule: {e}")
            return

        if not config_schedule:
            return

        for name, entry in config_schedule.items():
            if not isinstance(name, str):
                continue
            if name.startswith("urby:schedule:"):
                # Riservato a `_sync_from_db` — un'eventuale collisione
                # tra config + DB favorisce il DB (sorgente di verita'
                continue
            if name in self.data:
                # Idempotente: gia' caricato
                continue
            try:
                if not isinstance(entry, dict):
                    continue
                task = entry.get("task")
                schedule = entry.get("schedule")
                if not task or schedule is None:
                    logger.warning(
                        f"[CONFIG-BEAT] Entry {name!r} senza task/schedule, skip"
                    )
                    continue
                self.data[name] = BeatEntry(
                    name=name,
                    task=task,
                    schedule=schedule,
                    args=tuple(entry.get("args") or ()),
                    kwargs=dict(entry.get("kwargs") or {}),
                    options=dict(entry.get("options") or {}),
                    last_run_at=beat_now(),
                )
                logger.info(
                    f"[CONFIG-BEAT] Entry caricata: {name!r} task={task!r}"
                )
            except Exception as e:
                logger.warning(
                    f"[CONFIG-BEAT] Errore caricamento entry {name!r}: {e}",
                    exc_info=True,
                )

    def close(self) -> None:
        """Cleanup alla chiusura.

        Rilascia il lock leader su PostgreSQL se e' ancora posseduto da
        questo processo: cosi' un altro beat puo' prenderlo subito invece
        di aspettare il TTL di 120s. La release e' "safe" (DELETE filtrato
        per owner_token nello store): un beat che ha gia' perso la
        leadership non cancella il lock di un altro durante un failover."""
        if self._is_leader:
            try:
                released = self._leader_store.release_beat_leader(
                    owner_token=self._leader_id
                )
                if released:
                    logger.info(f"[LEADER] Rilasciato su close: {self._leader_id}")
                else:
                    logger.info(
                        f"[LEADER] Skip release su close: il lock non e' piu' "
                        f"mio (io={self._leader_id})"
                    )
            except Exception as e:
                logger.warning(f"[LEADER] Errore release su close: {e}")
        logger.info("PlenoraBeatScheduler chiuso")
