"""
Task Celery per esecuzione pipeline (ESTRAI / MANIPOLA / ARRICCHISCI).
"""

from pathlib import Path as _Path

import time as _time
import traceback as _traceback
import uuid as _uuid
from collections.abc import Iterator as _Iterator
from collections.abc import Mapping as _Mapping
from typing import Any, Optional

from app.shared.contracts.payloads import JsonObject
from app.utils.logger import get_logger, open_run, close_run, set_run_context, get_steps
from app.config import settings
from app.core.auth import async_user_recheck as async_user_recheck_module
from app.core.eseguibili import service as eseguibili_service
from app.core.intermediate import run_idempotency as run_idempotency_module
from app.core.intermediate import run_lifecycle as run_lifecycle_module
from app.core.projects import retention as project_retention
from app.core.projects import runtime_context as project_runtime_context
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.orchestrator.task_progress import TaskRevoked, report_task_progress
from app.utils import debug_payload_builder as debug_payload_builder_module
from app.shared.execution.runtime_identity import task_result, trigger_for_test_run

logger = get_logger("workers.pipeline_task")

_EXECUTABLE_OUTPUT_CHUNK_SIZE = 8 * 1024 * 1024

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


def _update_progress(
    task: object,
    percent: int,
    message: str,
    *,
    check_revocation: bool = True,
) -> None:
    # Progress scritto solo su PG via report_task_progress; il result backend
    # del task runtime non e' la fonte di verita' per la UI.
    report_task_progress(
        getattr(getattr(task, "request", None), "id", None),
        percent,
        message,
        name=getattr(task, "name", None),
        check_revocation=check_revocation,
    )


def _close_worker_connection(*, stage: str = "") -> None:
    """Rilascia la connessione thread-local anche sui ritorni pre-run."""
    try:
        run_lifecycle_module.close_worker_connection()
    except Exception as cleanup_exc:
        suffix = f" {stage}" if stage else ""
        logger.debug(
            f"close_connection pipeline fallito{suffix}: "
            f"{type(cleanup_exc).__name__}: {cleanup_exc}"
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
) -> PipelineExecutionContract | None:
    """Validate the optional control-plane to data-plane execution contract."""
    if raw_contract is None:
        return None
    return PipelineExecutionContract.from_payload(
        raw_contract,
        expected_project_id=expected_project_id,
    )


def _executable_subject(
    contract: PipelineExecutionContract | None,
) -> JsonObject | None:
    if not contract:
        return None
    return contract.executable_subject


def _cleanup_executable_inputs(
    contract: PipelineExecutionContract | None,
) -> None:
    if _executable_subject(contract) is None:
        return
    context = contract.runtime_context
    ids = context.get("cleanup_file_source_ids")
    if not isinstance(ids, list):
        return
    from app.core.files.service import file_source_service

    for file_source_id in ids:
        try:
            file_source_service.delete_file_source(str(file_source_id))
        except Exception as exc:
            logger.warning(
                f"Cleanup input eseguibile {file_source_id} fallito: {exc}"
            )


def _iter_executable_output_chunks(
    body: Any,
    *,
    chunk_size: int = _EXECUTABLE_OUTPUT_CHUNK_SIZE,
) -> _Iterator[bytes]:
    """Normalizza uno stream result in chunk binari senza materializzarlo."""
    if chunk_size <= 0:
        raise ValueError("chunk_size deve essere maggiore di zero")

    iterator: Any = None
    try:
        if isinstance(body, bytes):
            for offset in range(0, len(body), chunk_size):
                yield body[offset : offset + chunk_size]
            return
        if isinstance(body, (bytearray, memoryview)):
            for offset in range(0, len(body), chunk_size):
                yield bytes(body[offset : offset + chunk_size])
            return

        read = getattr(body, "read", None)
        if callable(read):
            while True:
                chunk = read(chunk_size)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise TypeError("Lo stream result deve produrre chunk binari")
                yield chunk if isinstance(chunk, bytes) else bytes(chunk)
            return

        try:
            iterator = iter(body)
        except TypeError as exc:
            raise TypeError(
                "Il body del result deve essere uno stream leggibile o un iterabile"
            ) from exc
        for chunk in iterator:
            if not chunk:
                continue
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise TypeError("Lo stream result deve produrre chunk binari")
            yield chunk if isinstance(chunk, bytes) else bytes(chunk)
    finally:
        closed_ids: set[int] = set()
        for candidate in (iterator, body):
            if candidate is None or id(candidate) in closed_ids:
                continue
            closed_ids.add(id(candidate))
            close = getattr(candidate, "close", None)
            if callable(close):
                close()


def _persist_executable_output(
    *,
    contract: PipelineExecutionContract,
    result: Any,
    task_id: str,
    group_id: str,
) -> JsonObject | None:
    subject = _executable_subject(contract)
    if subject is None:
        return None
    result_id = result.get("result_id")
    if not result_id:
        raise RuntimeError("Executor eseguibile completato senza result_id")

    from app.core.eseguibili import storage as eseguibili_storage
    from app.core.results.blob import open_result_stream

    stream = open_result_stream(
        str(result_id),
        expected_project_id=str(subject.get("id") or ""),
        expected_group_id=group_id,
    )
    if stream is None:
        raise RuntimeError("Output eseguibile non leggibile dal result store")
    body = stream["body"]

    filename = str(stream.get("filename") or f"{task_id}.bin")
    key = eseguibili_storage.save_chunks(
        task_id,
        filename,
        _iter_executable_output_chunks(body),
        group_id=group_id,
    )
    return {
        "result_id": str(result_id),
        "filename": filename,
        "storage_key": key,
    }


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
    contract = _normalize_execution_contract(
        execution_contract,
        expected_project_id=project_id,
    )
    subject = _executable_subject(contract)
    # Esecuzione di prova dall'editor (runtime_identity, regola H): riga di
    # cronologia marcata, risultato isolato come un Eseguibile, nessuna run
    # intermedia, nessun Carica, output del progetto intatto.
    test_run = contract.test_run_subject if contract is not None else None
    isolated = contract is not None and contract.isolated_output
    if test_run is not None:
        # La prova non ricalcola nulla a monte (gli endpoint lo rifiutano).
        force_recalculate = False

    def _outcome(payload: Any) -> JsonObject:
        # REGOLA: ogni esito del task passa di qui e porta il segno della
        # prova (permesso revocato, idempotenza, connessione persa,
        # fallimento, successo). Gli esiti in eccezione (raise) li scrive
        # la finalizzazione del worker con la stessa funzione
        # (task_progress.finalize_from_celery).
        return task_result(payload, test_run)
    # Run intermedie (cronologia dei nodi, idempotenza, retention): solo le
    # esecuzioni che pubblicano l'output del progetto.
    intermediate_run = not isolated
    has_external_run_id = run_id is not None

    if run_id is None:
        run_id = str(_uuid.uuid4())
    _run_start = _time.time()

    if subject is not None:
        _revoked = async_user_recheck_module.recheck_eseguibile_authorized(
            user_id=user_id,
            eseguibile_id=str(subject.get("id") or project_id),
            eseguibile_loader=eseguibili_service.get_eseguibile_for_access_check,
            task_id=task_id,
            run_id=run_id,
        )
    else:
        _revoked = async_user_recheck_module.recheck_user_authorized(
            user_id=user_id, project_id=project_id,
            task_id=task_id, run_id=run_id, task_name="PIPELINE",
            # Prova: anche il ruolo (runtime_identity.can_test_run).
            test_run=test_run is not None,
        )
    if _revoked is not None:
        _cleanup_executable_inputs(contract)
        if subject is not None:
            try:
                eseguibili_service.complete_eseguibile_run(
                    str(subject.get("run_id") or ""),
                    success=False,
                    error_message="Permesso di esecuzione revocato",
                )
            except Exception as run_exc:
                logger.warning(
                    f"Update run eseguibile revocata fallito: {run_exc}"
                )
        _revoked.update({"run_id": run_id, "task_id": task_id})
        _close_worker_connection(stage="post-permission-revoke")
        return _outcome(_revoked)

    logger.info(
        f"Task PIPELINE {task_id} avviato per progetto {project_id}",
        run_id=run_id, project_id=project_id,
    )
    try:
        _update_progress(self, 0, "Inizializzazione pipeline...")
    except TaskRevoked:
        # Il primo checkpoint precede sia i lookup di idempotenza sia
        # l'apertura delle run, ma gli input temporanei esistono gia'.
        _cleanup_executable_inputs(contract)
        if subject is not None:
            try:
                eseguibili_service.cancel_eseguibile_run(
                    str(subject.get("run_id") or ""),
                    error_message="Task annullato",
                )
            except Exception as run_exc:
                logger.warning(
                    f"Update run eseguibile annullata fallito: {run_exc}"
                )
        _close_worker_connection(stage="pre-run")
        raise

    # Authorization and the cancellation checkpoint must precede idempotency
    # lookups: revoked callers must not receive metadata from an earlier run.
    if subject is not None:
        if subject.get("run_id"):
            previous_run = eseguibili_service.get_eseguibile_run(
                str(subject["run_id"])
            )
            if previous_run and previous_run.get("status") == "success":
                output_metadata = previous_run.get("output_metadata") or {}
                if isinstance(output_metadata, str):
                    import json as _json

                    try:
                        output_metadata = _json.loads(output_metadata)
                    except ValueError:
                        output_metadata = {}
                output_file = (
                    output_metadata.get("filename")
                    if isinstance(output_metadata, _Mapping)
                    else None
                )
                _close_worker_connection(stage="post-eseguibile-idempotency")
                return _outcome({
                    "status": "skipped_idempotent",
                    "success": True,
                    "project_id": project_id,
                    "task_id": task_id,
                    "run_id": run_id,
                    "result_id": None,
                    "output_file": output_file,
                    "download_url": f"/api/eseguibili/{project_id}/download/{task_id}",
                    "is_eseguibile": True,
                })
    elif has_external_run_id and run_idempotency_module.is_already_completed(run_id):
        logger.info(
            f"[IDEMPOTENCY] Task PIPELINE {task_id}: run_id {run_id} "
            f"gia' completato con successo, skip."
        )
        result: JsonObject = {
            "status": "skipped_idempotent",
            "success": True,
            "message": f"Run {run_id} gia' completato",
            "run_id": run_id,
            "task_id": task_id,
        }
        _close_worker_connection(stage="post-idempotency")
        return _outcome(result)

    project_info = _get_project_info(project_id) if contract is None else None
    group_id = (
        contract.group_id
        if contract is not None
        else str((project_info or {}).get("group_id") or "")
    )
    module = (
        contract.project_type
        if contract is not None
        else str((project_info or {}).get("project_type") or "")
    )
    retention_n = (
        contract.retention_n
        if contract is not None
        else (project_info or {}).get("retention_n")
    )
    if retention_n is None and intermediate_run:
        retention_n = _resolve_intermediate_retention_n(project_id)

    log_run_opened = False
    try:
        if subject is None:
            open_run(
                run_id=run_id,
                project_id=project_id,
                group_id=group_id,
                module=module,
                triggered_by=(
                    trigger_for_test_run(str(test_run["username"]))
                    if test_run is not None else triggered_by
                ),
                user_id=user_id,
            )
            log_run_opened = True
            set_run_context(run_id=run_id, project_id=project_id)
            if intermediate_run:
                # L'apertura intermediate e' parte del contratto idempotente
                # per i progetti. Gli eseguibili usano ``eseguibile_runs``;
                # le prove non scrivono run intermedie.
                run_lifecycle_module.create_run(
                    run_id=run_id,
                    canvas_id=project_id,
                    group_id=group_id,
                    user_id=user_id,
                )
        else:
            # Evita di ereditare il contesto di una precedente run sullo
            # stesso thread worker senza creare log_runs per ``exec_*``.
            set_run_context(run_id="", project_id="")
    except BaseException:
        if log_run_opened:
            try:
                close_run(
                    run_id=run_id,
                    status="failure",
                    duration_ms=int((_time.time() - _run_start) * 1000),
                    error_module=module,
                    error_message="Inizializzazione run fallita",
                )
            except Exception:
                logger.warning("close_run pipeline fallito durante setup", exc_info=True)
        _cleanup_executable_inputs(contract)
        _close_worker_connection(stage="setup")
        raise

    try:
        _update_progress(self, 5, "Caricamento configurazione...")
        orchestrator = _build_orchestrator()
        executor_run_id = run_id if intermediate_run else None

        _update_progress(self, 10, "Esecuzione pipeline...")
        if contract is not None:
            result = orchestrator.execute_pipeline_from_contract(
                contract=contract,
                force_recalculate=force_recalculate,
                skip_carica=isolated,
                run_id=executor_run_id,
                user_id=user_id,
                intermediate_mode_override=intermediate_mode_override,
                output_task_id=task_id if isolated else None,
            )
        else:
            result = orchestrator.execute_pipeline(
                project_id,
                force_recalculate=force_recalculate,
                skip_carica=False,
                run_id=executor_run_id,
                user_id=user_id,
                intermediate_mode_override=intermediate_mode_override,
            )

        _cleanup_executable_inputs(contract)

        elapsed_ms = int((_time.time() - _run_start) * 1000)

        if not result.success:
            message = result.message or "Errore sconosciuto"
            if subject is not None:
                try:
                    eseguibili_service.complete_eseguibile_run(
                        str(subject.get("run_id") or ""),
                        success=False,
                        error_message=message,
                    )
                except Exception as run_exc:
                    logger.warning(f"Update run eseguibile fallito: {run_exc}")

            # Errori di connessione DB: fail immediato
            if _is_retryable_connection_message(message):
                _update_progress(
                    self,
                    0,
                    f"Connessione persa: {message}",
                    check_revocation=False,
                )
                logger.warning(
                    f"Task PIPELINE {task_id} — connessione DB chiusa: {message}",
                    run_id=run_id, project_id=project_id,
                )
                if subject is None:
                    close_run(
                        run_id=run_id,
                        status="failure",
                        duration_ms=elapsed_ms,
                        steps=get_steps(),
                        error_module=module,
                        error_message=f"Connessione al database persa: {message}",
                    )
                _update_progress(
                    self,
                    0,
                    f"Connessione al database persa. {message}",
                    check_revocation=False,
                )
                return _outcome({
                    "success": False,
                    "retryable": True,
                    "message": f"Connessione al database persa: {message}",
                    "project_id": project_id,
                    "run_id": run_id,
                    "task_id": task_id,
                })

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

            if subject is None:
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

            _update_progress(self, 0, message, check_revocation=False)
            return _outcome({
                "success": False,
                "message": message,
                "project_id": project_id,
                "run_id": run_id,
                "task_id": task_id,
            })

        rows_out = result.rows_count or 0
        # Ultimo checkpoint revocabile prima della finalizzazione degli output.
        # Dopo la persistenza non osserviamo piu' una cancellazione tardiva:
        # l'operazione e' ormai completata e deve terminare SUCCESS, evitando
        # output validi associati a un task marcato REVOKED.
        _update_progress(
            self,
            95,
            "Finalizzazione output...",
            check_revocation=False,
        )
        executable_output = None
        if isolated and contract is not None:
            direct_storage_key = result.get("storage_key")
            if test_run is not None and (result.get("result_id") or not direct_storage_key):
                # Una prova che ha scritto fuori dallo spazio dell'attivita'
                # non si dichiara riuscita (regola H: mai l'output del progetto).
                raise RuntimeError("Esecuzione di prova senza output isolato")
            if direct_storage_key:
                output_name = _Path(result.output_path or str(direct_storage_key)).name
                executable_output = {
                    "result_id": None,
                    "filename": output_name,
                    "storage_key": str(direct_storage_key),
                }
            else:
                # Compatibility for results produced before the direct output
                # adapter. New executable runs never enter this copy path.
                executable_output = _persist_executable_output(
                    contract=contract,
                    result=result,
                    task_id=task_id,
                    group_id=str(group_id),
                )
        if subject is not None:
            try:
                eseguibili_service.complete_eseguibile_run(
                    str(subject.get("run_id") or ""),
                    success=True,
                    output_metadata=executable_output,
                )
            except Exception as run_exc:
                logger.warning(f"Update run eseguibile fallito: {run_exc}")
        logger.info(
            f"Task PIPELINE {task_id} completato per {project_id}",
            run_id=run_id, project_id=project_id,
        )

        if subject is None:
            close_run(
                run_id=run_id,
                status="success",
                rows_out=rows_out,
                duration_ms=elapsed_ms,
                steps=get_steps(),
            )

        if intermediate_run:
            # Barriera idempotente sincrona per le run dei progetti.
            run_lifecycle_module.close_run(
                run_id=run_id, input_hash="", output_hash="",
                total_exec_time=_time.time() - _run_start, success=True,
                retention_n=retention_n,
            )

        output_path = result.output_path
        output_file = _Path(output_path).name if output_path else None
        extra = {"output_file": output_file} if output_file else {}
        if test_run is not None:
            # Scaricabile una volta da chi l'ha lanciata (/tasks/{id}/download).
            extra.update({
                "output_file": (executable_output or {}).get("filename") or output_file,
                "result_id": None,
            })
        elif executable_output is not None:
            extra.update(
                {
                    "output_file": executable_output["filename"],
                    "download_url": (
                        f"/api/eseguibili/{project_id}/download/{task_id}"
                    ),
                    "is_eseguibile": True,
                }
            )
        return _outcome({
            "success": True,
            "project_id": project_id,
            "run_id": run_id,
            "task_id": task_id,
            **{k: v for k, v in result.items() if k not in ("success", "output_path")},
            **extra,
        })

    except TaskRevoked:
        _cleanup_executable_inputs(contract)
        if subject is not None:
            try:
                eseguibili_service.cancel_eseguibile_run(
                    str(subject.get("run_id") or ""),
                    error_message="Task annullato",
                )
            except Exception as run_exc:
                logger.warning(f"Update run eseguibile annullata fallito: {run_exc}")
        elapsed_ms = int((_time.time() - _run_start) * 1000)
        if subject is None:
            try:
                close_run(
                    run_id=run_id,
                    status="revoked",
                    duration_ms=elapsed_ms,
                    steps=get_steps(),
                    error_module=module,
                    error_message="Task annullato",
                )
            except Exception as close_exc:
                logger.warning(
                    "close_run log fallito post-revoke: "
                    f"{type(close_exc).__name__}: {close_exc}"
                )
        if intermediate_run:
            try:
                run_lifecycle_module.close_run(
                    run_id=run_id,
                    input_hash="",
                    output_hash="",
                    total_exec_time=_time.time() - _run_start,
                    success=False,
                    retention_n=retention_n,
                )
            except Exception as close_exc:
                logger.debug(
                    "close_run intermediate fallito post-revoke: "
                    f"{type(close_exc).__name__}: {close_exc}"
                )
        raise

    except Exception as e:
        _cleanup_executable_inputs(contract)
        if subject is not None:
            try:
                eseguibili_service.complete_eseguibile_run(
                    str(subject.get("run_id") or ""),
                    success=False,
                    error_message=str(e),
                )
            except Exception as run_exc:
                logger.warning(f"Update run eseguibile fallito: {run_exc}")
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

        if subject is None:
            close_run(
                run_id=run_id,
                status="failure",
                duration_ms=elapsed_ms,
                steps=get_steps(),
                error_module=module,
                error_message=str(e),
                debug_payload=debug_payload,
            )

        if intermediate_run:
            # Chiudi run intermediate storage tramite boundary di lifecycle.
            try:
                run_lifecycle_module.close_run(
                    run_id=run_id, input_hash="", output_hash="",
                    total_exec_time=_time.time() - _run_start, success=False,
                    retention_n=retention_n,
                )
            except Exception as close_error:
                logger.debug(
                    "close_run intermediate fallito post-crash: "
                    f"{type(close_error).__name__}: {close_error}"
                )

        raise

    finally:
        # Anche successo, failure gestita e revoca devono rilasciare la
        # connessione thread-local (inline mode riusa il thread worker).
        _close_worker_connection()


def preview_pipeline_task(
    self,
    project_id: str,
    limit: int = settings.MAX_PREVIEW_ROWS,
    user_id: Optional[str] = None,
    execution_contract: Optional[JsonObject] = None,
) -> JsonObject:
    """Task asincrono per preview pipeline."""
    task_id = self.request.id
    contract = _normalize_execution_contract(
        execution_contract,
        expected_project_id=project_id,
    )
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
        if contract is not None:
            result = orchestrator.execute_preview_from_contract(
                contract,
                limit=limit,
            )
        else:
            # Compatibility for preview messages queued before contracts.
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
                if isinstance(value, _np.integer):
                    return int(value)
                if isinstance(value, _np.floating):
                    return float(value)
                if isinstance(value, _np.ndarray):
                    return value.tolist()
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
        # control-plane reference.
        from app.core.orchestrator.preview_payload_store import (
            persist_preview_payload,
        )

        preview_group_id = (
            contract.group_id
            if contract is not None
            else str(_get_project_info(project_id).get("group_id") or "")
        )
        if not preview_group_id:
            raise RuntimeError("group_id progetto non determinabile per preview")

        return persist_preview_payload(
            result,
            group_id=preview_group_id,
            project_id=project_id,
            task_id=task_id,
        )

    except Exception as e:
        logger.error(f"[CRASH] Task PREVIEW {task_id} fallito: {e}", exc_info=True)
        raise
    finally:
        _close_worker_connection(stage="preview")


# Nomi registrati dei task.
# Soddisfano il protocol DelayTask.name (dispatch by-name di submit_task) e
# combaciano con gli actor Dramatiq in workers/dramatiq_app.py.
execute_pipeline_task.name = "pipeline.execute"
preview_pipeline_task.name = "pipeline.preview"
