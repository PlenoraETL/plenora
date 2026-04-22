"""
Task Celery per esecuzione pipeline (ESTRAI / MANIPOLA / ARRICCHISCI).
"""

import time as _time
import traceback as _traceback
import uuid as _uuid
from typing import Any, Dict, Optional

from workers.celery_app import celery_app, BaseTaskWithRetry
from app.utils.logger import get_logger, open_run, close_run, set_run_context, get_steps
from app.config import settings

logger = get_logger("workers.pipeline_task")


def _update_progress(task, percent: int, message: str):
    task.update_state(
        state="PROGRESS",
        meta={"percent": percent, "message": message, "type": "pipeline"},
    )


def _build_orchestrator():
    """Istanzia OrchestratorService con tutte le dipendenze.

    IMPORTANTE: passa anche `features_service` — senza, i progetti ESTRAI
    con nodi ArcGIS Feature Service non riescono a risolvere il layer al
    task-time (mentre in modalita' sincrona via app/main.py funzionano).
    """
    from app.services.project_service import ProjectService
    from app.services.connection_service import ConnectionService
    from app.core.estrai.query_generator import QueryGenerator
    from app.core.features.features_service import FeaturesService
    from app.db.query_executor import create_executor
    from app.core.orchestrator.service import OrchestratorService
    from app.utils.dataframe import save_dataframe
    from app.config import settings

    project_service = ProjectService()
    connection_service = ConnectionService()
    generator = QueryGenerator()
    executor = create_executor(save_function=save_dataframe)
    features_service = FeaturesService(save_function=save_dataframe)

    return OrchestratorService(
        project_service=project_service,
        executor=executor,
        generator=generator,
        connection_service=connection_service,
        features_service=features_service,
    )


def _get_project_info(project_id: str) -> dict:
    """Legge metadata progetto da config.db — non bloccante."""
    try:
        import app.db.config_db_service as cfg_db
        return cfg_db.get_project(project_id) or {}
    except Exception:
        return {}


@celery_app.task(
    bind=True,
    base=BaseTaskWithRetry,
    name="pipeline.execute",
    max_retries=0,
    soft_time_limit=settings.CELERY_TASK_TIMEOUT,
    time_limit=settings.CELERY_TASK_HARD_LIMIT,
)
def execute_pipeline_task(
    self,
    project_id: str,
    force_recalculate: bool = False,
    triggered_by: str = "manual",
    run_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Task asincrono per eseguire la pipeline completa di un progetto.

    Idempotency (C4 round 4): se `run_id` viene passato dall'esterno
    (retry-handler UI, scheduler, replay da backup) ed esiste gia' in
    intermediate.runs con status='completed', il task SKIPPA
    l'esecuzione e ritorna un result cached. Copre lo scenario
    "admin rilancia pipeline intera": con run_id fresh, la pipeline
    sarebbe ri-eseguita da zero aggirando l'idempotency dei singoli
    sotto-task (carica/enrichment). Con un run_id stabile, il
    re-invio dell'intera pipeline diventa no-op.

    Il run_id passato viene propagato all'orchestrator via
    `execute_pipeline(run_id=...)`, quindi l'intera run usa una
    chiave stabile anziche' UUID volatile.

    Args:
        project_id: ID del progetto da eseguire.
        force_recalculate: Se True, ignora cache e ricalcola.
        triggered_by: "manual" / "cron" / "retry".
        run_id: Chiave di idempotency opzionale. Se None → generato
            fresh (behavior legacy, no idempotency).
    """
    task_id = self.request.id

    # C4 round 4: idempotency guard. Se run_id e' passato ed e' gia'
    # completato, ritorna subito senza ri-eseguire la pipeline.
    if run_id is not None:
        from app.shared.idempotency import is_already_completed
        if is_already_completed(run_id):
            logger.info(
                f"[IDEMPOTENCY] Task PIPELINE {task_id}: run_id {run_id} "
                f"gia' completato con successo, skip."
            )
            return {
                "status": "skipped_idempotent",
                "success": True,
                "message": f"Run {run_id} gia' completato",
                "run_id": run_id,
                "task_id": task_id,
            }

    if run_id is None:
        run_id = str(_uuid.uuid4())
    _run_start = _time.time()

    # --- N9 (2026-04-17): re-check permessi user al pickup del worker ---
    # Helper centralizzato in app.shared.async_user_recheck — single source
    # of truth condivisa fra tutti i worker Celery (pipeline, preview,
    # carica, enrichment). user_id=None -> skip legittimo (cron/scheduled).
    from app.shared.async_user_recheck import recheck_user_authorized
    _revoked = recheck_user_authorized(
        user_id=user_id, project_id=project_id,
        task_id=task_id, run_id=run_id, task_name="PIPELINE",
    )
    if _revoked is not None:
        _revoked.update({"run_id": run_id, "task_id": task_id})
        return _revoked

    # Raccolta step in itinere — popolata dagli executor tramite append_step()
    # Non usiamo una lista locale: get_steps() legge dal RunContext thread-local

    logger.info(
        f"Task PIPELINE {task_id} avviato per progetto {project_id}",
        run_id=run_id, project_id=project_id,
    )
    _update_progress(self, 0, "Inizializzazione pipeline...")

    # --- Leggi metadata progetto ---
    project_info = _get_project_info(project_id)
    group_id = project_info.get("group_id", "")
    module   = project_info.get("project_type", "")

    # --- Apri run su log.db (N9: user_id persistito per audit) ---
    open_run(
        run_id=run_id,
        project_id=project_id,
        group_id=group_id,
        module=module,
        triggered_by=triggered_by,
        user_id=user_id,
    )

    # --- Imposta contesto thread-local (run_id + steps collector) ---
    set_run_context(run_id=run_id, project_id=project_id)

    # --- Apri run su intermediate.db (non bloccante) ---
    try:
        from app.core.intermediate import db as idb
        idb.create_run(run_id=run_id, canvas_id=project_id, group_id=group_id, user_id=user_id)
    except Exception as _ie:
        logger.warning(f"Intermediate: apertura run fallita (non bloccante): {_ie}")

    try:
        _update_progress(self, 5, "Caricamento configurazione...")
        orchestrator = _build_orchestrator()

        _update_progress(self, 10, "Esecuzione pipeline...")
        result = orchestrator.execute_pipeline(
            project_id,
            force_recalculate=force_recalculate,
            skip_carica=False,
            run_id=run_id,
        )

        elapsed_ms = int((_time.time() - _run_start) * 1000)

        if not result.success:
            message = result.message or "Errore sconosciuto"

            # Errori di connessione DB: fail immediato
            _CONNECTION_ERRORS = (
                "Lost connection", "server has gone away", "Connection refused",
                "Can't connect", "connection was forcibly closed", "broken pipe",
                "Connection reset", "SSL connection", "query was cancelled",
            )
            if any(err.lower() in message.lower() for err in _CONNECTION_ERRORS):
                _update_progress(self, 0, f"Connessione persa: {message}")
                logger.warning(
                    f"Task PIPELINE {task_id} — connessione DB chiusa: {message}",
                    run_id=run_id, project_id=project_id,
                )
                close_run(
                    run_id=run_id,
                    status="failure",
                    duration_ms=elapsed_ms,
                    steps=get_steps(),
                    error_module=module,
                    error_message=f"Connessione al database persa: {message}",
                )
                self.update_state(
                    state="FAILURE",
                    meta={
                        "exc_type": "ConnectionError",
                        "exc_message": message,
                        "percent": 0,
                        "message": f"Connessione al database persa. {message}",
                    },
                )
                return {"success": False, "message": f"Connessione al database persa: {message}"}

            # Errore logico — costruisce debug_payload
            if result.get("user_error"):
                logger.warning(
                    f"[GESTITO] Task PIPELINE {task_id} fallito (input utente): {message[:200]}",
                    run_id=run_id, project_id=project_id,
                )
            else:
                logger.error(
                    f"[GESTITO] Task PIPELINE {task_id} fallito: {message}",
                    run_id=run_id, project_id=project_id,
                )

            error_node_id  = result.get("error_node_id")
            error_node_type = result.get("error_node_type")
            error_node_label = result.get("error_node_label")

            try:
                from app.utils.debug_payload_builder import DebugPayloadBuilder
                debug_payload = DebugPayloadBuilder().build(
                    run_id=run_id,
                    project_id=project_id,
                    steps=get_steps(),
                    failure={
                        "node_id":        error_node_id,
                        "node_type":      error_node_type,
                        "node_label":     error_node_label,
                        "module":         module,
                        "error_type":     "PipelineError",
                        "error_message":  message,
                        "traceback":      None,
                        "occurred_at_ms": elapsed_ms,
                    },
                )
            except Exception as _pe:
                logger.warning(f"DebugPayloadBuilder fallito (non bloccante): {_pe}")
                debug_payload = None

            close_run(
                run_id=run_id,
                status="failure",
                duration_ms=elapsed_ms,
                steps=get_steps(),
                error_node_id=error_node_id,
                error_module=module,
                error_message=message,
                debug_payload=debug_payload,
            )

            _update_progress(self, 0, message)
            return {"success": False, "message": message}

        # --- Successo ---
        rows_out = result.rows_count or 0
        _update_progress(self, 100, "Pipeline completata")
        logger.info(
            f"Task PIPELINE {task_id} completato per {project_id}",
            run_id=run_id, project_id=project_id,
        )

        close_run(
            run_id=run_id,
            status="success",
            rows_out=rows_out,
            duration_ms=elapsed_ms,
            steps=get_steps(),
        )

        # Chiudi intermediate.db
        try:
            from app.core.intermediate.writer import close_run as idb_close_run
            idb_close_run(
                run_id=run_id, input_hash="", output_hash="",
                total_exec_time=_time.time() - _run_start, success=True,
            )
        except Exception as _ce:
            logger.warning(f"Intermediate: chiusura run fallita: {_ce}")

        from pathlib import Path as _Path
        output_path = result.output_path
        output_file = _Path(output_path).name if output_path else None
        extra = {"output_file": output_file} if output_file else {}
        return {
            "success": True,
            "project_id": project_id,
            **{k: v for k, v in result.items() if k not in ("success", "output_path")},
            **extra,
        }

    except Exception as e:
        elapsed_ms = int((_time.time() - _run_start) * 1000)
        tb_str = _traceback.format_exc()

        logger.error(
            f"[CRASH] Task PIPELINE {task_id} fallito: {e}",
            run_id=run_id, project_id=project_id, exc_info=True,
        )

        # Costruisce debug_payload con traceback completo
        try:
            from app.utils.debug_payload_builder import DebugPayloadBuilder
            debug_payload = DebugPayloadBuilder().build(
                run_id=run_id,
                project_id=project_id,
                steps=get_steps(),
                failure={
                    "node_id":        None,
                    "node_type":      None,
                    "node_label":     None,
                    "module":         module,
                    "error_type":     type(e).__name__,
                    "error_message":  str(e),
                    "traceback":      tb_str,
                    "occurred_at_ms": elapsed_ms,
                },
            )
        except Exception as _pe:
            logger.warning(f"DebugPayloadBuilder fallito (non bloccante): {_pe}")
            debug_payload = None

        close_run(
            run_id=run_id,
            status="failure",
            duration_ms=elapsed_ms,
            steps=get_steps(),
            error_module=module,
            error_message=str(e),
            debug_payload=debug_payload,
        )

        # Chiudi intermediate.db
        try:
            from app.core.intermediate.writer import close_run as idb_close_run
            idb_close_run(
                run_id=run_id, input_hash="", output_hash="",
                total_exec_time=_time.time() - _run_start, success=False,
            )
        except Exception as e:
            logger.debug(f"close_run intermediate fallito post-crash: {type(e).__name__}: {e}")

        # Chiudi connessione SQLite del thread Celery: senza questo,
        # ogni task lascia aperto un handle che si accumula nel pool.
        try:
            from app.core.intermediate.db import close_connection
            close_connection()
        except Exception as e:
            logger.debug(f"close_connection intermediate fallito post-crash: {type(e).__name__}: {e}")

        raise


@celery_app.task(
    bind=True,
    base=BaseTaskWithRetry,
    name="pipeline.preview",
    max_retries=0,
    soft_time_limit=settings.CELERY_PREVIEW_TIMEOUT,
    time_limit=settings.CELERY_PREVIEW_HARD_LIMIT,
)
def preview_pipeline_task(
    self,
    project_id: str,
    limit: int = settings.MAX_PREVIEW_ROWS,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Task asincrono per preview pipeline."""
    task_id = self.request.id
    logger.info(f"Task PREVIEW {task_id} avviato per progetto {project_id}")
    _update_progress(self, 0, "Inizializzazione preview...")

    # N9: re-check permessi al pickup (helper condiviso)
    from app.shared.async_user_recheck import recheck_user_authorized
    _revoked = recheck_user_authorized(
        user_id=user_id, project_id=project_id,
        task_id=task_id, task_name="PREVIEW",
    )
    if _revoked is not None:
        _revoked.update({"task_id": task_id, "project_id": project_id})
        return _revoked

    try:
        _update_progress(self, 10, "Esecuzione preview...")
        orchestrator = _build_orchestrator()
        result = orchestrator.execute_preview(project_id, limit=limit)

        if not result.get("success"):
            message = result.get("message", "Errore preview")
            if result.get("user_error"):
                logger.warning(f"[GESTITO] Task PREVIEW {task_id} fallito (input utente): {message[:200]}")
            else:
                logger.error(f"[GESTITO] Task PREVIEW {task_id} fallito: {message}")
            return {"success": False, "task_id": task_id, "project_id": project_id, "error": message}

        _update_progress(self, 100, "Preview completata")

        try:
            import numpy as _np
            def _to_serializable(obj):
                if isinstance(obj, _np.integer): return int(obj)
                if isinstance(obj, _np.floating): return float(obj)
                if isinstance(obj, _np.ndarray): return obj.tolist()
                if isinstance(obj, dict): return {k: _to_serializable(v) for k, v in obj.items()}
                if isinstance(obj, (list, tuple)): return [_to_serializable(v) for v in obj]
                return obj
            result = _to_serializable(result)
        except ImportError:
            pass

        return result

    except Exception as e:
        logger.error(f"[CRASH] Task PREVIEW {task_id} fallito: {e}", exc_info=True)
        raise

