"""
Execution Manager per lo Scheduler URBY-ETL.

Architettura
============
Cuore dell'esecuzione schedulata. Coordina tutti i componenti:

    1. Pre-check connessione CARICA (se configurato)
    2. Esecuzione pipeline headless via OrchestratorService
    3. Salvataggio file output (sempre, gestito dall'orchestrator)
    4. Esecuzione CARICA su database (se configurato nello schedule)
    5. Scrittura log strutturato
    6. Aggiornamento stato runtime dello schedule

Dipendenze:
    - OrchestratorService → esegue la pipeline (ESTRAI/MANIPOLA/ARRICCHISCI)
    - ConcurrencyManager → lock Redis per evitare esecuzioni parallele
    - PolicyEngine → retry, conflitti, valutazione risultato
    - ScheduleService → aggiornamento stato runtime
    - ScheduleLogService → log esecuzioni su filesystem

Nota: l'orchestrator viene chiamato con skip_carica=True perché lo scheduler
gestisce CARICA con la propria configurazione (dallo schedule, non dal canvas).
"""

import time
import uuid as _uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from app.config import settings
from app.utils.logger import get_logger, open_run, close_run, set_run_context, append_step

logger = get_logger(__name__)

from app.core.scheduler.models import ScheduleConfig, LogStep
from app.core.scheduler.schedule_service import ScheduleService
from app.core.scheduler.log_service import ScheduleLogService
from app.core.scheduler.concurrency_manager import ConcurrencyManager
from app.core.scheduler.policy_engine import PolicyEngine


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
    
    Il metodo principale è execute(), chiamato dal task Celery.
    """
    
    def __init__(self):
        self.schedule_service = ScheduleService()
        self.log_service = ScheduleLogService()
        self.concurrency = ConcurrencyManager()
        self.policy = PolicyEngine()
        # ConnectionService singleton — riusata in _get_orchestrator, _pre_check_connection, _execute_carica
        self._connection_service = None

    def _get_connection_service(self):
        """ConnectionService lazy singleton per questa istanza di ExecutionManager."""
        if self._connection_service is None:
            from app.services.connection_service import ConnectionService
            self._connection_service = ConnectionService()
        return self._connection_service

    def _get_orchestrator(self):
        """
        Crea un'istanza dell'OrchestratorService con tutte le dipendenze.
        
        Importato lazy per evitare circular imports e perché i servizi
        richiedono settings già inizializzati.
        """
        from app.services.project_service import ProjectService
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
    
    # =========================================================================
    # ESECUZIONE PRINCIPALE
    # =========================================================================
    
    def execute(
        self,
        schedule_id: str,
        triggered_by: str = "cron",
        retry_attempt: int = 0
    ) -> Dict[str, Any]:
        """
        Esegue una pipeline schedulata — metodo principale.
        
        Args:
            schedule_id: ID dello schedule da eseguire
            triggered_by: "cron" o "manual"
            retry_attempt: Numero tentativo corrente (0 = primo)
        
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

        # --- Carica configurazione schedule ---
        config = self.schedule_service.get_schedule(schedule_id)
        if not config:
            logger.error(f"[GESTITO] Schedule non trovato: {schedule_id}")
            return {"success": False, "status": "failed", "message": "Schedule non trovato"}

        project_id = config.pipeline.project_id
        logger.info(f"Esecuzione schedule {schedule_id} → progetto {project_id} (tentativo {retry_attempt})")

        # --- Leggi group_id del progetto ---
        _group_id = ""
        try:
            from app.services.project_service import ProjectService as _PS
            _meta = _PS().get_project_metadata(project_id)
            _group_id = getattr(_meta, "group_id", "") if _meta else ""
        except Exception:
            pass

        # --- Apri run su log.db ---
        open_run(
            run_id=run_id,
            project_id=project_id,
            group_id=_group_id,
            module=config.pipeline.project_type if config else "scheduler",
            triggered_by=triggered_by,
        )
        set_run_context(run_id=run_id, project_id=project_id)

        # --- 1b. Verifica progetto esiste e appartiene allo stesso gruppo ---
        from app.services.project_service import ProjectService
        import app.db.db_service as _db
        project_service = ProjectService()

        schedule_row = _db.get_schedule(schedule_id)
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

        # Group isolation: lo schedule non può eseguire un progetto di un altro gruppo
        if schedule_group_id and project_meta.group_id and project_meta.group_id != schedule_group_id:
            logger.error(
                f"Group isolation violation: schedule {schedule_id} (group={schedule_group_id}) "
                f"tenta di eseguire progetto {project_id} (group={project_meta.group_id})"
            )
            entry = self.log_service.create_log_entry(
                schedule_id=schedule_id,
                event="PIPELINE_FAILED",
                triggered_by=triggered_by,
                level="ERROR",
                error_message="Accesso negato: il progetto appartiene a un gruppo diverso",
                pipeline_info={"project_id": project_id}
            )
            self.log_service.write_log(entry)
            return {"success": False, "status": "failed", "message": "Accesso negato: group isolation"}
        
        # --- 2. Concurrency check ---
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
        
        # --- Lock acquisito, procediamo ---
        self.schedule_service.set_runtime_status(schedule_id, "running")
        
        try:
            # --- 3. Pre-check connessione CARICA ---
            if config.destination and config.pre_check.verify_destination_connection:
                pre_check_ok = self._pre_check_connection(config)
                if not pre_check_ok:
                    raise ExecutionError(
                        "Pre-check connessione fallito: database destinazione non raggiungibile",
                        step="PRE_CHECK"
                    )
                steps.append(LogStep(step="CARICA", status="success", duration_ms=0))
            
            # --- 4. Esegui pipeline headless ---
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
            
            if not result.get("success"):
                raise ExecutionError(
                    result.get("message", "Pipeline fallita"),
                    step="PIPELINE"
                )
            
            # Aggiungi step pipeline al log
            rows = result.get("row_count", result.get("rows", 0))
            steps.append(LogStep(
                step=config.pipeline.project_type.upper(),
                status="success",
                duration_ms=pipeline_duration,
                rows=rows
            ))
            
            # Step OUTPUT (file salvato dall'orchestrator)
            output_file = result.get("file", result.get("output_file", ""))
            
            # --- 4b. Archivia output in cartella timestamped ---
            # L'orchestrator salva in results/{project_id}/ (sovrascrive)
            # Lo scheduler copia in results/{project_id}/runs/{schedule}/{timestamp}/
            archived_path = self._archive_output(
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
            
            # --- 5. Esegui CARICA (se configurato) ---
            rows_written = 0
            rows_skipped = 0
            
            if config.destination:
                carica_start = time.time()
                carica_result = self._execute_carica(config, result)
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
            
            # --- 6. Successo — aggiorna runtime + log ---
            total_duration = int((time.time() - start_time) * 1000)
            
            decision = self.policy.decide_after_success(rows_written, rows_skipped)
            
            # Aggiorna runtime PRIMA di scrivere il log
            # (se update_runtime fallisce, non vogliamo un log di successo orfano)
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
                    "scheduled_at": datetime.now(timezone.utc).isoformat()
                },
                pipeline_info={
                    "project_id": project_id,
                    "project_name": config.pipeline.project_name,
                    "project_type": config.pipeline.project_type
                },
                execution_info={
                    "use_cache": config.execution.use_cache,
                    "pre_check_passed": True,
                    "retry_attempt": retry_attempt
                }
            )
            log_path = self.log_service.write_log(entry)

            # --- Chiudi run su log.db ---
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
            
            return {
                "success": True,
                "status": decision["status"],
                "message": f"Pipeline completata in {total_duration}ms",
                "log_file": log_path,
                "rows_written": rows_written
            }
        
        except ExecutionError as e:
            return self._handle_failure(
                config=config,
                schedule_id=schedule_id,
                triggered_by=triggered_by,
                retry_attempt=retry_attempt,
                error_message=str(e),
                steps=steps,
                start_time=start_time,
                run_id=run_id,
            )
        
        except Exception as e:
            logger.error(f"[CRASH] Errore inatteso schedule {schedule_id}: {e}", exc_info=True)
            return self._handle_failure(
                config=config,
                schedule_id=schedule_id,
                triggered_by=triggered_by,
                retry_attempt=retry_attempt,
                error_message=f"Errore inatteso: {str(e)}",
                steps=steps,
                start_time=start_time,
                run_id=run_id,
            )
        
        finally:
            # Rilascia sempre il lock
            self.concurrency.release(schedule_id)
            # FIX: chiudi le connessioni SQLite thread-local del worker Celery.
            # Stesso pattern di carica_task: senza questa pulizia, ogni
            # esecuzione lascia connessioni aperte nel thread.local del worker
            # Celery. Dopo molte esecuzioni il file descriptor si esaurisce o
            # il DB resta locked → SQLITE_BUSY.
            try:
                from app.core.intermediate.db import close_connection as _close_idb
                _close_idb()
            except Exception:
                pass
            try:
                from app.db.config_db_service import close_connection as _close_cfg
                _close_cfg()
            except Exception:
                pass
    
    # =========================================================================
    # PRE-CHECK CONNESSIONE
    # =========================================================================
    
    def _pre_check_connection(self, config: ScheduleConfig) -> bool:
        """
        Verifica che il database di destinazione sia raggiungibile
        PRIMA di ricalcolare la pipeline.
        """
        if not config.destination:
            return True
        
        try:
            connection_service = self._get_connection_service()
            connection = connection_service.get_connection(config.destination.connection_id)
            if not connection:
                logger.error(f"[GESTITO] Connessione {config.destination.connection_id} non trovata")
                return False
            
            # Test connessione
            result = connection_service.test_connection(config.destination.connection_id)
            if result and result.success:
                logger.info(f"Pre-check OK: {config.destination.connection_id}")
                return True
            else:
                logger.error(f"[GESTITO] Pre-check fallito: {config.destination.connection_id}")
                return False
                
        except Exception as e:
            logger.error(f"[CRASH] Errore pre-check: {e}", exc_info=True)
            return False
    
    # =========================================================================
    # ARCHIVIAZIONE OUTPUT
    # =========================================================================
    
    def _archive_output(
        self,
        project_id: str,
        schedule_id: str,
        output_file: str = ""
    ) -> Optional[str]:
        """
        Copia i file output in una cartella timestamped per evitare sovrascrittura.
        
        L'orchestrator salva in:
            results/{project_id}/*.parquet|csv|xlsx  (sovrascrive)
        
        Lo scheduler copia TUTTI i file in:
            results/{project_id}/runs/{schedule_short}/{YYYYMMDD_HHMMSS}/
        
        Se output_file è specificato, copia solo quello.
        Se vuoto, copia tutti i file nella cartella (esclusa sottocartella runs/).
        
        Returns:
            Path della cartella archiviata (relativo a RESULTS_DIR), o None se fallisce.
        """
        try:
            import shutil
            from app.config import settings

            project_output_dir = settings.RESULTS_DIR / project_id

            if not project_output_dir.exists():
                logger.warning(f"Cartella output non trovata: {project_output_dir}")
                return None
            
            # Trova i file da archiviare
            if output_file and (project_output_dir / output_file).is_file():
                files_to_copy = [project_output_dir / output_file]
            else:
                # Copia tutti i file nella root (escludi sottocartelle come runs/)
                files_to_copy = [
                    f for f in project_output_dir.iterdir()
                    if f.is_file()
                ]

            if not files_to_copy:
                logger.warning(f"Nessun file da archiviare in {project_output_dir}")
                return None

            # Cartella destinazione: results/{project_id}/runs/{schedule_short}/{timestamp}/
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            schedule_short = schedule_id.replace("schedule_", "")[:12]

            archive_dir = project_output_dir / "runs" / schedule_short / timestamp
            archive_dir.mkdir(parents=True, exist_ok=True)

            copied = []
            for src_file in files_to_copy:
                dest = archive_dir / src_file.name
                shutil.copy2(str(src_file), str(dest))
                copied.append(src_file.name)

            relative = str(archive_dir.relative_to(settings.RESULTS_DIR))
            logger.info(f"Output archiviato: {relative} ({len(copied)} file: {', '.join(copied)})")

            return relative

        except Exception as e:
            logger.warning(f"Errore archiviazione output: {e}", exc_info=True)
            return None
    
    # =========================================================================
    # ESECUZIONE CARICA
    # =========================================================================
    
    def _execute_carica(self, config: ScheduleConfig, pipeline_result: dict) -> dict:
        """
        Esegue il caricamento su database con la configurazione dello schedule.

        Recupera l'output della pipeline (dal result_store o dal path diretto),
        carica il DataFrame e lo scrive sulla tabella configurata nello schedule
        tramite CaricaExecutor (stessa logica dei task Celery CARICA).

        Differenza rispetto al canvas: usa connection_id/table_name dello schedule,
        non quelli del nodo CARICA nel canvas (skip_carica=True in execute_pipeline).
        """
        dest = config.destination
        logger.info(
            f"CARICA scheduler: {dest.write_mode} → "
            f"{dest.connection_id}/{dest.table_name}",
            op="carica"
        )

        try:
            # 1. Recupera il file output della pipeline
            from app.utils.result_store import get_result_as_tempfile
            from app.utils.dataframe import load_dataframe_from_file
            from pathlib import Path

            project_id = config.pipeline.project_id

            # Prima prova dal result_store (DB)
            file_path = get_result_as_tempfile(project_id)

            # Fallback: path diretto restituito dall'orchestrator
            if not file_path:
                raw_path = pipeline_result.get("output_path", "")
                if raw_path and Path(raw_path).exists():
                    file_path = Path(raw_path)

            if not file_path or not file_path.exists():
                logger.error(
                    f"File output non trovato per progetto {project_id}",
                    op="carica"
                )
                return {"success": False, "error": "File output pipeline non trovato"}

            df = load_dataframe_from_file(file_path)

            # Pulizia file temp estratto dal DB
            try:
                import os as _os
                if "urby_cached_" in str(file_path):
                    _os.unlink(str(file_path))
            except Exception:
                pass

            if df is None or df.empty:
                return {"success": False, "error": "DataFrame output vuoto o non caricabile"}

            # 2. Costruisci node_data compatibile con CaricaExecutor.execute()
            #    usando la configurazione della destinazione dello schedule
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

            write_result = carica_executor.execute(
                df=df,
                node_data=node_data,
                project_id=project_id,
                triggered_by="scheduler",
            )

            # 4. Traduci WriteResult nel formato atteso dall'execution_manager
            from app.core.carica.models import WriteStatus
            success = write_result.status in (WriteStatus.SUCCESS, WriteStatus.PARTIAL)

            if not success:
                error_msg = write_result.error_message or f"Scrittura fallita: {write_result.status.value}"
                logger.error(f"[GESTITO] CARICA scheduler fallito: {error_msg}", op="carica")
                return {"success": False, "error": error_msg}

            rows_written = write_result.rows_written or 0
            rows_inserted = write_result.rows_inserted or 0
            rows_updated = write_result.rows_updated or 0
            rows_skipped = (write_result.total_rows or 0) - rows_written

            logger.info(
                f"CARICA scheduler completato: {rows_written} righe → "
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
            return {"success": False, "error": str(e)}
    
    # =========================================================================
    # GESTIONE FALLIMENTI
    # =========================================================================
    
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
    ) -> dict:
        """
        Gestisce un fallimento: decide se fare retry o arrendersi.
        """
        total_duration = int((time.time() - start_time) * 1000)
        
        decision = self.policy.decide_after_failure(config, retry_attempt, error_message)
        
        # Scrivi log scheduler (filesystem)
        entry = self.log_service.create_log_entry(
            schedule_id=schedule_id,
            event=decision["event"],
            triggered_by=triggered_by,
            level=decision["log_level"],
            steps=steps,
            total_duration_ms=total_duration,
            error_message=error_message,
            pipeline_info={
                "project_id": config.pipeline.project_id,
                "project_name": config.pipeline.project_name,
                "project_type": config.pipeline.project_type
            },
            execution_info={
                "use_cache": config.execution.use_cache,
                "retry_attempt": retry_attempt
            }
        )
        log_path = self.log_service.write_log(entry)

        # --- Chiudi run su log.db ---
        if run_id:
            _steps_json = [
                {
                    "module":      s.step.lower(),
                    "status":      s.status,
                    "rows_out":    s.rows or 0,
                    "duration_ms": s.duration_ms or 0,
                }
                for s in steps
            ]
            # Costruisci debug_payload
            try:
                from app.utils.debug_payload_builder import DebugPayloadBuilder
                _debug_payload = DebugPayloadBuilder().build(
                    run_id=run_id,
                    project_id=config.pipeline.project_id,
                    steps=_steps_json,
                    failure={
                        "node_id":        None,
                        "node_type":      None,
                        "node_label":     None,
                        "module":         config.pipeline.project_type,
                        "error_type":     "SchedulerError",
                        "error_message":  error_message,
                        "traceback":      None,
                        "occurred_at_ms": total_duration,
                    },
                )
            except Exception:
                _debug_payload = None

            close_run(
                run_id=run_id,
                status="failure",
                duration_ms=total_duration,
                steps=_steps_json,
                error_module=config.pipeline.project_type,
                error_message=error_message,
                debug_payload=_debug_payload,
            )
        
        if decision["action"] == "retry":
            logger.warning(
                f"Schedule {schedule_id} fallito (tentativo {retry_attempt}), "
                f"retry tra {decision['wait_seconds']}s"
            )
            
            # Aggiorna runtime ma non lo stato finale (resta "running" durante retry)
            self.schedule_service.set_runtime_status(schedule_id, "running")
            
            # Schedula il retry come task Celery con countdown
            try:
                from workers.tasks.schedule_task import schedule_pipeline_task
                schedule_pipeline_task.apply_async(
                    args=[schedule_id],
                    kwargs={
                        "triggered_by": triggered_by,
                        "retry_attempt": retry_attempt + 1
                    },
                    countdown=decision["wait_seconds"]
                )
                logger.info(f"Retry schedulato: {schedule_id} tra {decision['wait_seconds']}s")
            except Exception as e:
                logger.error(f"[CRASH] Errore scheduling retry: {e}", exc_info=True)
                # Se non riusciamo a schedulare il retry, segna come fallito
                self.schedule_service.update_runtime(
                    schedule_id,
                    status="failed",
                    last_execution={
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": "failed",
                        "rows_written": 0,
                        "duration_ms": total_duration,
                        "log_file": log_path
                    }
                )
            
            return {
                "success": False,
                "status": "retry",
                "message": f"Retry {retry_attempt + 1} tra {decision['wait_seconds']}s",
                "log_file": log_path
            }
        
        else:
            # Retry esauriti — fallimento definitivo
            logger.error(f"[GESTITO] Schedule {schedule_id} fallito definitivamente dopo {retry_attempt} tentativi")
            
            self.schedule_service.update_runtime(
                schedule_id,
                status="failed",
                last_execution={
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "failed",
                    "rows_written": 0,
                    "duration_ms": total_duration,
                    "log_file": log_path
                }
            )
            
            return {
                "success": False,
                "status": "failed",
                "message": error_message,
                "log_file": log_path
            }


class ExecutionError(Exception):
    """Errore durante l'esecuzione di uno step specifico."""
    
    def __init__(self, message: str, step: str = ""):
        self.step = step
        super().__init__(message)