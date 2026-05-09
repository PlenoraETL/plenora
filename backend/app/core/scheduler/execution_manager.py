"""
Execution Manager per lo Scheduler PLENORA-ETL.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import time
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

from app.config import settings
from app.utils.logger import get_logger, open_run, close_run, set_run_context

logger = get_logger(__name__)

from app.core.scheduler.models import ScheduleConfig, LogStep
from app.core.scheduler.schedule_service import ScheduleService
from app.core.scheduler.log_service import ScheduleLogService
from app.core.scheduler.concurrency_manager import ConcurrencyManager
from app.core.scheduler.policy_engine import PolicyEngine
from app.core.scheduler.scheduler_execution_runtime import (
    handle_failure,
    start_lock_heartbeat,
    stop_lock_heartbeat,
)
from app.core.scheduler.scheduler_preflight import pre_check_destination_connection


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
    
    def __init__(self):
        self.schedule_service = ScheduleService()
        self.log_service = ScheduleLogService()
        self.concurrency = ConcurrencyManager()
        self.policy = PolicyEngine()
        # ConnectionService singleton — riusata in _get_orchestrator
        # _pre_check_connection, _execute_carica
        self._connection_service = None
        from app.core.scheduler.artifact_store import ScheduleArtifactStore
        self._artifact_store = ScheduleArtifactStore()

    def _get_connection_service(self):
        """ConnectionService lazy singleton per questa istanza di ExecutionManager."""
        if self._connection_service is None:
            from app.core.connections.service import ConnectionService
            self._connection_service = ConnectionService()
        return self._connection_service

    def _get_orchestrator(self):
        """
        Crea un'istanza dell'OrchestratorService con tutte le dipendenze.
        
        Importato lazy per evitare circular imports e perché i servizi
        richiedono settings già inizializzati.
        """
        from app.core.projects.service import ProjectService
        from app.db.query_executor import QueryExecutor
        from app.core.estrai.query_generator import QueryGenerator
        from app.core.orchestrator.service import OrchestratorService
        from app.core.features.features_service import FeaturesService
        from app.utils.dataframe import save_dataframe

        project_service = ProjectService()
        connection_service = self._get_connection_service()
        query_generator = QueryGenerator()
        query_executor = QueryExecutor(
            results_dir=settings.RESULTS_DIR,
            save_function=save_dataframe
        )
        # FeaturesService necessario se la pipeline usa Feature Service ArcGIS
        features_service = FeaturesService(save_function=save_dataframe)

        return OrchestratorService(
            project_service=project_service,
            executor=query_executor,
            generator=query_generator,
            connection_service=connection_service,
            features_service=features_service,
        )
    
    # HEARTBEAT LOCK (C3 fix 4)

    def _start_lock_heartbeat(self, schedule_id: str, interval: float = None, clock=None) -> Dict[str, Any]:
        """Compatibility wrapper for scheduler_execution_runtime."""
        return start_lock_heartbeat(self.concurrency, schedule_id, interval, clock)

    def _stop_lock_heartbeat(self, state: Dict[str, Any]) -> None:
        """Compatibility wrapper for scheduler_execution_runtime."""
        stop_lock_heartbeat(state)

    # ESECUZIONE PRINCIPALE

    def execute(
        self,
        schedule_id: str,
        triggered_by: str = "cron",
        retry_attempt: int = 0,
        scheduled_for: Optional[str] = None,
    ) -> Dict[str, Any]:
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

        config = self.schedule_service.get_schedule(schedule_id)
        if not config:
            logger.error(f"[GESTITO] Schedule non trovato: {schedule_id}")
            return {"success": False, "status": "failed", "message": "Schedule non trovato"}

        project_id = config.pipeline.project_id
        # C3 round 3 fix 2: log esplicito di scheduled_for per lineage
        # dei catchup run. Distingue "run eseguito alle 10:00 per il
        # tick delle 06:00" (catchup) da "run alle 10:00 per le 10:00"
        # cron normale o manuale
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
        from app.core.projects.service import ProjectService
        from app.core.scheduler.db import get_schedule
        project_service = ProjectService()

        schedule_row = get_schedule(schedule_id)
        schedule_group_id = schedule_row.get("group_id") if schedule_row else None

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

            return {"success": False, "status": "orphan", "message": "Progetto eliminato"}

        # Group isolation, FAIL-CLOSED
        # Ogni assenza di group_id (schedule senza gruppo, progetto
        # senza gruppo) e' un errore di consistenza che blocca
        # l'esecuzione, non un caso da ignorare. Un check del tipo
        # `if a and b and a != b` sarebbe fail-OPEN: lo short-circuit
        # del primo `and` lascerebbe passare l'esecuzione cross-tenant
        # se uno dei due lati ha group_id None/vuoto
        proj_group_id = getattr(project_meta, "group_id", None) or None

        def _isolation_failure(reason: str) -> dict:
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

        _group_id = getattr(project_meta, "group_id", "") or ""
        open_run(
            run_id=run_id,
            project_id=project_id,
            group_id=_group_id,
            module=config.pipeline.project_type if config else "scheduler",
            triggered_by=triggered_by,
        )
        set_run_context(run_id=run_id, project_id=project_id)
        
        lock_result = self.concurrency.try_acquire(
            schedule_id, 
            max_queue_size=config.concurrency.max_queue_size
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
            return {"success": True, "status": "queued", "message": "In coda"}
        
        self.schedule_service.set_runtime_status(schedule_id, "running")

        # Avvia heartbeat del lock (C3 fix 4): il TTL del lock e' corto
        # 10min), un thread daemon rinfresca mentre la pipeline e' attiva
        # Su SIGKILL del worker, il thread muore senza rinfrescare, e il
        # lock scade entro TTL invece di restare orfano fino al hard limit
        heartbeat_state = self._start_lock_heartbeat(schedule_id)

        try:
            if config.destination and config.pre_check.verify_destination_connection:
                pre_check_ok = self._pre_check_connection(config)
                if not pre_check_ok:
                    raise ExecutionError(
                        "Pre-check connessione fallito: database destinazione non raggiungibile",
                        step="PRE_CHECK"
                    )
                steps.append(LogStep(step="CARICA", status="success", duration_ms=0))
            
            pipeline_start = time.time()
            
            orchestrator = self._get_orchestrator()
            force_recalculate = not config.execution.use_cache
            
            result = orchestrator.execute_pipeline(
                project_id=project_id,
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
            # `result.rows_count` legge direttamente la chiave che TUTTI
            # gli executor scrivono (estrai/manipola/arricchisci/features
            # il vecchio `result.get("row_count", result.get("rows", 0))`
            # leggeva chiavi che nessun executor scriveva → telemetria
            # sempre 0
            rows = result.rows_count or 0
            steps.append(LogStep(
                step=config.pipeline.project_type.upper(),
                status="success",
                duration_ms=pipeline_duration,
                rows=rows
            ))

            # Step OUTPUT (file salvato dall'orchestrator
            # `output_path` e' la chiave canonica (scritta da tutti gli
            # executor); Path.name estrae il basename per l'archivio
            output_path_str = result.output_path or ""
            output_file = Path(output_path_str).name if output_path_str else ""

            # Arricchisci salva direttamente come blob nel DB (via
            # save_result_blob) senza scrivere in results/{project_id}/
            # Se non c'e' output_path ma il result ha un artifact
            # risolvibile (result_id), lo materializziamo qui su
            # filesystem prima di archiviare, altrimenti lo scheduler
            # archivierebbe una cartella vuota e la lista runs sarebbe
            # sempre vuota
            if not output_file:
                try:
                    from app.core.artifacts import (
                        ArtifactResolver, ArtifactResolutionError,
                    )
                    ref = result.to_run_artifact_ref()
                    if ref is not None:
                        materialized = ArtifactResolver().materialize_run(ref)
                        with materialized as m:
                            if m.path:
                                # Copia nella project output dir con nome
                                # basename del blob materializzato
                                import shutil as _shutil
                                from app.config import settings as _s
                                dest_dir = Path(_s.RESULTS_DIR) / project_id
                                dest_dir.mkdir(parents=True, exist_ok=True)
                                dest_path = dest_dir / Path(m.path).name
                                _shutil.copy2(m.path, dest_path)
                                output_file = dest_path.name
                                logger.info(
                                    f"Output materializzato da blob per "
                                    f"archive: {dest_path}"
                                )
                except ArtifactResolutionError as _re:
                    logger.warning(
                        f"Materializzazione blob per archive fallita: {_re}"
                    )
                except Exception as _me:
                    logger.warning(
                        f"Errore materializzazione output per archive: {_me}",
                        exc_info=True,
                    )

            # L'orchestrator salva in results/{project_id}/ (sovrascrive
            # Lo scheduler copia in results/{project_id}/runs/{schedule}/{timestamp}/
            archived_path = self._artifact_store.archive(
                project_id=project_id,
                schedule_id=schedule_id,
                output_file=output_file
            )
            
            steps.append(LogStep(
                step="OUTPUT",
                status="success",
                duration_ms=0,
                file=archived_path or output_file or "N/A"
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
                    # C3 round 3 fix 2: scheduled_for = tick logico
                    # recuperato (catchup) o None per run normali
                    # Persistito nel log per lineage audit
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

            # C3 round 3 fix 1: propaga lock_lost al result se il
            # thread heartbeat ha rilevato perdita del lock durante
            # l'esecuzione (lock scaduto + re-acquire fallito = un
            # altro processo potrebbe aver eseguito in parallelo
            lock_lost = bool(heartbeat_state.get("lock_lost", False))
            if lock_lost:
                logger.warning(
                    f"[LOCK_LOST] Schedule {schedule_id}: heartbeat ha "
                    f"rilevato perdita del lock durante l'esecuzione. "
                    f"Possibile concorrenza — verificare eventuali run "
                    f"duplicati per lo stesso schedule_id."
                )

            return {
                "success": True,
                "status": decision["status"],
                "message": f"Pipeline completata in {total_duration}ms",
                "log_file": log_path,
                "rows_written": rows_written,
                "lock_lost": lock_lost,
            }

        except Exception as e:
            # Unico ramo per ExecutionError (step esplicito) e Exception
            # errore inatteso): i due casi differiscono solo per il
            # prefisso del messaggio e il log [CRASH] su quello inatteso
            if isinstance(e, ExecutionError):
                err_msg = str(e)
            else:
                logger.error(
                    f"[CRASH] Errore inatteso schedule {schedule_id}: {e}",
                    exc_info=True,
                )
                # intent: boundary top-level sanitize — str(e) di eccezioni
                # unknown NON deve finire nel result dict Celery user-facing
                # via _handle_failure. Dettaglio tecnico preservato nel
                # CRASH] logger.error sopra + canali admin (close_run
                # log_service.create_log_entry) che continuano a ricevere
                # err_msg_internal-style per postmortem
                err_msg = "Errore inatteso durante l'esecuzione dello schedule (consulta log)"
            # Propagazione lock_lost anche nel failure branch: se il
            # lock era perso e la pipeline e' poi fallita, l'operatore
            # deve poter distinguere fail "pulito" da fail "con
            # concorrenza potenzialmente attiva"
            lock_lost = bool(heartbeat_state.get("lock_lost", False))
            if lock_lost:
                logger.warning(
                    f"[LOCK_LOST] Schedule {schedule_id}: lock perso "
                    f"durante esecuzione fallita."
                )
            failure_result = self._handle_failure(
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
            if isinstance(failure_result, dict):
                failure_result["lock_lost"] = lock_lost
            return failure_result
        
        finally:
            # Ferma prima il thread heartbeat (C3 fix 4) cosi' non
            # rinfresca piu' il lock mentre lo stiamo rilasciando
            try:
                self._stop_lock_heartbeat(heartbeat_state)
            except Exception as hb_err:
                logger.warning(f"[GESTITO] Errore stop heartbeat {schedule_id}: {hb_err}")
            # Rilascia sempre il lock con retry bounded su transient PG.
            from app.core.scheduler.scheduler_release import release_lock_with_retry
            release_lock_with_retry(self.concurrency, schedule_id)
            # Chiudi le connessioni PG thread-local del worker Celery
            # senza questa pulizia ogni esecuzione lascia conn aperte nel
            # thread.local del worker e dopo molte run le connessioni PG
            # restano aperte saturando il pool del server. Pattern ereditato
            # dalla migrazione SQLite -> PostgreSQL (in SQLite il rischio era
            # SQLITE_BUSY / file descriptor exhaustion
            try:
                from app.core.intermediate.cleanup import close_worker_connection
                close_worker_connection()
            except Exception as e:
                logger.debug(f"close_connection intermediate fallito: {type(e).__name__}: {e}")
            try:
                from app.db.pg_connection import close_connection as _close_cfg
                _close_cfg()
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
    ) -> dict:
        """
        Esegue il caricamento su database con la configurazione dello schedule.

        Recupera l'output della pipeline (dal result_store o dal path diretto),
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

        try:
            # Risoluzione artefatto centralizzata via PipelineArtifact +
            # ArtifactResolver. Ordine di preferenza
            # inline_df → result_id (con verify project_id, hard fail su
            # mismatch) → output_path. Niente latest-by-project: due
            # esecuzioni parallele sullo stesso project si scambierebbero
            # silenziosamente l'output sulla tabella di destinazione
            from app.utils.dataframe import load_dataframe_from_file
            from app.core.artifacts import (
                ArtifactResolver,
                ArtifactResolutionError,
                PipelineArtifact,
            )

            project_id = config.pipeline.project_id

            artifact = PipelineArtifact.from_executor_result(
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
                materialized = ArtifactResolver().materialize_run(
                    artifact.to_run_artifact_ref()
                )
            except ArtifactResolutionError as e:
                logger.error(
                    f"[GESTITO] CARICA scheduler: risoluzione artefatto "
                    f"fallita per project={project_id}: {e}",
                    op="carica"
                )
                return {"success": False, "error": str(e)}

            with materialized as m:
                if m.df is not None:
                    df = m.df
                else:
                    df = load_dataframe_from_file(m.path)

            if df.empty:
                return {"success": False, "error": "DataFrame output vuoto"}

            # 2. Costruisci node_data compatibile con CaricaExecutor.execute
            # usando la configurazione della destinazione dello schedule
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

            # 3. Istanzia CaricaExecutor con ConnectionService
            from app.core.carica.executor import CaricaExecutor

            carica_executor = CaricaExecutor(connection_service=self._get_connection_service())

            # N15: chiave idempotency deterministica sul tick scheduled
            # stabile su retry dello stesso tick). None se scheduled_for
            # non e' disponibile -> nessuna protezione sul guard executor
            # residuo documentato
            carica_run_id = (
                f"schedule-{config.id}-{scheduled_for}-carica"
                if scheduled_for else None
            )

            write_result = carica_executor.execute(
                df=df,
                node_data=node_data,
                project_id=project_id,
                triggered_by="scheduler",
                run_id=carica_run_id,
            )

            # 4. Traduci WriteResult nel formato atteso dall'execution_manager
            from app.core.carica.models import WriteStatus
            # N15: SKIPPED_IDEMPOTENT e' success (no-op sicuro su retry
            # Se non incluso qui, il guard causa retry loop infiniti
            success = write_result.status in (
                WriteStatus.SUCCESS,
                WriteStatus.PARTIAL,
                WriteStatus.SKIPPED_IDEMPOTENT,
            )

            if not success:
                error_msg = write_result.error_message or f"Scrittura fallita: {write_result.status.value}"
                logger.error(f"[GESTITO] CARICA scheduler fallito: {error_msg}", op="carica")
                return {"success": False, "error": error_msg}

            rows_written = write_result.rows_written or 0
            rows_inserted = write_result.rows_inserted or 0
            rows_updated = write_result.rows_updated or 0
            rows_skipped = (write_result.total_rows or 0) - rows_written

            logger.info(
                f"CARICA scheduler completato: {rows_written} righe -> "
                f"{dest.connection_id}/{dest.table_name}",
                op="carica"
            )
            return {
                "success": True,
                "rows_written": rows_written,
                "rows_inserted": rows_inserted,
                "rows_updated": rows_updated,
                "rows_skipped": max(rows_skipped, 0),
            }

        except Exception as e:
            logger.error(f"[GESTITO] Errore CARICA scheduler: {e}", op="carica", exc_info=True)
            # intent: boundary CARICA scheduler / outcome: fail-closed sanitizzato
            return {
                "success": False,
                "error": "carica_scheduler_failed",
                "message": "Errore esecuzione CARICA scheduler (consulta log)",
            }
    
    # GESTIONE FALLIMENTI
    
    def _handle_failure(
        self,
        config: ScheduleConfig,
        schedule_id: str,
        triggered_by: str,
        retry_attempt: int,
        error_message: str,
        steps: list,
        start_time: float,
        run_id: str = "",
        scheduled_for: Optional[str] = None,
    ) -> dict:
        """Compatibility wrapper for scheduler_execution_runtime."""
        return handle_failure(
            schedule_service=self.schedule_service,
            log_service=self.log_service,
            policy=self.policy,
            config=config,
            schedule_id=schedule_id,
            triggered_by=triggered_by,
            retry_attempt=retry_attempt,
            error_message=error_message,
            steps=steps,
            start_time=start_time,
            run_id=run_id,
            scheduled_for=scheduled_for,
        )

class ExecutionError(Exception):
    """Errore durante l'esecuzione di uno step specifico."""
    
    def __init__(self, message: str, step: str = ""):
        self.step = step
        super().__init__(message)
