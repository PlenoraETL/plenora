"""
Servizio CRUD per gli schedule PLENORA-ETL.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.config import settings
from app.utils.logger import get_logger


# FUNZIONE PURA — detect_missed_run (C3 fix 3)
#
# Estratta come funzione a livello-modulo (non metodo di ScheduleService)
# per essere banalmente testabile e riusabile dal beat scheduler senza
# dover istanziare un service completo. Zero side-effect, solo logica.


def detect_missed_run(
    cron_expression: str,
    last_run: Optional[datetime],
    now: datetime,
    grace_hours: int = 24,
) -> Optional[datetime]:
    """Rileva se c'e' un run mancante da recuperare per uno schedule.

    Args:
        cron_expression: espressione cron standard a 5 campi.
        last_run: timestamp dell'ultima esecuzione riuscita, o None
            se lo schedule non e' mai stato eseguito.
        now: istante corrente (deve essere tz-aware).
        grace_hours: finestra massima per il recupero. Oltre questa,
            il missed e' considerato "troppo vecchio" e scartato
            (evita di recuperare run obsoleti dopo outage lunghi).

    Returns:
        Il timestamp dell'`expected_last` cron tick da recuperare,
        oppure None se non c'e' niente da recuperare:
        - last_run is None (primo run di sempre)
        - last_run >= expected_last (nessun tick perso)
        - expected_last piu' vecchio di grace_hours (troppo obsoleto)
        - cron expression invalida (graceful fallback)
    """
    if last_run is None:
        return None

    try:
        from croniter import croniter
    except ImportError:
        return None

    try:
        cron = croniter(cron_expression, now)
        expected_last = cron.get_prev(datetime)
    except Exception:
        return None

    if last_run >= expected_last:
        return None

    # Grace window check: se l'expected_last e' troppo vecchio, skip.
    if (now - expected_last).total_seconds() > grace_hours * 3600:
        return None

    return expected_last
from app.core.scheduler.models import (
    ScheduleConfig,
    ScheduleCreate,
    ScheduleUpdate,
    ScheduleList,
    SchedulePipeline,
    ScheduleFrequency,
    ScheduleExecution,
    ScheduleDestination,
    ScheduleConcurrency,
    SchedulePreCheck,
    ScheduleErrorPolicy,
    ScheduleConflictPolicy,
    ScheduleRuntime,
)
import app.db.db_service as db

logger = get_logger(__name__)


class ScheduleService:
    """
    Servizio CRUD per schedule PLENORA-ETL.
    Storage su PostgreSQL tramite db_service.
    RedBeat rimosso — il beat scheduler custom legge dal DB.
    """

    DEFAULT_GROUP = settings.DEFAULT_GROUP

    # LOCK PER-SCHEDULE — Race condition guard
    # mark_as_orphan, set_runtime_status e update_runtime fanno tutti
    # read-modify-write sul blob `config` (lettura piena, modifica in memoria,
    # write piena). Senza lock, due thread che operano sullo stesso schedule
    # possono perdere update (es. UI toggle disable mentre Celery fa update_runtime
    # → la modifica dello UI viene sovrascritta).
    #
    # I lock sono CLASS-LEVEL: condivisi fra tutte le istanze di ScheduleService
    # nello stesso processo. Cross-process (es. multi-worker Celery) NON copre,
    # ma copre il caso comune FastAPI threads + un worker Celery.
    #
    # RLock per permettere call annidate dallo stesso thread (es. mark_as_orphan
    # chiamato da check_orphans).
    _schedule_locks_lock = threading.Lock()
    _schedule_locks: Dict[str, threading.RLock] = {}

    @classmethod
    def _get_schedule_lock(cls, schedule_id: str) -> threading.RLock:
        """Restituisce (creando se necessario) il lock per uno schedule_id.

        Pattern double-checked locking sul lock dei lock.
        """
        with cls._schedule_locks_lock:
            lock = cls._schedule_locks.get(schedule_id)
            if lock is None:
                lock = threading.RLock()
                cls._schedule_locks[schedule_id] = lock
        return lock

    def _generate_schedule_id(self) -> str:
        short_uuid = str(uuid.uuid4())[:12]
        return f"schedule_{short_uuid}"

    def _row_to_config(self, row: dict) -> Optional[ScheduleConfig]:
        """Converte riga DB in ScheduleConfig."""
        if not row:
            return None
        try:
            config_data = row.get("config") or {}
            if isinstance(config_data, str):
                config_data = json.loads(config_data)
            return ScheduleConfig.model_validate(config_data)
        except Exception as e:
            logger.error(f"[GESTITO] Errore parsing schedule {row.get('id')}: {e}", exc_info=True)
            return None

    def _config_to_db(self, config: ScheduleConfig, group_id: str) -> dict:
        """Prepara i dati per il salvataggio nel DB."""
        return {
            "schedule_id": config.id,
            "group_id": group_id,
            "project_id": config.pipeline.project_id,
            "name": config.pipeline.project_name or config.id,
            "cron_expression": config.schedule.cron_expression,
            "is_enabled": config.schedule.enabled,
            "config": config.model_dump(mode='json'),
        }

    # LETTURA

    def get_schedule(self, schedule_id: str) -> Optional[ScheduleConfig]:
        """Carica un singolo schedule per ID."""
        row = db.get_schedule(schedule_id)
        return self._row_to_config(row)

    def list_schedules(self, project_id: Optional[str] = None,
                       group_id: str = None) -> ScheduleList:
        """Lista tutti gli schedule del gruppo, ordinati per data."""
        group_id = group_id or self.DEFAULT_GROUP
        rows = db.list_schedules(group_id)
        schedules = []
        for row in rows:
            config = self._row_to_config(row)
            if not config:
                continue
            if project_id and config.pipeline.project_id != project_id:
                continue
            schedules.append(config)

        schedules.sort(key=lambda x: x.updated_at, reverse=True)
        return ScheduleList(schedules=schedules, total=len(schedules))

    def schedule_exists(self, schedule_id: str) -> bool:
        return db.get_schedule(schedule_id) is not None

    # CREAZIONE

    def create_schedule(self, schedule_data: ScheduleCreate,
                        project_name: str = "", project_type: str = "estrai",
                        group_id: str = None,
                        created_by: str = None) -> ScheduleConfig:
        """Crea un nuovo schedule dai dati del wizard (6 step)."""
        group_id = group_id or self.DEFAULT_GROUP
        schedule_id = self._generate_schedule_id()
        now = datetime.now(timezone.utc)

        config = ScheduleConfig(
            id=schedule_id,
            created_at=now,
            updated_at=now,
            pipeline=SchedulePipeline(
                project_id=schedule_data.project_id,
                project_name=project_name,
                project_type=project_type
            ),
            schedule=ScheduleFrequency(
                cron_expression=schedule_data.cron_expression,
                timezone=schedule_data.timezone,
                description=schedule_data.description,
                enabled=True
            ),
            execution=ScheduleExecution(
                use_cache=schedule_data.use_cache
            ),
            destination=schedule_data.destination,
            on_error=ScheduleErrorPolicy(
                max_retries=schedule_data.max_retries,
                retry_backoff_seconds=schedule_data.retry_backoff_seconds
            ),
            conflict_policy=ScheduleConflictPolicy(
                append_conflicts=schedule_data.append_conflicts,
                log_skipped_rows=schedule_data.log_skipped_rows
            ),
            concurrency=ScheduleConcurrency(
                max_queue_size=schedule_data.concurrency_max_queue
            ),
            pre_check=SchedulePreCheck(
                verify_destination_connection=schedule_data.destination is not None
            ),
            runtime=ScheduleRuntime(status="idle")
        )

        # Calcola prossima esecuzione
        next_exec = self._calculate_next_execution(
            config.schedule.cron_expression,
            config.schedule.timezone
        )
        if next_exec:
            config.runtime.next_execution = next_exec

        self._save_to_db(config, group_id, created_by=created_by)
        logger.info(f"Schedule creato: {schedule_id} -> progetto {schedule_data.project_id}")
        return config

    # MODIFICA

    def update_schedule(self, schedule_id: str,
                        update_data: ScheduleUpdate) -> Optional[ScheduleConfig]:
        """Aggiorna uno schedule esistente."""
        config = self.get_schedule(schedule_id)
        if not config:
            return None

        if update_data.project_id is not None:
            config.pipeline.project_id = update_data.project_id
        if update_data.cron_expression is not None:
            config.schedule.cron_expression = update_data.cron_expression
        if update_data.timezone is not None:
            config.schedule.timezone = update_data.timezone
        if update_data.description is not None:
            config.schedule.description = update_data.description
        if update_data.use_cache is not None:
            config.execution.use_cache = update_data.use_cache
        if update_data.clear_destination:
            config.destination = None
            config.pre_check.verify_destination_connection = False
        elif update_data.destination is not None:
            config.destination = update_data.destination
            config.pre_check.verify_destination_connection = True
        if update_data.max_retries is not None:
            config.on_error.max_retries = update_data.max_retries
        if update_data.retry_backoff_seconds is not None:
            config.on_error.retry_backoff_seconds = update_data.retry_backoff_seconds
        if update_data.append_conflicts is not None:
            config.conflict_policy.append_conflicts = update_data.append_conflicts
        if update_data.log_skipped_rows is not None:
            config.conflict_policy.log_skipped_rows = update_data.log_skipped_rows
        if update_data.concurrency_max_queue is not None:
            config.concurrency.max_queue_size = update_data.concurrency_max_queue

        config.updated_at = datetime.now(timezone.utc)

        next_exec = self._calculate_next_execution(
            config.schedule.cron_expression,
            config.schedule.timezone
        )
        if next_exec:
            config.runtime.next_execution = next_exec

        # Aggiorna cron e is_enabled nel DB oltre al config JSON
        db.update_schedule(schedule_id,
                           cron_expression=config.schedule.cron_expression,
                           is_enabled=config.schedule.enabled,
                           config=config.model_dump(mode='json'))
        logger.info(f"Schedule aggiornato: {schedule_id}")
        return config

    # TOGGLE ENABLE/DISABLE

    def update_catchup_config(
        self,
        schedule_id: str,
        enabled: bool,
        grace_hours: int = 24,
    ) -> Optional[ScheduleConfig]:
        """Aggiorna il catchup config di uno schedule (C3 fix 3).

        Ritorna None se lo schedule non esiste.
        """
        config = self.get_schedule(schedule_id)
        if not config:
            return None

        config.schedule.catchup_missed_runs = enabled
        config.schedule.catchup_grace_hours = grace_hours
        config.updated_at = datetime.now(timezone.utc)

        db.update_schedule(schedule_id, config=config.model_dump(mode='json'))
        logger.info(
            f"Schedule {schedule_id} catchup aggiornato: "
            f"enabled={enabled}, grace_hours={grace_hours}"
        )
        return config

    def toggle_schedule(self, schedule_id: str, enabled: bool) -> Optional[ScheduleConfig]:
        """Abilita o disabilita uno schedule."""
        config = self.get_schedule(schedule_id)
        if not config:
            return None

        if enabled and config.runtime.status == "orphan":
            logger.warning(f"Impossibile abilitare schedule orfano {schedule_id}")
            return None

        config.schedule.enabled = enabled
        config.runtime.status = "idle" if enabled else "disabled"
        config.updated_at = datetime.now(timezone.utc)

        if enabled:
            next_exec = self._calculate_next_execution(
                config.schedule.cron_expression,
                config.schedule.timezone
            )
            if next_exec:
                config.runtime.next_execution = next_exec
        else:
            config.runtime.next_execution = None

        db.update_schedule(schedule_id,
                           is_enabled=enabled,
                           config=config.model_dump(mode='json'))
        logger.info(f"Schedule {'abilitato' if enabled else 'disabilitato'}: {schedule_id}")
        return config

    # ELIMINAZIONE

    def delete_schedule(self, schedule_id: str) -> bool:
        """Elimina uno schedule e i suoi log."""
        if not db.get_schedule(schedule_id):
            return False

        db.delete_schedule(schedule_id)

        try:
            from app.core.scheduler.log_service import ScheduleLogService
            log_service = ScheduleLogService()
            deleted_logs = log_service.delete_schedule_logs(schedule_id)
            logger.info(f"Schedule eliminato: {schedule_id} ({deleted_logs} log rimossi)")
        except Exception as e:
            logger.warning(f"Schedule eliminato ma errore pulizia log: {e}")

        # Cleanup lock in-memory per evitare accumulo di lock orfani
        with self._schedule_locks_lock:
            self._schedule_locks.pop(schedule_id, None)

        return True

    # GESTIONE ORFANI

    def mark_as_orphan(self, schedule_id: str) -> Optional[ScheduleConfig]:
        """Segna uno schedule come orfano (progetto eliminato).

        Read-modify-write protetto da lock per-schedule contro race con
        update_runtime / set_runtime_status / toggle_schedule.
        """
        with self._get_schedule_lock(schedule_id):
            config = self.get_schedule(schedule_id)
            if not config:
                return None

            config.schedule.enabled = False
            config.runtime.status = "orphan"
            config.runtime.next_execution = None
            config.updated_at = datetime.now(timezone.utc)

            db.update_schedule(schedule_id,
                               is_enabled=False,
                               config=config.model_dump(mode='json'))
            logger.warning(f"Schedule orfano: {schedule_id}")
            return config

    def check_orphans(self) -> List[str]:
        """Verifica tutti gli schedule e segna come orfani quelli con progetto eliminato.

        Fix N+1: era un loop con project_exists() per ogni schedule. Ora fa una
        sola query per avere tutti i project_id esistenti.
        """
        import app.db.db_service as _db

        orphaned = []
        schedule_list = self.list_schedules()

        # Fetch tutti gli ID progetto esistenti in un set
        existing_project_ids = {
            row["id"] for row in _db.fetchall("SELECT id FROM projects")
        }

        for schedule in schedule_list.schedules:
            if schedule.runtime.status == "orphan":
                continue
            if schedule.pipeline.project_id not in existing_project_ids:
                self.mark_as_orphan(schedule.id)
                orphaned.append(schedule.id)

        if orphaned:
            logger.warning(f"Trovati {len(orphaned)} schedule orfani: {orphaned}")
        return orphaned

    def get_schedules_for_project(self, project_id: str) -> List[ScheduleConfig]:
        """Lista tutti gli schedule di un progetto specifico."""
        return self.list_schedules(project_id=project_id).schedules

    # AGGIORNAMENTO RUNTIME

    def set_runtime_status(self, schedule_id: str, status: str,
                           current_task_id: str = None) -> Optional[ScheduleConfig]:
        """Aggiorna solo lo stato runtime (transizioni leggere).

        Read-modify-write protetto da lock per-schedule.
        """
        with self._get_schedule_lock(schedule_id):
            config = self.get_schedule(schedule_id)
            if not config:
                return None

            config.runtime.status = status
            if current_task_id is not None:
                config.runtime.current_task_id = current_task_id
            config.updated_at = datetime.now(timezone.utc)

            db.update_schedule(schedule_id, config=config.model_dump(mode='json'))
            return config

    def update_runtime(self, schedule_id: str, status: str,
                       last_execution: Optional[dict] = None) -> Optional[ScheduleConfig]:
        """Aggiorna lo stato runtime dopo un'esecuzione completata.

        Read-modify-write protetto da lock per-schedule contro race su
        counter (success_count, failure_count, consecutive_failures) e
        next_execution.
        """
        with self._get_schedule_lock(schedule_id):
            config = self.get_schedule(schedule_id)
            if not config:
                return None

            config.runtime.status = status
            config.runtime.current_task_id = None

            if last_execution:
                from app.core.scheduler.models import LastExecution
                config.runtime.last_execution = LastExecution(**last_execution)

            config.runtime.total_runs += 1
            if status == "completed":
                config.runtime.success_count += 1
                config.runtime.consecutive_failures = 0
            elif status == "partial":
                config.runtime.success_count += 1
                config.runtime.consecutive_failures = 0
            elif status == "failed":
                config.runtime.failure_count += 1
                config.runtime.consecutive_failures += 1

            if config.schedule.enabled:
                next_exec = self._calculate_next_execution(
                    config.schedule.cron_expression,
                    config.schedule.timezone
                )
                if next_exec:
                    config.runtime.next_execution = next_exec

            config.updated_at = datetime.now(timezone.utc)
            db.update_schedule(schedule_id, config=config.model_dump(mode='json'))
            return config

    # HELPER INTERNI

    def _save_to_db(self, config: ScheduleConfig, group_id: str, created_by: str = None) -> None:
        """Salva config schedule nel DB."""
        db.save_schedule(
            schedule_id=config.id,
            group_id=group_id,
            project_id=config.pipeline.project_id,
            name=config.pipeline.project_name or config.id,
            cron_expression=config.schedule.cron_expression,
            is_enabled=config.schedule.enabled,
            config=config.model_dump(mode='json'),
            created_by=created_by,
        )

    def _calculate_next_execution(self, cron_expression: str,
                                  timezone: str) -> Optional[datetime]:
        """Calcola la prossima esecuzione dalla cron expression."""
        try:
            from croniter import croniter
            import pytz
            tz = pytz.timezone(timezone)
            now = datetime.now(tz)
            cron = croniter(cron_expression, now)
            return cron.get_next(datetime)
        except ImportError:
            logger.warning("croniter non installato — next_execution non calcolata")
            return None
        except Exception as e:
            logger.error(f"[CRASH] Errore calcolo next_execution: {e}", exc_info=True)
            return None
