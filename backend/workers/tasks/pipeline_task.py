"""
Task Celery per esecuzione pipeline (ESTRAI / MANIPOLA / ARRICCHISCI).
"""

from pathlib import Path as _Path

import time as _time
import traceback as _traceback
import uuid as _uuid
from collections.abc import Mapping as _Mapping
from typing import Any, Optional

from app.shared.contracts.payloads import JsonObject
from app.utils.logger import get_logger, open_run, close_run, set_run_context, get_steps
from app.config import settings
from app.core.auth import async_user_recheck as async_user_recheck_module
from app.core.intermediate import run_idempotency as run_idempotency_module
from app.core.intermediate import run_lifecycle as run_lifecycle_module
from app.core.projects import retention as project_retention
from app.core.projects import runtime_context as project_runtime_context
from app.core.orchestrator.task_progress import report_task_progress
from app.utils import debug_payload_builder as debug_payload_builder_module

logger = get_logger("workers.pipeline_task")

_RETRYABLE_CONNECTION_MESSAGES = (
    "lost connection",
    "server has gone away",
    "connection refused",
    "can't connect",
    "connection was forcibly closed",
    "broken pipe",
    "connection reset",
    "connection timed out",
    "server closed the connection unexpectedly",
    "ssl connection has been closed unexpectedly",
)


def _is_retryable_connection_message(message: str) -> bool:
    normalized = str(message or "").lower()
    return any(fragment in normalized for fragment in _RETRYABLE_CONNECTION_MESSAGES)


def _update_progress(task: object, percent: int, message: str) -> None:
    # Progress scritto solo su PG via report_task_progress; il result backend
    # del task runtime non e' la fonte di verita' per la UI.
    report_task_progress(
        getattr(getattr(task, "request", None), "id", None),
        percent,
        message,
        name=getattr(task, "name", None),
    )


def _build_orchestrator() -> Any:
    """Istanzia OrchestratorService con tutte le dipendenze.

    IMPORTANTE: passa anche `features_service` — senza, i progetti ESTRAI
    con nodi ArcGIS Feature Service non riescono a risolvere il layer al
    task-time (mentre in modalita' sincrona via app/main.py funzionano).
    """
    from app.runtime.services import build_orchestrator_stack

    return build_orchestrator_stack().orchestrator


def _get_project_info(project_id: str) -> JsonObject:
    """Legge metadata progetto tramite il boundary runtime del dominio projects."""
    try:
        context = project_runtime_context.get_project_runtime_context(project_id)
        if context is None:
            return {}
        return {
            "group_id": context.group_id,
            "project_type": context.project_type,
            "retention_n": context.retention_n,
        }
    except Exception as e:
        logger.debug(f"project info lookup fallito: {type(e).__name__}: {e}")
        return {}


def _resolve_intermediate_retention_n(project_id: str) -> int | None:
    try:
        return project_retention.resolve_runs_retention_n(
            project_id,
            default=run_lifecycle_module.get_default_runs_retention(),
        )
    except Exception as e:
        logger.debug(f"retention policy lookup fallito: {type(e).__name__}: {e}")
        return None


def _normalize_execution_contract(
    raw_contract: Any,
    *,
    expected_project_id: str,
) -> JsonObject | None:
    """Validate the optional control-plane to data-plane execution contract."""
    if raw_contract is None:
        return None
    if not isinstance(raw_contract, _Mapping):
        raise ValueError("execution_contract must be a JSON object")

    project_id = str(raw_contract.get("project_id") or expected_project_id)
    if project_id != expected_project_id:
        raise ValueError("execution_contract project_id mismatch")

    project_type = raw_contract.get("project_type")
    canvas = raw_contract.get("canvas")
    if not isinstance(project_type, str) or not project_type:
        raise ValueError("execution_contract.project_type is required")
    if not isinstance(canvas, _Mapping):
        raise ValueError("execution_contract.canvas is required")

    contract: JsonObject = {
        "schema_version": raw_contract.get("schema_version", 1),
        "project_id": project_id,
        "project_type": project_type,
        "canvas": dict(canvas),
        "group_id": str(raw_contract.get("group_id") or ""),
        "artifact_refs": raw_contract.get("artifact_refs") or {},
    }
    output_contract = raw_contract.get("output_contract")
    if isinstance(output_contract, _Mapping):
        contract["output_contract"] = dict(output_contract)
    retention_n = raw_contract.get("retention_n")
    if retention_n is not None:
        contract["retention_n"] = retention_n
    return contract


# Corpo task come funzione pura.
# `self` e' il self-shim dell'actor Dramatiq (request.id/name). Fail-fast
# con retry soltanto per errori di connessione esplicitamente classificati;
# run_id stabile + idempotency evitano doppie finalizzazioni.
def execute_pipeline_task(
    self,
    project_id: str,
    force_recalculate: bool = False,
    triggered_by: str = "manual",
    run_id: Optional[str] = None,
    user_id: Optional[str] = None,
    intermediate_mode_override: Optional[str] = None,
    execution_contract: Optional[JsonObject] = None,
) -> JsonObject:
    """Task asincrono per eseguire la pipeline completa di un progetto.

    Idempotency: se `run_id` viene passato dall'esterno
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
        run_id: Chiave di idempotency opzionale. Se None, viene generato
            fresh e non si applica idempotency cross-run.
    """
    task_id = self.request.id

    if run_id is not None:
        if run_idempotency_module.is_already_completed(run_id):
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

    _revoked = async_user_recheck_module.recheck_user_authorized(
        user_id=user_id, project_id=project_id,
        task_id=task_id, run_id=run_id, task_name="PIPELINE",
    )
    if _revoked is not None:
        _revoked.update({"run_id": run_id, "task_id": task_id})
        return _revoked

    logger.info(
        f"Task PIPELINE {task_id} avviato per progetto {project_id}",
        run_id=run_id, project_id=project_id,
    )
    _update_progress(self, 0, "Inizializzazione pipeline...")

    contract = _normalize_execution_contract(
        execution_contract,
        expected_project_id=project_id,
    )
    project_info = (
        contract if contract is not None else _get_project_info(project_id)
    )
    group_id = project_info.get("group_id", "")
    module   = project_info.get("project_type", "")
    retention_n = project_info.get("retention_n")
    if retention_n is None:
        retention_n = _resolve_intermediate_retention_n(project_id)

    open_run(
        run_id=run_id,
        project_id=project_id,
        group_id=group_id,
        module=module,
        triggered_by=triggered_by,
        user_id=user_id,
    )

    set_run_context(run_id=run_id, project_id=project_id)

    # Apri run su intermediate storage tramite boundary di lifecycle.
    try:
        run_lifecycle_module.create_run(
            run_id=run_id,
            canvas_id=project_id,
            group_id=group_id,
            user_id=user_id,
        )
    except Exception as _ie:
        logger.warning(f"Intermediate: apertura run fallita (non bloccante): {_ie}")

    try:
        _update_progress(self, 5, "Caricamento configurazione...")
        orchestrator = _build_orchestrator()

        _update_progress(self, 10, "Esecuzione pipeline...")
        if contract is not None:
            result = orchestrator.execute_pipeline_from_contract(
                project_id=project_id,
                project_type=str(contract["project_type"]),
                canvas=contract["canvas"],
                force_recalculate=force_recalculate,
                skip_carica=False,
                run_id=run_id,
                user_id=user_id,
                intermediate_mode_override=intermediate_mode_override,
            )
        else:
            result = orchestrator.execute_pipeline(
                project_id,
                force_recalculate=force_recalculate,
                skip_carica=False,
                run_id=run_id,
                user_id=user_id,
                intermediate_mode_override=intermediate_mode_override,
            )

        elapsed_ms = int((_time.time() - _run_start) * 1000)

        if not result.success:
            message = result.message or "Errore sconosciuto"

            # Errori di connessione DB: fail immediato
            if _is_retryable_connection_message(message):
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
                _update_progress(self, 0, f"Connessione al database persa. {message}")
                return {
                    "success": False,
                    "retryable": True,
                    "message": f"Connessione al database persa: {message}",
                    "project_id": project_id,
                    "run_id": run_id,
                    "task_id": task_id,
                }

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
                debug_payload = debug_payload_builder_module.DebugPayloadBuilder().build(
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
            return {
                "success": False,
                "message": message,
                "project_id": project_id,
                "run_id": run_id,
                "task_id": task_id,
            }

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

        # Chiudi run intermediate storage tramite boundary di lifecycle.
        try:
            run_lifecycle_module.close_run(
                run_id=run_id, input_hash="", output_hash="",
                total_exec_time=_time.time() - _run_start, success=True,
                retention_n=retention_n,
            )
        except Exception as _ce:
            logger.warning(f"Intermediate: chiusura run fallita: {_ce}")

        output_path = result.output_path
        output_file = _Path(output_path).name if output_path else None
        extra = {"output_file": output_file} if output_file else {}
        return {
            "success": True,
            "project_id": project_id,
            "run_id": run_id,
            "task_id": task_id,
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
            debug_payload = debug_payload_builder_module.DebugPayloadBuilder().build(
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

        # Chiudi run intermediate storage tramite boundary di lifecycle.
        try:
            run_lifecycle_module.close_run(
                run_id=run_id, input_hash="", output_hash="",
                total_exec_time=_time.time() - _run_start, success=False,
                retention_n=retention_n,
            )
        except Exception as e:
            logger.debug(f"close_run intermediate fallito post-crash: {type(e).__name__}: {e}")

        # Chiudi connessione PostgreSQL del thread Celery: senza questo
        # ogni task lascia aperto un handle che si accumula nel pool
        # Pattern ereditato dalla migrazione SQLite -> PG.
        try:
            run_lifecycle_module.close_worker_connection()
        except Exception as e:
            logger.debug(f"close_connection intermediate fallito post-crash: {type(e).__name__}: {e}")

        raise


def preview_pipeline_task(
    self,
    project_id: str,
    limit: int = settings.MAX_PREVIEW_ROWS,
    user_id: Optional[str] = None,
) -> JsonObject:
    """Task asincrono per preview pipeline."""
    task_id = self.request.id
    logger.info(f"Task PREVIEW {task_id} avviato per progetto {project_id}")
    _update_progress(self, 0, "Inizializzazione preview...")

    # Re-check permessi al pickup tramite helper condiviso.

    _revoked = async_user_recheck_module.recheck_user_authorized(
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
            def _to_serializable(value: Any) -> Any:
                if isinstance(value, _np.integer): return int(value)
                if isinstance(value, _np.floating): return float(value)
                if isinstance(value, _np.ndarray): return value.tolist()
                if isinstance(value, dict):
                    return {
                        key: _to_serializable(nested_value)
                        for key, nested_value in value.items()
                    }
                if isinstance(value, (list, tuple)):
                    return [_to_serializable(nested_value) for nested_value in value]
                return value
            result = _to_serializable(result)
        except ImportError:
            pass

        # In the object-storage-only data plane the tabular preview must not
        # cross the subprocess pipe and end up in task_results JSONB. Persist
        # it here, inside the disposable child, and return only a small
        # control-plane reference. Legacy db/hybrid modes remain readable for
        # transition tests and old deployments, but supported installs use
        # object_storage.
        if str(settings.get("STORAGE_POLICY", "object_storage")).lower() == "object_storage":
            from app.core.orchestrator.preview_payload_store import (
                persist_preview_payload,
            )

            return persist_preview_payload(
                result,
                project_id=project_id,
                task_id=task_id,
            )

        return result

    except Exception as e:
        logger.error(f"[CRASH] Task PREVIEW {task_id} fallito: {e}", exc_info=True)
        raise


# Nomi registrati dei task.
# Soddisfano il protocol DelayTask.name (dispatch by-name di submit_task) e
# combaciano con gli actor Dramatiq in workers/dramatiq_app.py.
execute_pipeline_task.name = "pipeline.execute"
preview_pipeline_task.name = "pipeline.preview"
