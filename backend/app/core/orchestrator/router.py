"""
Router API per il modulo Orchestrator.

Esecuzione pipeline, preview, download risultati e gestione task Celery.
"""

import time
from collections.abc import Mapping
from typing import Literal, Optional, List, Iterator, TypedDict

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, field_validator

from app.config import settings
from app.utils.logger import get_logger
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from app.core.projects.canvas_models import ExecuteRequest
from app.core.orchestrator.task_service import (
    TaskType,
)
from app.core.orchestrator.execution_contract import (
    build_pipeline_execution_contract,
)
from app.core.orchestrator.router_mappers import (
    sanitize_task_status_payload,
    task_info_to_dict as _task_info_to_dict,
    task_result_project_id as _task_result_project_id,
    task_result_to_dict as _task_result_to_dict,
)
from app.core.orchestrator import access_control as _access_control
from app.core.orchestrator.carica_payload_validation import (
    validate_rest_carica_task_payload,
)
from app.core.orchestrator import download_response as _download_response
from app.utils.router_utils import require_service
from app.observability.audit_action import audit_action
from app.core.auth.dependencies import get_current_user, require_not_esecutore
from app.shared.contracts.auth import CurrentUser
from app.shared.contracts.payloads import JsonObject
from app.core.auth.ownership import assert_group_access
from app.shared.execution.canvas_graph import CanvasGraph
from app.shared.execution.canvas_types_enricher import enrich_canvas_types_from_metadata
from app.core.eseguibili import output_store as _eseg_output_store
from app.core.results import legacy_file_store

def _mime_for_filename(filename: str) -> str:
    """Ritorna il Content-Type corretto basandosi sull'estensione del file."""
    return _download_response.mime_for_filename(filename)


logger = get_logger(__name__)

def _validate_rest_carica_task_payload(carica_node_data: JsonObject) -> None:
    try:
        validate_rest_carica_task_payload(carica_node_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _assert_project_group(project_id: str, current_user: CurrentUser) -> None:
    """Verifica che il progetto appartenga al gruppo dell'utente corrente.

    Helper centralizzato per gli endpoint orchestrator che operano su
    project_id. Solleva 404 se il progetto non esiste, 403 se appartiene
    a un altro gruppo. Superadmin bypass (assert_group_access lo gestisce).
    """
    _access_control.assert_project_group(project_id, current_user)


def _assert_task_group(task_id: str, current_user: CurrentUser) -> None:
    """Verifica che il task appartenga al gruppo dell'utente via project_id.

    Estrae project_id dal task, poi verifica ownership sul progetto.
    Se il progetto non esiste più (cancellato), solo admin/superadmin possono
    operare sul task.
    """
    _access_control.assert_task_group(task_id, current_user, logger=logger)


def _assert_eseguibile_task_owner(
    task_id: str,
    current_user: CurrentUser,
    *,
    expected_eseguibile_id: str | None = None,
) -> None:
    """Validate task-scoped eseguibile output ownership.

    Visibility on the executable is necessary but not sufficient: a shared
    executable can be run by many users, and the output belongs to the user who
    launched that specific task.
    """
    _access_control.assert_eseguibile_task_owner(
        task_id,
        current_user,
        expected_eseguibile_id=expected_eseguibile_id,
    )


pipeline_router = APIRouter(prefix="/projects", tags=["orchestrator"])
tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])

_orchestrator = None


class CancelTaskResponse(TypedDict):
    status: Literal["cancelled"]
    task_id: str


def init_orchestrator(orchestrator: object) -> None:
    """Inizializza il modulo. Chiamare da main.py dopo setup."""
    global _orchestrator
    _orchestrator = orchestrator


@pipeline_router.post("/{project_id}/execute")
def execute_project_pipeline(
    project_id: str,
    request: ExecuteRequest = None,
    intermediate_mode: Optional[
        Literal["metadata_only", "preview_only", "full_pg", "full_artifact"]
    ] = Query(
        None,
        description=(
            "R-25 PR-B3: override per-call della INTERMEDIATE_PERSISTENCE_MODE "
            "globale. Solo superadmin puo' usarlo (403 altrimenti). Mode "
            "invalido restituisce 422 (Pydantic Literal). None = comportamento "
            "compat (legge default globale dalle settings)."
        ),
    ),
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> Mapping[str, object]:
    """Avvia la pipeline come task Celery asincrono."""
    require_service(_orchestrator, "OrchestratorService")
    _assert_project_group(project_id, current_user)

    if intermediate_mode is not None and current_user.role != "superadmin":
        raise HTTPException(
            status_code=403,
            detail="intermediate_mode override richiede ruolo superadmin",
        )

    try:
        from app.core.orchestrator.task_service import task_service
        force_recalculate = request.force_recalculate if request else False
        project_service = getattr(_orchestrator, "project_service", None)
        if project_service is None:
            from app.core.projects.service import ProjectService

            project_service = ProjectService()
        try:
            execution_contract = build_pipeline_execution_contract(
                project_id=project_id,
                project_service=project_service,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        task_info = task_service.create_pipeline_task(
            project_id=project_id,
            force_recalculate=force_recalculate,
            user_id=current_user.user_id,
            intermediate_mode_override=intermediate_mode,
            execution_contract=execution_contract,
        )
        result = dict(_task_info_to_dict(task_info))
        result["async"] = True
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[CRASH] Errore avvio task execute: {e}",
            op="execute",
            project_id=project_id,
            exc_info=True,
        )
        raise_typed_http(
            error_codes.INTERNAL_ERROR,
            status_code=500,
            message="Errore interno del server",
        )


@pipeline_router.get("/{project_id}/preview")
def preview_project(
    project_id: str,
    limit: int = Query(
        100,
        ge=1,
        le=10000,
        description="Numero righe preview (cap 10k per evitare OOM worker)",
    ),
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> Mapping[str, object]:
    """Avvia preview come task Celery asincrono."""
    require_service(_orchestrator, "OrchestratorService")
    _assert_project_group(project_id, current_user)
    try:
        from app.core.orchestrator.task_service import task_service
        task_info = task_service.create_preview_task(
            project_id=project_id,
            limit=limit,
            user_id=current_user.user_id,
        )
        result = dict(_task_info_to_dict(task_info))
        result["async"] = True
        return result
    except Exception as e:
        logger.error(
            f"[CRASH] Errore avvio task preview: {e}",
            op="preview",
            project_id=project_id,
            exc_info=True,
        )
        raise_typed_http(
            error_codes.INTERNAL_ERROR,
            status_code=500,
            message="Errore interno del server",
        )


@pipeline_router.get("/{project_id}/download")
def download_project_result(
    project_id: str,
    file: Optional[str] = None,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> Response:
    """Scarica il file risultato di un progetto."""
    _assert_project_group(project_id, current_user)
    try:
        return _download_response.project_result_response(
            project_id,
            requested_file=file,
            results_root=settings.RESULTS_DIR,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[CRASH] Errore download progetto {project_id}: {e}",
            exc_info=True,
        )
        raise_typed_http(
            error_codes.INTERNAL_ERROR,
            status_code=500,
            message="Errore interno del server",
        )


class CreateTaskRequest(BaseModel):
    task_type: TaskType
    project_id: str
    canvas: JsonObject
    output_format: str = "csv"
    force_recalculate: bool = False
    carica_node_data: Optional[JsonObject] = None
    triggered_by: str = "manual"


class TaskStatusResponse(BaseModel):
    task_id: str
    task_type: str
    project_id: str
    status: str
    progress: float
    message: str
    result: Optional[JsonObject] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    @field_validator("progress", mode="before")
    @classmethod
    def coerce_progress_to_int(_cls, progress_value: object) -> int:
        """Pydantic v2 è strict: il worker Celery può salvare progress come float (es. 5.0)."""
        try:
            return int(progress_value) if progress_value is not None else 0
        except (TypeError, ValueError):
            return 0


@tasks_router.post("", response_model=TaskStatusResponse)
def create_task(
    request: CreateTaskRequest,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> TaskStatusResponse:
    """Crea e avvia un nuovo task asincrono.

    SECURITY: verifica che `request.project_id` appartenga al gruppo
    dell'utente PRIMA di creare qualsiasi task. Senza questo check, un
    utente autenticato puo' accodare task su progetti di altri tenant
    conoscendone l'id (cross-tenant data exfil).
    """
    from app.core.orchestrator.task_service import task_service
    from app.core.auth.resource_access import (
        assert_project_access, assert_canvas_resources_access,
    )

    # Authz guard cross-tenant su project_id
    assert_project_access(request.project_id, current_user)
    # Authz guard sui nodi del canvas: blocca canvas costruiti ad arte
    # che referenziano file_source/project_source di altri tenant
    # Validare qui significa che il task Celery riceve un canvas gia'
    # verificato lato HTTP
    canvas_graph = CanvasGraph.from_canvas(request.canvas)
    assert_canvas_resources_access(
        {"nodes": canvas_graph.raw_nodes, "edges": canvas_graph.edges},
        current_user,
    )

    try:
        if request.task_type == TaskType.ENRICHMENT:
            task_info = task_service.create_enrichment_task(
                project_id=request.project_id,
                canvas=request.canvas,
                output_format=request.output_format,
                force_recalculate=request.force_recalculate,
                user_id=current_user.user_id,
            )
        elif request.task_type == TaskType.CARICA:
            if not request.carica_node_data:
                raise HTTPException(status_code=400, detail="carica_node_data è obbligatorio per task CARICA")
            _validate_rest_carica_task_payload(request.carica_node_data)
            from app.core.projects.service import ProjectService

            enrich_canvas_types_from_metadata(
                request.carica_node_data,
                request.project_id,
                ProjectService().get_project_columns_metadata,
            )
            task_info = task_service.create_carica_task(
                project_id=request.project_id,
                canvas=request.canvas,
                carica_node_data=request.carica_node_data,
                output_format=request.output_format,
                force_recalculate=request.force_recalculate,
                triggered_by=request.triggered_by,
                user_id=current_user.user_id,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Tipo task '{request.task_type}' non supportato")

        return TaskStatusResponse(**_task_info_to_dict(task_info))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore creazione task: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@tasks_router.get("/{task_id}")
def get_task_status(
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Stato attuale di un task.

    SECURITY: verifica ownership via project_id del task.
    """
    _assert_task_group(task_id, current_user)
    from app.core.orchestrator.task_service import task_service

    try:
        task_info = task_service.get_task_status(task_id)
        result = _task_info_to_dict(task_info)
        result["progress"] = int(result.get("progress") or 0)

        if result.get("status") == "pending" and not result.get("created_at"):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.TASK_NOT_FOUND,
                    "message": f"Task {task_id} non trovato",
                },
            )

        return JSONResponse(content=sanitize_task_status_payload(result))

    except HTTPException:
        raise
    # intent: boundary broad→404 / outcome: fail-closed sanitizzato
    # detail fisso: task_id e' input utente, niente interpolazione in response
    except Exception as e:
        logger.error(f"[CRASH] Errore lettura stato task {task_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.TASK_NOT_FOUND,
                "message": "Task non trovato",
            },
        )


@tasks_router.delete("/{task_id}")
@audit_action(action="task.cancel", target_param="task_id")
def cancel_task(
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> CancelTaskResponse:
    """Annulla un task in esecuzione.

    SECURITY: verifica ownership via project_id del task.
    """
    _assert_task_group(task_id, current_user)
    from app.core.orchestrator.task_service import task_service
    
    try:
        success = task_service.cancel_task(task_id)
        if success:
            return {"status": "cancelled", "task_id": task_id}
        else:
            raise HTTPException(status_code=500, detail="Impossibile annullare il task")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore annullamento task {task_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@tasks_router.delete(
    "/{task_id}/full-cleanup",
    status_code=204,
    response_model=None,
    responses={
        204: {"description": "Cleanup completato — tutti gli step ok"},
        207: {
            "description": (
                "Cleanup parziale — almeno uno degli step ha fallito. "
                "Body: {status: 'partial', failed_steps: [step_name, ...]} "
                "dove step_name in {delete_result, unlink_output_file, "
                "delete_log_runs, celery_forget}."
            )
        },
    },
)
@audit_action(action="task.cleanup_complete", target_param="task_id")
def cleanup_task_completely(
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> Optional[JSONResponse]:
    """
    Cleanup completo di un task: elimina result file/blob, log entries e revoca
    il record Celery. Pensato per il ruolo "esecutore" che richiede privacy
    GDPR-style: dopo aver scaricato il risultato, l'esecutore (o la sua UI)
    chiama questo endpoint per rimuovere ogni traccia dell'esecuzione dal
    server.

    SECURITY:
      - Verifica che il project_id collegato al task appartenga al gruppo
        dell'utente corrente. Cross-tenant → 403.
      - Senza project_id non possiamo verificare ownership: 404.
      - Idempotente: ripetere su stesso task non è un errore.

    STATUS CODE:
      - 204: tutti e 4 gli step cleanup (delete_result, unlink_output_file,
        delete_log_runs, celery_forget) sono andati a buon fine.
      - 207: almeno uno step ha sollevato. Body JSON con `failed_steps`
        list espone al caller quali step non sono stati completati.
        Gli step sono best-effort individualmente (uno che fallisce non
        blocca gli altri), ma il caller deve poter sapere che il cleanup
        non e' integrale — altrimenti il claim GDPR "rimuovere ogni traccia"
        resta silenziosamente violato.
    """
    from app.core.orchestrator.task_service import task_service
    from app.core.results.cleanup import delete_result

    project_id = None
    task_result_payload: JsonObject = {}
    try:
        task_info = task_service.get_task_status(task_id)
        project_id = task_info.project_id
        if task_info.result:
            task_result_payload = _task_result_to_dict(task_info.result)
            if not project_id:
                project_id = task_result_payload.get("project_id")
    except Exception as e:
        logger.warning(f"cleanup: impossibile leggere task_info: {e}")

    if not project_id:
        raise HTTPException(
            status_code=404,
            detail="Task non trovato o senza progetto associato — cleanup non eseguibile",
        )
    try:
        from app.core.projects.service import ProjectService
        _meta = ProjectService().get_project_metadata(project_id)
    except Exception as e:
        logger.warning(
            f"[GESTITO] full-cleanup tenant check: project lookup project_id={project_id} fallito: {e}",
            exc_info=True,
        )
        _meta = None
    if _meta is None:
        # Progetto già eliminato: solo admin/superadmin possono fare cleanup residui
        if current_user.role not in ("admin", "superadmin"):
            raise HTTPException(
                status_code=404,
                detail="Progetto associato al task non trovato",
            )
    else:
        assert_group_access(_meta, current_user, f"Task '{task_id}'")

    # Cleanup scoped AL SINGOLO task_id (NON all'intero progetto!
    # Rimuoviamo
    # il record `result` collegato a questo task_id
    # le righe logs / log_runs con run_id == task_id
    # il file output specifico (se registrato nel payload
    # NON tocchiamo `RESULTS_DIR/{project_id}` ne' altri risultati del
    # progetto: un cleanup wide sarebbe data-loss grave (un click su
    # "cleanup task" distruggerebbe lo storico dell'intero progetto

    # Accumulatore step cleanup falliti. Ogni step e' best-effort (un fallimento
    # non blocca i successivi) ma l'esito aggregato finisce nel body 207 cosi'
    # che il caller sappia quali step non sono andati a buon fine
    failed_steps: List[str] = []

    # intent: cascade step best-effort / outcome: fail-open con aggregazione
    # 3. Result row: solo il record di questo task, non tutti del progetto
    try:
        result_id = task_result_payload.get("result_id")
        if result_id:
            deleted = delete_result(result_id)
            if deleted:
                logger.info(f"cleanup: result_id '{result_id}' eliminato (task {task_id})")
        else:
            # Nessun result_id nel payload: non cancelliamo nulla da results
            # per non rischiare di toccare record di altri task
            logger.debug(f"cleanup: task {task_id} senza result_id nel payload, skip results")
    except Exception as e:
        logger.warning(f"cleanup: errore delete_result: {e}", exc_info=True)
        failed_steps.append("delete_result")

    # 4. File output specifico del task (se persistito su disco
    # Il task Celery salva il nome file in `output_file`. Proviamo a
    # rimuoverlo se presente dentro RESULTS_DIR/{project_id}/ — ma NON
    # rimuoviamo l'intera directory, che e' condivisa tra task
    try:
        output_file = task_result_payload.get("output_file")
        if output_file:
            try:
                deleted_output = legacy_file_store.delete_output_file(
                    project_id,
                    output_file,
                    root=settings.RESULTS_DIR,
                )
                if deleted_output:
                    logger.info(f"cleanup: file output rimosso: {output_file}")
            except ValueError:
                # Path traversal guard: file fuori da RESULTS_DIR, skip sicuro
                # non contato in failed_steps: e' una safety skip, non un fallimento
                logger.warning("cleanup: output_file fuori da RESULTS_DIR, skip")
    except Exception as e:
        logger.warning(f"cleanup: errore rimozione output_file: {e}", exc_info=True)
        failed_steps.append("unlink_output_file")

    try:
        from app.core.orchestrator.log_cleanup import delete_task_log_runs
        delete_task_log_runs(task_id)
    except Exception as e:
        logger.warning(f"cleanup: errore delete log_runs: {e}", exc_info=True)
        failed_steps.append("delete_log_runs")

    # 6. Forget del result Celery (libera Redis
    try:
        task_service.forget_task_result(task_id)
    except Exception as e:
        logger.warning(f"cleanup: errore forget Celery: {e}", exc_info=True)
        failed_steps.append("celery_forget")

    # Esito aggregato: se qualche step ha fallito, 207 Multi-Status con body
    # JSON che espone al caller quali step non sono stati completati. Altrimenti
    # 204 No Content (path invariato). FE consumer (axios) tratta 207 come
    # success (validateStatus default 200-299), quindi nessun breaking change
    # UX — il body e' leggibile dai caller futuri che vogliono differenziare
    if failed_steps:
        logger.warning(
            f"cleanup: task {task_id} partial — failed_steps={failed_steps}"
        )
        return JSONResponse(
            status_code=207,
            content={"status": "partial", "failed_steps": failed_steps},
        )

    return None  # 204 No Content


@tasks_router.get("/{task_id}/download")
def download_task_result(
    task_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """Scarica il file risultato di un task.

    Per i task degli eseguibili (is_eseguibile=True) l'output vive su
    object storage (eseguibili-outputs/{task_id}/) — streamming S3 + cleanup
    post-send via background_task. Per tutti gli altri task il reader
    `get_result_data(result_id)` risolve lo storage trasparente:
      - DB BYTEA se il result era < RESULT_S3_THRESHOLD_BYTES
      - S3 object se sopra (via storage_key della row results)
    In entrambi i casi il payload viene streammato al client.

    SECURITY: verifica ownership via project_id del task.
    """
    time_started = time.perf_counter()

    _assert_task_group(task_id, current_user)
    from app.core.orchestrator.task_service import task_service

    try:
        task_info = task_service.get_task_status(task_id)
        time_celery_loaded = time.perf_counter()

        if task_info.status.value != "success":
            raise HTTPException(status_code=400, detail=f"Task non completato (stato: {task_info.status.value})")

        safe_task_id = "".join(c for c in task_id if c.isalnum() or c in "-_")
        if safe_task_id == task_id:
            # intent: optional feature — fallback a DB blob se S3 lookup fallisce
            # outcome: fail-open con breadcrumb (exc_info per diagnosticare S3 down
            try:
                storage_keys = _eseg_output_store.find_output_keys(safe_task_id)
            except Exception as storage_error:
                logger.warning(f"lookup eseguibili storage fallito: {storage_error}", exc_info=True)
                storage_keys = []

            if storage_keys:
                project_id = getattr(task_info, "project_id", None)
                if not project_id and task_info.result:
                    project_id = _task_result_project_id(task_info.result)
                _assert_eseguibile_task_owner(
                    safe_task_id,
                    current_user,
                    expected_eseguibile_id=project_id,
                )
                # Tipicamente 1 file per task. Se multipli, primo e' OK
                # convention: un solo output per task eseguibile
                storage_key = storage_keys[0]
                storage_filename = storage_key.rsplit("/", 1)[-1]
                try:
                    storage_stream = _eseg_output_store.open_stream(storage_key)
                except Exception as storage_error:
                    logger.error(f"[CRASH] open_stream eseguibile {storage_key}: {storage_error}", exc_info=True)
                    raise HTTPException(status_code=500, detail="Errore apertura stream output")

                def _cleanup_after_send(task_id_to_delete: str = safe_task_id) -> None:
                    try:
                        _eseg_output_store.delete_task_outputs(task_id_to_delete)
                    except Exception as storage_error:
                        logger.warning(f"Cleanup post-download S3 fallito per {task_id_to_delete}: {storage_error}")

                background_tasks.add_task(_cleanup_after_send)
                stream_size = storage_stream.get("size") or 0
                logger.info(
                    f"download task {task_id}: fast-path eseguibile (S3 stream) "
                    f"celery={time_celery_loaded - time_started:.3f}s "
                    f"size={stream_size / (1024*1024):.2f}MB "
                    f"key={storage_key}"
                )
                response_headers = {
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Content-Disposition": f'attachment; filename="{storage_filename}"',
                }
                if stream_size:
                    response_headers["Content-Length"] = str(stream_size)
                return StreamingResponse(
                    storage_stream["body"],
                    media_type=_mime_for_filename(storage_filename),
                    headers=response_headers,
                )

        # Ogni task_id ha il proprio result_id nel payload Celery. Usiamo
        # quello per leggere ESATTAMENTE il result generato da questo task
        # NON `get_latest_result(project_id)`: due task sullo stesso
        # project condividono il "latest" e si scambierebbero l'output
        # in base a chi completa per ultimo (race cross-task
        result = _task_result_to_dict(task_info.result)
        project_id = result.get("project_id")
        if not project_id:
            raise HTTPException(status_code=404, detail="project_id non trovato nel risultato del task")

        result_id = result.get("result_id")
        if not result_id:
            # Senza result_id il task non e' downloadabile: il payload
            # Celery e' incompleto (probabile TTL scaduto o task molto
            # vecchio). 410 Gone esplicito invece di 404 ambiguo
            logger.warning(
                f"download task {task_id}: result_id assente nel payload, "
                f"restituisco 410 Gone"
            )
            raise HTTPException(
                status_code=410,
                detail=(
                    "Il risultato di questo task non e' piu' disponibile. "
                    "Rilancia la pipeline per generare un nuovo output."
                ),
            )

        from app.core.artifacts import ArtifactResolver, ArtifactResolutionError
        try:
            blob = ArtifactResolver().fetch_run_blob(
                result_id=result_id,
                expected_project_id=project_id,
            )
        except ArtifactResolutionError as e:
            logger.error(
                f"download task {task_id}: risoluzione blob fallita: {e}"
            )
            raise HTTPException(
                status_code=404,
                detail="Risultato del task non trovato (mismatch project_id)",
            )

        time_blob_loaded = time.perf_counter()

        # Sanitizza filename per prevenire header injection (CRLF
        safe_filename = blob.filename.replace("\r", "").replace("\n", "").replace('"', "'")

        # StreamingResponse + chunked yield: evita di mantenere il
        # blob intero in memoria nel buffer di Starlette/uvicorn e
        # abbassa il time-to-first-byte percepito dal client. Per blob
        # grandi (50+ MB di output Excel/parquet) il pattern
        # `Response(content=blob.data)` faceva una copia completa del
        # buffer prima di iniziare il send, raddoppiando l'occupazione
        # di RAM e ritardando l'inizio del download
        _CHUNK = 1 << 20  # 1 MB
        data = blob.data
        total = len(data)

        def _stream_chunks() -> Iterator[bytes]:
            for chunk_start in range(0, total, _CHUNK):
                yield data[chunk_start:chunk_start + _CHUNK]

        time_prepared = time.perf_counter()
        logger.info(
            f"download task {task_id}: timing celery={time_celery_loaded - time_started:.3f}s "
            f"blob_fetch={time_blob_loaded - time_celery_loaded:.3f}s "
            f"prep={time_prepared - time_blob_loaded:.3f}s "
            f"size={total / (1024*1024):.2f}MB filename={safe_filename}"
        )

        return StreamingResponse(
            _stream_chunks(),
            media_type=_mime_for_filename(blob.filename),
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"',
                "Content-Length": str(total),
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore download risultato task {task_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")
