"""
Servizio CRUD per gli schedule PLENORA-ETL.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Callable, List, Mapping, Optional

from croniter import croniter
import pytz

from app.config import settings
from app.core.scheduler import db
from app.core.scheduler.log_service import ScheduleLogService
from app.core.scheduler.models import (
    LastExecution,
    ScheduleConcurrency,
    ScheduleConfig,
    ScheduleConflictPolicy,
    ScheduleCreate,
    ScheduleErrorPolicy,
    ScheduleExecution,
    ScheduleFrequency,
    ScheduleList,
    SchedulePipeline,
    SchedulePreCheck,
    ScheduleRuntime,
    ScheduleUpdate,
)
from app.core.scheduler.runtime_status import RuntimeStatus, coerce_runtime_status
from app.runtime.services import build_project_service
from app.shared import error_codes
from fastapi import HTTPException
from app.utils.logger import get_logger


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

    # Allinea entrambi a UTC-aware prima del confronto: il DB puo' fornire
    # datetime naive da colonne TIMESTAMP WITHOUT TIME ZONE.
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)

    try:
        cron = croniter(cron_expression, now)
        expected_last = cron.get_prev(datetime)
    except Exception:
        return None

    if expected_last.tzinfo is None:
        expected_last = expected_last.replace(tzinfo=timezone.utc)

    if last_run >= expected_last:
        return None

    # Grace window check: se l'expected_last e' troppo vecchio, skip
    if (now - expected_last).total_seconds() > grace_hours * 3600:
        return None

    return expected_last

logger = get_logger(__name__)


class ScheduleService:
    """
    Servizio CRUD per schedule PLENORA-ETL.
    Storage su PostgreSQL tramite il leaf DB schedule.
    RedBeat rimosso — il beat scheduler custom legge dal DB.
    """

    DEFAULT_GROUP = settings.DEFAULT_GROUP

    def __init__(
        self,
        *,
        project_service_factory: Callable[[], object] = build_project_service,
    ) -> None:
        self._project_service_factory = project_service_factory

    def _generate_schedule_id(self) -> str:
        short_uuid = str(uuid.uuid4())[:12]
        return f"schedule_{short_uuid}"

    def _row_to_config(self, row: Optional[db.ScheduleRow]) -> Optional[ScheduleConfig]:
        """Converte riga DB in ScheduleConfig."""
        if not row:
            return None
        try:
            config_data = row.config or {}
            if isinstance(config_data, str):
                config_data = json.loads(config_data)
            return ScheduleConfig.model_validate(config_data)
        except Exception as e:
            logger.error(f"[GESTITO] Errore parsing schedule {row.id}: {e}", exc_info=True)
            return None

    def get_schedule(self, schedule_id: str) -> Optional[ScheduleConfig]:
        """Carica un singolo schedule per ID."""
        row = db.get_schedule_row(schedule_id)
        return self._row_to_config(row)

    def _update_config_locked(
        self,
        schedule_id: str,
        mutate_config: Callable[[ScheduleConfig], bool],
        fields_for_config: Optional[Callable[[ScheduleConfig], Mapping[str, object]]] = None,
    ) -> Optional[ScheduleConfig]:
        """Apply a schedule config mutation under the DB row lock."""
        updated_config: Optional[ScheduleConfig] = None

        def _locked_update(row: db.ScheduleRow) -> Optional[Mapping[str, object]]:
            nonlocal updated_config
            config = self._row_to_config(row)
            if not config:
                return None
            if not mutate_config(config):
                return None

            config.updated_at = datetime.now(timezone.utc)
            fields: dict[str, object] = {"config": config.model_dump(mode="json")}
            if fields_for_config is not None:
                fields.update(fields_for_config(config))
            updated_config = config
            return fields

        row = db.update_schedule_locked(schedule_id, _locked_update)
        if row is None:
            return None
        return updated_config

    def list_schedules(
        self,
        project_id: Optional[str] = None,
        group_id: str = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> ScheduleList:
        """Lista tutti gli schedule del gruppo, ordinati per data."""
        group_id = group_id or self.DEFAULT_GROUP
        if project_id is None and limit is None and offset == 0:
            rows = db.list_schedule_rows(group_id)
        else:
            rows = db.list_schedule_rows(
                group_id,
                project_id=project_id,
                limit=limit,
                offset=offset,
            )
        schedules = []
        for row in rows:
            config = self._row_to_config(row)
            if not config:
                continue
            schedules.append(config)

        schedules.sort(key=lambda x: x.updated_at, reverse=True)
        return ScheduleList(schedules=schedules, total=len(schedules))

    def schedule_exists(self, schedule_id: str) -> bool:
        return db.get_schedule_row(schedule_id) is not None

    def create_schedule_for_user(
        self,
        schedule_data: ScheduleCreate,
        *,
        group_id: str,
        created_by: str,
    ) -> ScheduleConfig:
        """Create a schedule from the public request boundary."""
        metadata = self._project_service_factory().get_project_metadata(
            schedule_data.project_id,
            group_id=group_id,
        )
        if not metadata:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.PROJECT_NOT_FOUND,
                    "message": f"Progetto '{schedule_data.project_id}' non trovato",
                },
            )

        self._validate_cron_expression(schedule_data.cron_expression)
        return self.create_schedule(
            schedule_data=schedule_data,
            project_name=metadata.name,
            project_type=metadata.project_type,
            group_id=group_id,
            created_by=created_by,
        )

    def _validate_cron_expression(self, cron_expression: str) -> None:
        if not croniter.is_valid(cron_expression):
            raise HTTPException(
                status_code=400,
                detail=f"Cron expression non valida: '{cron_expression}'",
            )

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
            runtime=ScheduleRuntime(status=RuntimeStatus.IDLE)
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
        def _mutate(config: ScheduleConfig) -> bool:
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

            next_exec = self._calculate_next_execution(
                config.schedule.cron_expression,
                config.schedule.timezone
            )
            if next_exec:
                config.runtime.next_execution = next_exec
            return True

        config = self._update_config_locked(
            schedule_id,
            _mutate,
            lambda cfg: {
                "cron_expression": cfg.schedule.cron_expression,
                "is_enabled": cfg.schedule.enabled,
            },
        )
        if not config:
            return None
        logger.info(f"Schedule aggiornato: {schedule_id}")
        return config

    # TOGGLE ENABLE/DISABLE

    def update_catchup_config(
        self,
        schedule_id: str,
        enabled: bool,
        grace_hours: int = 24,
    ) -> Optional[ScheduleConfig]:
        """Aggiorna il catchup config di uno schedule.

        Ritorna None se lo schedule non esiste.
        """
        def _mutate(config: ScheduleConfig) -> bool:
            config.schedule.catchup_missed_runs = enabled
            config.schedule.catchup_grace_hours = grace_hours
            return True

        config = self._update_config_locked(schedule_id, _mutate)
        if not config:
            return None
        logger.info(
            f"Schedule {schedule_id} catchup aggiornato: "
            f"enabled={enabled}, grace_hours={grace_hours}"
        )
        return config

    def toggle_schedule(self, schedule_id: str, enabled: bool) -> Optional[ScheduleConfig]:
        """Abilita o disabilita uno schedule."""
        def _mutate(config: ScheduleConfig) -> bool:
            # Confronto via enum canonical: evita typo silenti sullo stato
            # runtime persistito.
            if enabled and config.runtime.status == RuntimeStatus.ORPHAN:
                logger.warning(f"Impossibile abilitare schedule orfano {schedule_id}")
                return False

            config.schedule.enabled = enabled
            config.runtime.status = (
                RuntimeStatus.IDLE if enabled else RuntimeStatus.DISABLED
            )

            if enabled:
                next_exec = self._calculate_next_execution(
                    config.schedule.cron_expression,
                    config.schedule.timezone
                )
                if next_exec:
                    config.runtime.next_execution = next_exec
            else:
                config.runtime.next_execution = None
            return True

        config = self._update_config_locked(
            schedule_id,
            _mutate,
            lambda _cfg: {"is_enabled": enabled},
        )
        if not config:
            return None
        logger.info(f"Schedule {'abilitato' if enabled else 'disabilitato'}: {schedule_id}")
        return config

    # ELIMINAZIONE

    def delete_schedule(self, schedule_id: str) -> bool:
        """Elimina uno schedule e i suoi log."""
        if not db.get_schedule_row(schedule_id):
            return False

        db.delete_schedule(schedule_id)

        try:
            log_service = ScheduleLogService()
            deleted_logs = log_service.delete_schedule_logs(schedule_id)
            logger.info(f"Schedule eliminato: {schedule_id} ({deleted_logs} log rimossi)")
        except Exception as e:
            logger.warning(f"Schedule eliminato ma errore pulizia log: {e}")

        return True

    # GESTIONE ORFANI

    def mark_as_orphan(self, schedule_id: str) -> Optional[ScheduleConfig]:
        """Segna uno schedule come orfano (progetto eliminato).

        Read-modify-write protetto da row lock DB contro race con
        update_runtime / set_runtime_status / toggle_schedule su altri pod.
        """
        def _mutate(config: ScheduleConfig) -> bool:
            config.schedule.enabled = False
            config.runtime.status = RuntimeStatus.ORPHAN
            config.runtime.next_execution = None
            return True

        config = self._update_config_locked(
            schedule_id,
            _mutate,
            lambda _cfg: {"is_enabled": False},
        )
        if not config:
            return None
        logger.warning(f"Schedule orfano: {schedule_id}")
        return config

    def get_schedules_for_project(
        self, project_id: str, group_id: Optional[str] = None,
    ) -> List[ScheduleConfig]:
        """Lista tutti gli schedule di un progetto specifico.

        `group_id`: tenant in cui cercare. I caller che servono progetti
        di tenant non-default devono passare `group_id` esplicito,
        altrimenti `list_schedules` applica il proprio default di query.
        """
        return self.list_schedules(
            project_id=project_id, group_id=group_id,
        ).schedules

    # AGGIORNAMENTO RUNTIME

    def set_runtime_status(
        self,
        schedule_id: str,
        status: "str | RuntimeStatus",
        current_task_id: str = None,
    ) -> Optional[ScheduleConfig]:
        """Aggiorna solo lo stato runtime (transizioni leggere).

        Read-modify-write protetto da row lock DB.

        Accetta `str` per compatibilita' o `RuntimeStatus` (preferito).
        `coerce_runtime_status` normalizza a stringa canonical; il Pydantic
        `ScheduleStatus` Literal valida e la column DB resta una plain string.
        """
        status_str = coerce_runtime_status(status)

        def _mutate(config: ScheduleConfig) -> bool:
            config.runtime.status = status_str
            if current_task_id is not None:
                config.runtime.current_task_id = current_task_id
            return True

        return self._update_config_locked(schedule_id, _mutate)

    def update_runtime(
        self,
        schedule_id: str,
        status: "str | RuntimeStatus",
        last_execution: Optional[dict] = None,
    ) -> Optional[ScheduleConfig]:
        """Aggiorna lo stato runtime dopo un'esecuzione completata.

        Read-modify-write protetto da row lock DB contro race cross-process
        su counter (success_count, failure_count, consecutive_failures) e
        next_execution.

        Accetta enum o stringa compatibile; i confronti interni usano il
        valore canonical dell'enum.
        """
        status_str = coerce_runtime_status(status)
        # Validazione strict: rifiutiamo subito valori fuori enum cosi' il
        # caller scopre typo invece di vedere counter non aggiornati.
        # NB: `RuntimeStatus(...)` solleva
        # `ValueError` su input non valido.
        try:
            RuntimeStatus(status_str)
        except ValueError as exc:
            raise ValueError(
                f"update_runtime: status non valido '{status_str}'. "
                f"Atteso uno di {[e.value for e in RuntimeStatus]}."
            ) from exc

        def _mutate(config: ScheduleConfig) -> bool:
            config.runtime.status = status_str
            config.runtime.current_task_id = None

            if last_execution:
                config.runtime.last_execution = LastExecution(**last_execution)

            config.runtime.total_runs += 1
            if status_str == RuntimeStatus.COMPLETED:
                config.runtime.success_count += 1
                config.runtime.consecutive_failures = 0
            elif status_str == RuntimeStatus.PARTIAL:
                config.runtime.success_count += 1
                config.runtime.consecutive_failures = 0
            elif status_str == RuntimeStatus.FAILED:
                config.runtime.failure_count += 1
                config.runtime.consecutive_failures += 1

            if config.schedule.enabled:
                next_exec = self._calculate_next_execution(
                    config.schedule.cron_expression,
                    config.schedule.timezone
                )
                if next_exec:
                    config.runtime.next_execution = next_exec

            return True

        return self._update_config_locked(schedule_id, _mutate)

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
            tz = pytz.timezone(timezone)
            now = datetime.now(tz)
            cron = croniter(cron_expression, now)
            return cron.get_next(datetime)
        except Exception as e:
            logger.error(f"[CRASH] Errore calcolo next_execution: {e}", exc_info=True)
            return None
