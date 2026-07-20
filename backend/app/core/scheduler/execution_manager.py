"""
Execution Manager per lo Scheduler PLENORA-ETL.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import time
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from typing_extensions import TypedDict

from app.core.scheduler import db as scheduler_db
from app.core.scheduler.concurrency_manager import ConcurrencyManager
from app.core.scheduler.log_service import ScheduleLogService
from app.core.scheduler.models import ScheduleConfig, LogStep
from app.core.scheduler.policy_engine import PolicyEngine
from app.core.scheduler.runtime_status import RuntimeStatus
from app.core.scheduler.schedule_service import ScheduleService
from app.core.scheduler.scheduler_execution_runtime import (
    handle_failure,
    start_lock_heartbeat,
    stop_lock_heartbeat,
)
from app.core.scheduler.scheduler_preflight import pre_check_destination_connection
from app.utils.logger import get_logger, open_run, close_run, set_run_context
from app.core import artifacts as artifact_module
from app.core.carica import models as carica_models
from app.core.connections.service import ConnectionService
from app.core.dataframes import frame_io as dataframe_frame_io
from app.core.intermediate import cleanup as intermediate_cleanup
from app.core.intermediate import run_lifecycle as intermediate_run_lifecycle
from app.core.intermediate import runs_db as intermediate_runs_db
from app.core.orchestrator import execution_contract as execution_contract_module
from app.runtime.services import (
    build_connection_service,
    build_orchestrator_stack,
    build_project_service,
    build_scheduled_carica_executor,
)

logger = get_logger(__name__)


class SchedulerExecutionResult(TypedDict, total=False):
    """External result payload returned by scheduler executions."""

    success: bool
    status: str
    error: str
    message: str
    log_file: str
    rows_written: int
    lock_lost: bool


class SchedulerCaricaResult(TypedDict, total=False):
    """Internal result payload returned by scheduler-managed CARICA."""

    success: bool
    error: str
    message: str
    rows_written: int
    rows_inserted: int
    rows_updated: int
    rows_skipped: int


class ExecutionManager:
    """
    Gestisce l'esecuzione completa di una pipeline schedulata.
    
    Flusso:
    1. Verifica progetto esiste (altrimenti → orphan)
    2. Concurrency check (lock/coda/skip)
    3. Pre-check connessione CARICA (se configurato)
    4. Esegui pipeline headless
    5. Esegui CARICA (se configurato)
    6. Scrivi log + aggiorna runtime
    
    Il metodo principale è execute, chiamato dal task Celery.
    """
    
    def __init__(
        self,
        *,
        connection_service: Optional[ConnectionService] = None,
        connection_service_factory: Optional[Callable[[], ConnectionService]] = None,
        project_service_factory: Optional[Callable[[], object]] = None,
        carica_executor_factory: Optional[Callable[..., object]] = None,
        orchestrator_factory: Optional[Callable[..., object]] = None,
    ):
        self.schedule_service = ScheduleService()
        self.log_service = ScheduleLogService()
        self.concurrency = ConcurrencyManager()
        self.policy = PolicyEngine()
        # ConnectionService iniettabile; default lazy per schedule_task e
        # riuso in _get_orchestrator, _pre_check_connection, _execute_carica.
        self._connection_service = connection_service
        self._connection_service_factory = (
            connection_service_factory or build_connection_service
        )
        self._project_service_factory = (
            project_service_factory or build_project_service
        )
        # Factory composition-root per la scrittura CARICA schedulata:
        # i test iniettano qui un fake runner senza toccare carica.
        self._carica_executor_factory = (
            carica_executor_factory or build_scheduled_carica_executor
        )
        # La pipeline schedulata consuma un orchestrator costruito da una
        # factory iniettabile. I test scheduler usano un fake runner senza
        # patchare build_orchestrator_stack.
        self._orchestrator_factory = (
            orchestrator_factory or self._build_default_orchestrator
        )

    def _build_default_orchestrator(self) -> object:
        """Default composition-root: stack orchestrator completo."""
        stack = build_orchestrator_stack(
            connection_service=self._get_connection_service(),
        )
        return stack.orchestrator

    def _get_connection_service(self) -> ConnectionService:
        """ConnectionService lazy singleton per questa istanza di ExecutionManager."""
        if self._connection_service is None:
            factory = getattr(
                self,
                "_connection_service_factory",
                build_connection_service,
            )
            self._connection_service = factory()
        return self._connection_service

    def _get_orchestrator(self):
        """Orchestrator per l'esecuzione schedulata, via factory iniettata
        (G3): fake nei test, stack composition-root in produzione."""
        return self._orchestrator_factory()
    
    def execute(
        self,
        schedule_id: str,
        triggered_by: str = "cron",
        retry_attempt: int = 0,
        scheduled_for: Optional[str] = None,
        queue_reserved: bool = False,
    ) -> SchedulerExecutionResult:
        """
        Esegue una pipeline schedulata — metodo principale.

        Args:
            schedule_id: ID dello schedule da eseguire
            triggered_by: "cron", "manual", o "catchup"
            retry_attempt: Numero tentativo corrente (0 = primo)
            scheduled_for: ISO8601 del tick logico per catchup run
                (None per run normali). Propagato al log per lineage.
        
        Returns:
            Dict con risultato esecuzione:
            {
                "success": bool,
                "status": "completed" | "partial" | "failed" | "skipped" | "orphan",
                "message": str,
                "log_file": str
            }
        """
        start_time = time.time()
        steps = []
        run_id = str(_uuid.uuid4())

        def _cancel_reserved_queue() -> None:
            if queue_reserved:
                self.concurrency.cancel_queue_reservation(schedule_id)

        config = self.schedule_service.get_schedule(schedule_id)
        if not config:
            logger.error(f"[GESTITO] Schedule non trovato: {schedule_id}")
            _cancel_reserved_queue()
            return {"success": False, "status": "failed", "message": "Schedule non trovato"}

        project_id = config.pipeline.project_id
        _scheduled_for_display = scheduled_for or "now"
        logger.info(
            f"Esecuzione schedule {schedule_id} -> progetto {project_id} "
            f"(tentativo {retry_attempt}, triggered_by={triggered_by}, "
            f"scheduled_for={_scheduled_for_display})"
        )

        # I check su project_meta e group isolation devono precedere
        # `open_run`: se un check fallisce e il run e' gia' aperto, un
        # `return` senza `close_run` lascia il record in stato 'running'
        # per sempre (orphan run che sporca la dashboard e inquina il
        # check di task bloccate
        project_service_factory = getattr(
            self,
            "_project_service_factory",
            build_project_service,
        )
        project_service = project_service_factory()

        schedule_row = scheduler_db.get_schedule_row(schedule_id)
        schedule_group_id = schedule_row.group_id if schedule_row else None

        project_meta = project_service.get_project_metadata(project_id)
        if not project_meta:
            logger.error(f"[GESTITO] Progetto {project_id} non esiste — schedule diventa orphan")
            self.schedule_service.mark_as_orphan(schedule_id)

            entry = self.log_service.create_log_entry(
                schedule_id=schedule_id,
                event="PIPELINE_FAILED",
                triggered_by=triggered_by,
                level="ERROR",
                error_message=f"Progetto {project_id} non trovato — schedule disabilitato come orphan",
                pipeline_info={"project_id": project_id, "project_name": config.pipeline.project_name}
            )
            self.log_service.write_log(entry)

            _cancel_reserved_queue()
            return {"success": False, "status": "orphan", "message": "Progetto eliminato"}

        # Group isolation, FAIL-CLOSED
        # Ogni assenza di group_id (schedule senza gruppo, progetto
        # senza gruppo) e' un errore di consistenza che blocca
        # l'esecuzione, non un caso da ignorare. Un check del tipo
        # `if a and b and a != b` sarebbe fail-OPEN: lo short-circuit
        # del primo `and` lascerebbe passare l'esecuzione cross-tenant
        # se uno dei due lati ha group_id None/vuoto
        proj_group_id = getattr(project_meta, "group_id", None) or None

        def _isolation_failure(reason: str) -> SchedulerExecutionResult:
            logger.error(
                f"Group isolation violation: schedule {schedule_id} "
                f"(schedule_group={schedule_group_id!r}, project={project_id}, "
                f"project_group={proj_group_id!r}) — {reason}"
            )
            entry = self.log_service.create_log_entry(
                schedule_id=schedule_id,
                event="PIPELINE_FAILED",
                triggered_by=triggered_by,
                level="ERROR",
                error_message=f"Accesso negato (group isolation): {reason}",
                pipeline_info={"project_id": project_id},
            )
            self.log_service.write_log(entry)
            _cancel_reserved_queue()
            return {
                "success": False,
                "status": "failed",
                "message": f"Accesso negato: group isolation — {reason}",
            }

        if not schedule_group_id:
            return _isolation_failure("schedule senza group_id")
        if not proj_group_id:
            return _isolation_failure(
                f"progetto {project_id} senza group_id"
            )
        if proj_group_id != schedule_group_id:
            return _isolation_failure(
                "schedule e progetto appartengono a gruppi diversi"
            )

        # Gate canvas-validation per i run schedulati, dietro lo stesso
        # feature-flag del gate a esecuzione. Se il progetto non e' validato
        # (stato persistito ``canvas_validated``)
        # il run schedulato NON parte: si registra un evento PIPELINE_FAILED (il canale
        # notifiche dello scheduler, visibile in dashboard) e si ritorna failed.
        from app.core.orchestrator.service import canvas_validation_gate_blocks
        if canvas_validation_gate_blocks(getattr(project_meta, "canvas_validated", False)):
            logger.warning(
                f"[GESTITO] Run schedulato bloccato: progetto {project_id} non validato "
                f"(schedule {schedule_id})"
            )
            entry = self.log_service.create_log_entry(
                schedule_id=schedule_id,
                event="PIPELINE_FAILED",
                triggered_by=triggered_by,
                level="ERROR",
                error_message=(
                    "Run schedulato bloccato: il canvas del progetto non è validato. "
                    "Apri il progetto e premi Valida prima di schedularne l'esecuzione."
                ),
                pipeline_info={"project_id": project_id, "project_name": config.pipeline.project_name},
            )
            self.log_service.write_log(entry)
            _cancel_reserved_queue()
            return {
                "success": False,
                "status": "failed",
                "message": "Canvas non validato: run schedulato bloccato",
            }

        _group_id = getattr(project_meta, "group_id", "") or ""

        # Apri i record di run solo dopo lock acquisito: i path early-return
        # "skipped"/"queued" non hanno una run da chiudere.
        lock_result = self.concurrency.try_acquire(
            schedule_id,
            max_queue_size=config.concurrency.max_queue_size,
            owner_id=run_id,
            queue_reserved=queue_reserved,
        )

        if lock_result == "skipped":
            entry = self.log_service.create_log_entry(
                schedule_id=schedule_id,
                event="SCHEDULE_SKIPPED",
                triggered_by=triggered_by,
                level="WARNING",
                error_message="Coda piena, esecuzione saltata"
            )
            self.log_service.write_log(entry)
            return {"success": False, "status": "skipped", "message": "Coda piena"}

        if lock_result == "queued":
            entry = self.log_service.create_log_entry(
                schedule_id=schedule_id,
                event="SCHEDULE_QUEUED",
                triggered_by=triggered_by,
                level="INFO"
            )
            self.log_service.write_log(entry)
            # Il task Celery con countdown gestirà il retry dalla coda
            try:
                from app.core.scheduler.job_dispatcher import enqueue_schedule_run

                enqueue_schedule_run(
                    schedule_id,
                    triggered_by=triggered_by,
                    retry_attempt=retry_attempt,
                    scheduled_for=scheduled_for,
                    queue_reserved=True,
                    countdown=5,
                )
            except Exception:
                # Senza un successore nel broker lo slot non verrebbe mai
                # consumato e renderebbe la coda permanentemente piena.
                self.concurrency.cancel_queue_reservation(schedule_id)
                raise
            return {"success": False, "status": "queued", "message": "In coda"}

        # Lock acquisito: apri la run nel checkpoint store.
        open_run(
            run_id=run_id,
            project_id=project_id,
            group_id=_group_id,
            module=config.pipeline.project_type if config else "scheduler",
            triggered_by=triggered_by,
        )
        # Apri run anche su intermediate storage: `open_run` popola
        # `log_runs` per la dashboard, mentre `node_meta` ha FK su `runs.id`.
        try:
            intermediate_run_lifecycle.create_run(
                run_id=run_id,
                canvas_id=project_id,
                group_id=_group_id,
                user_id=None,  # cron / scheduled: no user umano
            )
        except Exception as _ie:
            logger.warning(
                f"Intermediate: apertura run fallita (non bloccante): {_ie}",
                op="execution_manager.create_intermediate_run",
            )
        set_run_context(run_id=run_id, project_id=project_id)

        # Qui siamo gia' dentro `execute()`: non c'e' un enqueue futuro che
        # possa fallire, quindi RUNNING rappresenta lo stato reale della run.
        self.schedule_service.set_runtime_status(schedule_id, RuntimeStatus.RUNNING)
        # Tracker per il rollback del runtime status nel finally. Settato
        # a True quando `update_runtime` viene chiamato (success path) o
        # quando `handle_failure` viene invocato (failure path); il
        # finally usa questo flag per fare set_runtime_status("idle") di
        # rollback se il worker e' stato killato a metà esecuzione e
        # nessuno dei due path canonici ha aggiornato lo status.
        runtime_status_finalized = False

        heartbeat_state = start_lock_heartbeat(
            self.concurrency,
            schedule_id,
            owner_id=run_id,
        )

        try:
            if config.destination and config.pre_check.verify_destination_connection:
                pre_check_ok = self._pre_check_connection(config)
                if not pre_check_ok:
                    raise ExecutionError(
                        "Pre-check connessione fallito: database destinazione non raggiungibile",
                        step="PRE_CHECK"
                    )
                # Il pre-check positivo resta silenzioso: non e' uno step
                # pipeline FE. Il fail usa ExecutionError(step="PRE_CHECK")
                # e viene registrato come fallimento boundary.
            
            pipeline_start = time.time()
            
            orchestrator = self._get_orchestrator()
            force_recalculate = not config.execution.use_cache

            execution_contract = execution_contract_module.build_pipeline_execution_contract(
                project_id=project_id,
                project_service=project_service,
            )

            result = orchestrator.execute_pipeline_from_contract(
                project_id=project_id,
                project_type=str(execution_contract["project_type"]),
                canvas=execution_contract["canvas"],
                force_recalculate=force_recalculate,
                skip_carica=True,  # Lo scheduler gestisce CARICA separatamente
                run_id=run_id,
            )
            
            pipeline_duration = int((time.time() - pipeline_start) * 1000)

            if not result.success:
                raise ExecutionError(
                    result.message or "Pipeline fallita",
                    step="PIPELINE"
                )

            # Aggiungi step pipeline al log
            # `result.rows_count` legge direttamente la chiave che tutti gli
            # executor scrivono (estrai/manipola/arricchisci/features).
            rows = result.rows_count or 0
            steps.append(LogStep(
                step=config.pipeline.project_type.upper(),
                status="success",
                duration_ms=pipeline_duration,
                rows=rows
            ))

            # Step OUTPUT: il payload canonico e' il result blob registrato in
            # `results` e referenziato nel log schedule via execution_info.
            # Non copiamo piu' in RESULTS_DIR/runs: in cluster il filesystem
            # locale non e' condiviso e non puo' essere source of truth.
            output_path_str = result.output_path or ""
            output_file = Path(output_path_str).name if output_path_str else ""
            output_ref = (
                f"result:{result.result_id}"
                if result.result_id else output_file or "N/A"
            )
            
            steps.append(LogStep(
                step="OUTPUT",
                status="success",
                duration_ms=0,
                file=output_ref,
            ))
            
            rows_written = 0
            rows_skipped = 0
            
            if config.destination:
                carica_start = time.time()
                carica_result = self._execute_carica(config, result, scheduled_for=scheduled_for)
                carica_duration = int((time.time() - carica_start) * 1000)
                
                if carica_result.get("success"):
                    rows_written = carica_result.get("rows_written", 0)
                    rows_skipped = carica_result.get("rows_skipped", 0)
                    
                    carica_status = "partial" if rows_skipped > 0 else "success"
                    steps.append(LogStep(
                        step="CARICA",
                        status=carica_status,
                        duration_ms=carica_duration,
                        rows=rows_written,
                        rows_inserted=carica_result.get("rows_inserted", 0),
                        rows_updated=carica_result.get("rows_updated", 0),
                        rows_skipped=rows_skipped,
                        write_mode=config.destination.write_mode,
                        destination=f"{config.destination.connection_id}/{config.destination.table_name}"
                    ))
                else:
                    raise ExecutionError(
                        carica_result.get("error", "CARICA fallita"),
                        step="CARICA"
                    )
            
            total_duration = int((time.time() - start_time) * 1000)
            
            decision = self.policy.decide_after_success(rows_written, rows_skipped)
            
            # Aggiorna runtime PRIMA di scrivere il log
            # se update_runtime fallisce, non vogliamo un log di successo orfano
            self.schedule_service.update_runtime(
                schedule_id,
                status=decision["status"],
                last_execution={
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": decision["status"],
                    "rows_written": rows_written,
                    "duration_ms": total_duration
                }
            )
            runtime_status_finalized = True
            
            entry = self.log_service.create_log_entry(
                schedule_id=schedule_id,
                event=decision["event"],
                triggered_by=triggered_by,
                level=decision["log_level"],
                steps=steps,
                total_duration_ms=total_duration,
                schedule_info={
                    "cron_expression": config.schedule.cron_expression,
                    "scheduled_at": datetime.now(timezone.utc).isoformat(),
                    "scheduled_for": scheduled_for,
                },
                pipeline_info={
                    "project_id": project_id,
                    "project_name": config.pipeline.project_name,
                    "project_type": config.pipeline.project_type
                },
                execution_info={
                    "use_cache": config.execution.use_cache,
                    "pre_check_passed": True,
                    "retry_attempt": retry_attempt,
                    "scheduled_for": scheduled_for,
                    "run_id": run_id,
                    "result_id": result.result_id,
                }
            )
            log_path = self.log_service.write_log(entry)

            _steps_json = [
                {
                    "module":      s.step.lower(),
                    "status":      s.status,
                    "rows_out":    s.rows or 0,
                    "duration_ms": s.duration_ms or 0,
                }
                for s in steps
            ]
            close_run(
                run_id=run_id,
                status="success" if decision["status"] != "failed" else "failure",
                rows_out=rows_written,
                duration_ms=total_duration,
                steps=_steps_json,
            )

            logger.info(f"Schedule {schedule_id} completato: {decision['status']} in {total_duration}ms")

            lock_lost = bool(heartbeat_state.get("lock_lost", False))
            if lock_lost:
                # La perdita del lock rende inaffidabile la run: ritorna
                # success=False e status partial cosi' Celery/UI non la
                # marcano come completata.
                logger.warning(
                    f"[LOCK_LOST] Schedule {schedule_id}: heartbeat ha "
                    f"rilevato perdita del lock durante l'esecuzione. "
                    f"Possibile concorrenza — la run viene marcata "
                    f"partial: gli output prodotti possono essere stati "
                    f"sovrascritti dal worker concorrente."
                )
                return {
                    "success": False,
                    "status": "partial",
                    "message": (
                        f"Pipeline completata in {total_duration}ms ma il "
                        f"lock e' stato perso durante l'esecuzione. "
                        f"Run marcata partial — verificare l'integrita' "
                        f"degli output ed eventuali run duplicati."
                    ),
                    "log_file": log_path,
                    "rows_written": rows_written,
                    "lock_lost": True,
                }

            return {
                "success": True,
                "status": decision["status"],
                "message": f"Pipeline completata in {total_duration}ms",
                "log_file": log_path,
                "rows_written": rows_written,
                "lock_lost": lock_lost,
            }

        except Exception as e:
            if isinstance(e, ExecutionError):
                err_msg = str(e)
            else:
                logger.error(
                    f"[CRASH] Errore inatteso schedule {schedule_id}: {e}",
                    exc_info=True,
                )
                # intent: boundary top-level sanitize — str(e) di eccezioni
                # unknown NON deve finire nel result dict Celery user-facing
                # via handle_failure. Dettaglio tecnico preservato nel
                # CRASH] logger.error sopra + canali admin (close_run
                # log_service.create_log_entry) che continuano a ricevere
                # err_msg_internal-style per postmortem
                err_msg = "Errore inatteso durante l'esecuzione dello schedule (consulta log)"
            lock_lost = bool(heartbeat_state.get("lock_lost", False))
            if lock_lost:
                logger.warning(
                    f"[LOCK_LOST] Schedule {schedule_id}: lock perso "
                    f"durante esecuzione fallita."
                )
            failure_result = handle_failure(
                schedule_service=self.schedule_service,
                log_service=self.log_service,
                policy=self.policy,
                config=config,
                schedule_id=schedule_id,
                triggered_by=triggered_by,
                retry_attempt=retry_attempt,
                error_message=err_msg,
                steps=steps,
                start_time=start_time,
                run_id=run_id,
                scheduled_for=scheduled_for,
            )
            # `handle_failure` invoca `schedule_service.update_runtime`
            # internamente (vedi `scheduler_execution_runtime.handle_failure`),
            # quindi lo status e' coerente: marca finalized.
            runtime_status_finalized = True
            if isinstance(failure_result, dict):
                failure_result["lock_lost"] = lock_lost
            return failure_result

        finally:
            try:
                stop_lock_heartbeat(heartbeat_state)
            except Exception as hb_err:
                logger.warning(f"[GESTITO] Errore stop heartbeat {schedule_id}: {hb_err}")
            # Rollback del runtime status se nessun path canonico l'ha
            # finalizzato. Caso tipico: worker SIGKILL'd / KeyboardInterrupt
            # tra `set_runtime_status("running")` e l'update_runtime di
            # success o failure → status resta "running" permanente anche
            # quando il lock scade. Best-effort: errori del rollback
            # vengono loggati ma non propagati per non oscurare la causa
            # originale del fallimento.
            if not runtime_status_finalized:
                try:
                    self.schedule_service.set_runtime_status(
                        schedule_id, RuntimeStatus.IDLE,
                    )
                    logger.warning(
                        f"[GESTITO] Schedule {schedule_id}: runtime status "
                        f"rollback a 'idle' nel finally (esecuzione "
                        f"interrotta prima dei path canonici)."
                    )
                except Exception as rb_err:
                    logger.warning(
                        f"[GESTITO] Rollback runtime status fallito per "
                        f"{schedule_id}: {rb_err}"
                    )
            # Rilascia sempre il lock con retry bounded su transient PG.
            from app.core.scheduler.scheduler_release import release_lock_with_retry
            release_lock_with_retry(
                self.concurrency,
                schedule_id,
                owner_id=run_id,
            )
            # Chiudi le connessioni PG thread-local del worker Celery
            # senza questa pulizia ogni esecuzione lascia conn aperte nel
            # thread.local del worker e dopo molte run le connessioni PG
            # restano aperte saturando il pool del server. Pattern ereditato
            # dalla migrazione SQLite -> PostgreSQL (in SQLite il rischio era
            # SQLITE_BUSY / file descriptor exhaustion
            try:
                intermediate_cleanup.close_worker_connection()
            except Exception as e:
                logger.debug(f"close_connection intermediate fallito: {type(e).__name__}: {e}")
            try:
                scheduler_db.close_connection()
            except Exception as e:
                logger.debug(f"close_connection config fallito: {type(e).__name__}: {e}")
    
    # PRE-CHECK CONNESSIONE
    
    def _pre_check_connection(self, config: ScheduleConfig) -> bool:
        """
        Verifica che il database di destinazione sia raggiungibile
        PRIMA di ricalcolare la pipeline.
        """
        return pre_check_destination_connection(
            config,
            self._get_connection_service(),
        )
    
    # ESECUZIONE CARICA
    
    def _execute_carica(
        self,
        config: ScheduleConfig,
        pipeline_result: dict,
        scheduled_for: Optional[str] = None,
    ) -> SchedulerCaricaResult:
        """
        Esegue il caricamento su database con la configurazione dello schedule.

        Recupera l'output della pipeline (dal result reader o dal path diretto),
        carica il DataFrame e lo scrive sulla tabella configurata nello schedule
        tramite CaricaExecutor (stessa logica dei task Celery CARICA).

        Differenza rispetto al canvas: usa connection_id/table_name dello schedule,
        non quelli del nodo CARICA nel canvas (skip_carica=True in execute_pipeline).

        N15: se `scheduled_for` e' impostato, deriva una chiave idempotency
        deterministica `schedule-{schedule_id}-{scheduled_for}-carica` e la
        passa all'executor come run_id. Su retry dello stesso tick scheduled,
        la stessa chiave viene ricostruita -> guard scatta -> no duplicati.

        Residuo: per cron "normali" senza scheduled_for, il run_id resta
        None e il guard e' no-op. Per chiudere anche quel caso, il beat
        scheduler dovrebbe sempre valorizzare scheduled_for col tick
        logico.
        """
        dest = config.destination
        logger.info(
            f"CARICA scheduler: {dest.write_mode} -> "
            f"{dest.connection_id}/{dest.table_name}",
            op="carica"
        )

        project_id = config.pipeline.project_id
        # N15: carica_run_id deterministico per idempotency su replay
        # dello stesso tick scheduled. None per cron senza
        # scheduled_for (guard no-op). Calcolato fuori dal try cosi'
        # e' visibile anche nell'except per chiudere la run come
        # "failed".
        carica_run_id = (
            f"schedule-{config.id}-{scheduled_for}-carica"
            if scheduled_for else None
        )

        try:
            # Risoluzione artefatto centralizzata via PipelineArtifact +
            # ArtifactResolver. Ordine di preferenza
            # inline_df → result_id (con verify project_id, hard fail su
            # mismatch) → output_path. Niente latest-by-project: due
            # esecuzioni parallele sullo stesso project si scambierebbero
            # silenziosamente l'output sulla tabella di destinazione
            artifact = artifact_module.PipelineArtifact.from_executor_result(
                pipeline_result, project_id=project_id
            )
            if artifact.artifact_kind == "none":
                logger.error(
                    f"[GESTITO] CARICA scheduler: nessun artefatto specifico "
                    f"della run per project={project_id} (no inline df, no "
                    f"result_id, no output_path). Abort.",
                    op="carica"
                )
                return {
                    "success": False,
                    "error": "Artefatto della run non disponibile per CARICA",
                }

            try:
                # mode="bytes": il consumer carica `m.data` senza tempfile.
                materialized = artifact_module.ArtifactResolver().materialize_run(
                    artifact.to_run_artifact_ref(),
                    mode="bytes",
                )
            except artifact_module.ArtifactResolutionError as e:
                logger.error(
                    f"[GESTITO] CARICA scheduler: risoluzione artefatto "
                    f"fallita per project={project_id}: {e}",
                    op="carica"
                )
                return {"success": False, "error": str(e)}

            with materialized as m:
                if m.df is not None:
                    df = m.df
                elif m.data is not None:
                    df = dataframe_frame_io.load_dataframe_from_artifact(
                        data=m.data, format_hint=m.format,
                    )
                else:
                    df = dataframe_frame_io.load_dataframe_from_file(m.path)

            if df.empty:
                return {"success": False, "error": "DataFrame output vuoto"}

            node_data = {
                "connection_id": dest.connection_id,
                "schema_name": dest.schema_name or "",
                "table_name": dest.table_name,
                "write_mode": dest.write_mode,
                "match_columns": dest.match_columns or [],
                "batch_size": dest.batch_size,
                "transaction_mode": "all_or_nothing",
                "create_spatial_index": True,
                "conflict_policy": "abort",
                "column_mappings": [],  # auto-mapping da CaricaExecutor
            }

            carica_executor = self._carica_executor_factory(
                connection_service=self._get_connection_service()
            )

            # Registra la run intermediate prima dell'execute: il run_id
            # deterministico deve esistere per permettere al guard
            # idempotente di riconoscere replay dello stesso tick.
            if carica_run_id:
                try:
                    if intermediate_runs_db.get_run(carica_run_id) is None:
                        intermediate_run_lifecycle.create_run(
                            run_id=carica_run_id,
                            canvas_id=project_id,
                            group_id=f"schedule-{config.id}-carica",
                            user_id=None,
                        )
                except Exception as _ie:
                    # Fail-open: se l'apertura della run intermediate esplode,
                    # non blocco la scrittura. L'unico effetto collaterale e'
                    # che il guard idempotency non scattera' al replay.
                    logger.warning(
                        f"Intermediate: apertura run CARICA fallita "
                        f"(non bloccante) per {carica_run_id}: {_ie}",
                        op="execution_manager._execute_carica",
                    )

            write_result = carica_executor.execute(
                df=df,
                node_data=node_data,
                project_id=project_id,
                triggered_by="scheduler",
                run_id=carica_run_id,
            )

            # SKIPPED_IDEMPOTENT is a safe no-op on retry.
            # Se non incluso qui, il guard causa retry loop infiniti
            success = write_result.status in (
                carica_models.WriteStatus.SUCCESS,
                carica_models.WriteStatus.PARTIAL,
                carica_models.WriteStatus.SKIPPED_IDEMPOTENT,
            )

            # Chiudi la run intermediate per il guard idempotency.
            # Su SKIPPED_IDEMPOTENT lo status era gia' "completed" dal
            # primo tick: questo update e' no-op semantico ma resta
            # coerente. Su failure marchiamo "failed" cosi' il replay
            # successivo riprova invece di skippare.
            if carica_run_id:
                try:
                    intermediate_runs_db.update_run(
                        carica_run_id,
                        status="completed" if success else "failed",
                    )
                except Exception as _ue:
                    logger.warning(
                        f"Intermediate: chiusura run CARICA fallita "
                        f"(non bloccante) per {carica_run_id}: {_ue}",
                        op="execution_manager._execute_carica",
                    )

            if not success:
                error_msg = write_result.error_message or f"Scrittura fallita: {write_result.status.value}"
                logger.error(f"[GESTITO] CARICA scheduler fallito: {error_msg}", op="carica")
                return {"success": False, "error": error_msg}

            rows_written = write_result.rows_written or 0
            rows_inserted = write_result.rows_inserted or 0
            rows_updated = write_result.rows_updated or 0
            rows_skipped = write_result.rows_skipped

            logger.info(
                f"CARICA scheduler completato: {rows_written} righe -> "
                f"{dest.connection_id}/{dest.table_name}",
                op="carica"
            )
            return {
                "success": True,
                "rows_written": rows_written,
                "rows_inserted": rows_inserted or 0,
                "rows_updated": rows_updated or 0,
                "rows_skipped": max(rows_skipped, 0),
            }

        except Exception as e:
            logger.error(f"[GESTITO] Errore CARICA scheduler: {e}", op="carica", exc_info=True)
            # Marcatura best-effort della run come failed: se il
            # crash e' arrivato dopo create_run ma prima
            # dell'update_run di chiusura, la riga resta "running" e
            # nessun guard scatterebbe al replay. Marcandola "failed"
            # il replay del prossimo tick riprova (e non skippa).
            # Se la run non era mai stata creata, l'UPDATE colpisce 0
            # righe → no-op silente, comportamento corretto.
            if carica_run_id:
                try:
                    intermediate_runs_db.update_run(
                        carica_run_id, status="failed"
                    )
                except Exception as _ue:
                    logger.warning(
                        f"Intermediate: marcatura run CARICA failed "
                        f"fallita (non bloccante) per {carica_run_id}: {_ue}",
                        op="execution_manager._execute_carica",
                    )
            # intent: boundary CARICA scheduler / outcome: fail-closed sanitizzato
            return {
                "success": False,
                "error": "carica_scheduler_failed",
                "message": "Errore esecuzione CARICA scheduler (consulta log)",
            }
    
class ExecutionError(Exception):
    """Errore durante l'esecuzione di uno step specifico."""
    
    def __init__(self, message: str, step: str = ""):
        self.step = step
        super().__init__(message)
