"""
Router API per il modulo Orchestrator.

Esecuzione pipeline, preview, download risultati e gestione task Celery.
"""

import time
from collections.abc import Mapping
from typing import Any, Literal, Optional, List
from typing_extensions import TypedDict

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, field_validator

from app.config import settings
from app.utils.logger import get_logger
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from app.core.projects.canvas_models import CanvasState, ExecuteRequest
from app.core.orchestrator.task_service import (
    TaskType,
)
from app.core.orchestrator import task_service as task_service_module
from app.core.orchestrator import preview_payload_store
from app.core.orchestrator.service import canvas_validation_gate_blocks
from app.core.orchestrator import log_cleanup as orchestrator_log_cleanup
from app.core.orchestrator.execution_contract import (
    CaricaExecutionContract,
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
    assert_rest_auth_usable,
)
from app.core.orchestrator import download_response as _download_response
from app.core.projects import service as project_service_module
from app.core.results import cleanup as results_cleanup
from app.core.results import blob as results_blob
from app.utils.router_utils import require_service
from app.observability.audit_action import audit_action
from app.core.auth.dependencies import get_current_user, require_not_esecutore
from app.shared.contracts.auth import CurrentUser
from app.shared.contracts.payloads import JsonObject
from app.core.auth import resource_access as resource_access_module
from app.shared.execution.canvas_graph import CanvasGraph
from app.shared.execution.output_contract_types_enricher import (
    enrich_canvas_types_from_output_contract,
)
from app.core.eseguibili import output_store as _eseg_output_store
from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili.download_response import build_consume_once_response
from app.core.io.http_streaming import (
    CloseableStreamingResponse,
    safe_download_filename,
)
from app.core.results import output_file_cleanup
from app.core.projects.canvas_preview_scope import preview_canvas
from app.core.orchestrator.runtime_inputs import apply_identity_inputs, apply_project_runtime_inputs
from app.shared.execution.runtime_identity import RuntimeIdentity, RuntimeIdentityError

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
    a un altro gruppo. Il controllo centralizzato gestisce anche il bypass
    del superadmin.
    """
    _access_control.assert_project_group(project_id, current_user)


def _assert_task_group(task_id: str, current_user: CurrentUser) -> Any:
    """Verifica che il task appartenga al gruppo dell'utente via project_id.

    Estrae project_id dal task, poi verifica ownership sul progetto.
    Se il progetto non esiste più (cancellato), solo admin/superadmin possono
    operare sul task.
    """
    return _access_control.assert_task_group(task_id, current_user, logger=logger)


def _assert_eseguibile_task_owner(
    task_id: str,
    current_user: CurrentUser,
    *,
    expected_eseguibile_id: str | None = None,
    expected_group_id: str | None = None,
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
        expected_group_id=expected_group_id,
    )


def _task_eseguibile_record(
    project_id: object,
    task_result_payload: Mapping[str, object],
) -> Mapping[str, object] | None:
    """Resolve executable identity from explicit task-result metadata."""
    candidate = str(project_id or "")
    if not candidate:
        return None
    if not bool(task_result_payload.get("is_eseguibile")):
        return None
    return eseguibili_db.get_eseguibile(candidate)


pipeline_router = APIRouter(prefix="/projects", tags=["orchestrator"])
tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])

_orchestrator = None


class CancelTaskResponse(TypedDict):
    status: Literal["cancellation_requested"]
    task_id: str


def init_orchestrator(orchestrator: object) -> None:
    """Inizializza il modulo. Chiamare da main.py dopo setup."""
    global _orchestrator
    _orchestrator = orchestrator


def _project_service() -> project_service_module.ProjectService:
    """ProjectService dell'orchestrator iniettato (composition root);
    fallback alla costruzione diretta per contesti senza wiring."""
    service = getattr(_orchestrator, "project_service", None)
    if service is not None:
        return service
    return project_service_module.ProjectService()


@pipeline_router.post("/{project_id}/execute")
def execute_project_pipeline(
    project_id: str,
    request: ExecuteRequest = None,
    intermediate_mode: Optional[
        Literal["metadata_only", "full_artifact"]
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
        force_recalculate = request.force_recalculate if request else False
        project_service = _project_service()
        # Gate canvas-validation: un progetto non validato non parte.
        # Fail-fast qui (nessun task spawnato); il gate scheduler e' separato.
        _gate_meta = project_service.get_project_metadata(project_id)
        if _gate_meta is not None and canvas_validation_gate_blocks(
            getattr(_gate_meta, "canvas_validated", False)
        ):
            raise HTTPException(
                status_code=409,
                detail="Canvas non validato: apri il progetto e premi Valida prima di eseguire.",
            )
        try:
            execution_contract = build_pipeline_execution_contract(
                project_id=project_id,
                project_service=project_service,
            )
            # «Esegui» del progetto: nessun utente per «Utente che esegue»
            # (si esegue come Eseguibile), regola di runtime_identity.
            execution_contract = apply_project_runtime_inputs(
                execution_contract,
                request.inputs if request else {},
                identity=RuntimeIdentity.project_run(),
            )
        except RuntimeIdentityError as exc:
            raise_typed_http(exc.code, status_code=400, message=exc.message)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        task_info = task_service_module.task_service.create_pipeline_task(
            project_id=project_id,
            force_recalculate=force_recalculate,
            user_id=current_user.user_id,
            intermediate_mode_override=intermediate_mode,
            execution_contract=execution_contract.to_payload(),
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
    node_id: Optional[str] = None,
) -> Mapping[str, object]:
    """Avvia preview come task Celery asincrono."""
    require_service(_orchestrator, "OrchestratorService")
    _assert_project_group(project_id, current_user)
    try:
        try:
            execution_contract = build_pipeline_execution_contract(
                project_id=project_id,
                project_service=_project_service(),
            )
        except LookupError:
            raise_typed_http(
                error_codes.PROJECT_NOT_FOUND,
                status_code=404,
                message="Progetto non trovato",
            )
        # Anteprima: «Utente che esegue» vale lo username di prova del nodo
        # (mai l'utente reale, mai per un risultato salvato).
        try:
            execution_contract = apply_identity_inputs(
                execution_contract,
                RuntimeIdentity.preview_of(execution_contract.canvas),
            )
        except RuntimeIdentityError as exc:
            raise_typed_http(exc.code, status_code=400, message=exc.message)
        except ValueError as exc:
            raise_typed_http(error_codes.VALIDATION_ERROR, status_code=400, message=str(exc))
        if node_id is not None:
            try:
                if execution_contract.project_type.lower() not in {"manipola", "estrai", "arricchisci"}:
                    raise ValueError("node_id e' supportato solo per preview Manipola, Estrai e Arricchisci.")
                preview_canvas(CanvasState.model_validate(execution_contract.canvas), node_id)
            except ValueError as exc:
                raise_typed_http(
                    error_codes.VALIDATION_ERROR, status_code=400, message=str(exc),
                )
            execution_contract = execution_contract.model_copy(update={
                "runtime_context": {**execution_contract.runtime_context, "preview_node_id": node_id},
            })
        task_info = task_service_module.task_service.create_preview_task(
            project_id=project_id,
            limit=limit,
            user_id=current_user.user_id,
            execution_contract=execution_contract.to_payload(),
        )
        result = dict(_task_info_to_dict(task_info))
        result["async"] = True
        return result
    except HTTPException:
        # Preserve typed 4xx outcomes instead of flattening them to 500.
        raise
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
    def coerce_progress_to_int(
        _cls: type["TaskStatusResponse"], progress_value: object
    ) -> int:
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
    # Authz guard cross-tenant su project_id
    # Reuse the metadata already loaded by the authorization boundary. A
    # second direct ProjectService lookup both duplicated I/O and bypassed the
    # injectable boundary used by tests and alternate runtimes.
    _gate_meta = resource_access_module.assert_project_access(
        request.project_id,
        current_user,
    )
    if _gate_meta is not None and canvas_validation_gate_blocks(
        getattr(_gate_meta, "canvas_validated", False)
    ):
        raise HTTPException(
            status_code=409,
            detail="Canvas non validato: apri il progetto e premi Valida prima di eseguire.",
        )
    # Authz guard sui nodi del canvas: blocca canvas costruiti ad arte
    # che referenziano file_source/project_source di altri tenant
    # Validare qui significa che il task Celery riceve un canvas gia'
    # verificato lato HTTP
    canvas_graph = CanvasGraph.from_canvas(request.canvas)
    resource_access_module.assert_canvas_resources_access(
        {"nodes": canvas_graph.raw_nodes, "edges": canvas_graph.edges},
        current_user,
    )

    try:
        if request.task_type == TaskType.ENRICHMENT:
            task_info = task_service_module.task_service.create_enrichment_task(
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
            # Credenziali del connettore: chi lancia deve poterlo usare e
            # l'endpoint deve avere la sua origine (carica.rest_auth).
            assert_rest_auth_usable(
                request.carica_node_data,
                current_user,
                project_group_id=str(getattr(_gate_meta, "group_id", "") or ""),
            )

            enrich_canvas_types_from_output_contract(
                request.carica_node_data,
                request.project_id,
                _project_service().get_project_output_contract,
            )
            execution_contract = CaricaExecutionContract(
                project_id=request.project_id,
                project_type=str(
                    getattr(_gate_meta, "project_type", "") or ""
                ),
                group_id=str(getattr(_gate_meta, "group_id", "") or ""),
                canvas=request.canvas,
                carica_node_data=request.carica_node_data,
                output_format=request.output_format,
                force_recalculate=request.force_recalculate,
                triggered_by=request.triggered_by,
            )
            task_info = task_service_module.task_service.create_carica_task(
                project_id=request.project_id,
                canvas=request.canvas,
                carica_node_data=request.carica_node_data,
                output_format=request.output_format,
                force_recalculate=request.force_recalculate,
                triggered_by=request.triggered_by,
                user_id=current_user.user_id,
                execution_contract=execution_contract.to_payload(),
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
    task_info = _assert_task_group(task_id, current_user)

    try:
        result = _task_info_to_dict(task_info)
        result["progress"] = int(result.get("progress") or 0)

        # PostgreSQL stores only the preview reference. Hydrate the tabular
        # payload at this authenticated boundary so existing FE consumers keep
        # the same response contract without a second public endpoint.
        task_payload = result.get("result")
        if isinstance(task_payload, Mapping):
            if preview_payload_store.is_preview_reference(task_payload):
                preview_project_id = getattr(task_info, "project_id", None)
                if not preview_project_id:
                    preview_project_id = _task_result_project_id(task_payload)
                preview_group_id = task_payload.get(
                    preview_payload_store.PREVIEW_GROUP_ID_FIELD
                )
                if not preview_group_id:
                    try:
                        preview_meta = (
                            _project_service().get_project_metadata(preview_project_id)
                            if preview_project_id else None
                        )
                    except _access_control.TASK_LOOKUP_ERRORS as exc:
                        logger.error(
                            "preview tenant lookup unavailable task_id=%s error=%s",
                            task_id,
                            type(exc).__name__,
                            exc_info=True,
                        )
                        raise HTTPException(
                            status_code=503,
                            detail={
                                "error": error_codes.TASK_STORE_UNAVAILABLE,
                                "message": "Stato task temporaneamente non disponibile",
                            },
                        ) from exc
                    preview_group_id = getattr(preview_meta, "group_id", None)
                if not preview_group_id:
                    raise HTTPException(
                        status_code=404,
                        detail="Tenant della preview non determinabile",
                    )
                if (
                    current_user.role != "superadmin"
                    and str(preview_group_id) != current_user.group_id
                ):
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": error_codes.TASK_NOT_FOUND,
                            "message": "Task non trovato",
                        },
                    )
                result["result"] = preview_payload_store.hydrate_preview_payload(
                    task_payload,
                    expected_group_id=preview_group_id,
                )

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
    except preview_payload_store.PreviewPayloadError as e:
        logger.warning(
            "preview payload non disponibile task_id=%s: %s", task_id, e
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": error_codes.PREVIEW_STORAGE_UNAVAILABLE,
                "message": "Preview temporaneamente non disponibile",
            },
        )
    # intent: boundary broad→404 / outcome: fail-closed sanitizzato
    # detail fisso: task_id e' input utente, niente interpolazione in response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore lettura stato task {task_id}: {e}", exc_info=True)
        raise_typed_http(
            error_codes.INTERNAL_ERROR,
            status_code=500,
            message="Errore interno del server",
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
    
    try:
        success = task_service_module.task_service.cancel_task(task_id)
        if success:
            return {"status": "cancellation_requested", "task_id": task_id}
        else:
            raise HTTPException(status_code=500, detail="Impossibile annullare il task")
    except task_service_module.TaskAlreadyTerminalError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": error_codes.TASK_ALREADY_TERMINAL,
                "message": "Il task e' gia' terminato",
                "status": exc.state.lower(),
            },
        )
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
                "delete_eseguibile_outputs, delete_log_runs, celery_forget}."
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
      - 204: tutti gli step applicabili, incluso l'object storage degli
        eseguibili, sono andati a buon fine.
      - 207: almeno uno step ha sollevato. Body JSON con `failed_steps`
        list espone al caller quali step non sono stati completati.
        Gli step sono best-effort individualmente (uno che fallisce non
        blocca gli altri), ma il caller deve poter sapere che il cleanup
        non e' integrale — altrimenti il claim GDPR "rimuovere ogni traccia"
        resta silenziosamente violato.
    """
    task_info = _assert_task_group(task_id, current_user)
    project_id = task_info.project_id
    task_result_payload: JsonObject = {}
    if task_info.result:
        task_result_payload = _task_result_to_dict(task_info.result)
        if not project_id:
            project_id = task_result_payload.get("project_id")

    if not project_id:
        raise HTTPException(
            status_code=404,
            detail="Task non trovato o senza progetto associato — cleanup non eseguibile",
        )
    eseguibile_record = _task_eseguibile_record(
        project_id, task_result_payload,
    )
    is_eseguibile = eseguibile_record is not None
    eseguibile_group_id = ""
    if is_eseguibile:
        eseguibile_group_id = str(eseguibile_record.get("group_id") or "")
    # Cleanup scoped AL SINGOLO task_id (NON all'intero progetto!
    # Rimuoviamo
    # il record `result` collegato a questo task_id
    # le righe logs / log_runs con run_id == task_id
    # il file output specifico (se registrato nel payload
    # NON tocchiamo `RESULTS_DIR/{project_id}` ne' altri risultati del
    # progetto: un cleanup wide sarebbe data-loss grave.

    # Accumulatore step cleanup falliti. Ogni step e' best-effort (un fallimento
    # non blocca i successivi) ma l'esito aggregato finisce nel body 207 cosi'
    # che il caller sappia quali step non sono andati a buon fine
    failed_steps: List[str] = []

    # Eseguibili persist outputs and the task-owner sidecar outside the generic
    # results store. This step is required for the GDPR "remove every trace"
    # contract and remains retryable when object storage is temporarily down.
    if is_eseguibile:
        try:
            _eseg_output_store.delete_task_outputs(
                task_id,
                group_id=eseguibile_group_id,
            )
        except Exception as e:
            logger.warning(
                f"cleanup: errore delete output eseguibile: {e}",
                exc_info=True,
            )
            failed_steps.append("delete_eseguibile_outputs")

    # intent: cascade step best-effort / outcome: fail-open con aggregazione
    # 3. Result row: solo il record di questo task, non tutti del progetto
    try:
        result_id = task_result_payload.get("result_id")
        if result_id:
            deleted = results_cleanup.delete_result(result_id)
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
                deleted_output = output_file_cleanup.delete_project_output_file(
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
        orchestrator_log_cleanup.delete_task_log_runs(task_id)
    except Exception as e:
        logger.warning(f"cleanup: errore delete log_runs: {e}", exc_info=True)
        failed_steps.append("delete_log_runs")

    # 6. Forget del result Celery (libera Redis
    try:
        task_service_module.task_service.forget_task_result(task_id)
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
    current_user: CurrentUser = Depends(get_current_user),
) -> CloseableStreamingResponse:
    """Scarica il file risultato di un task.

    Per i task degli eseguibili (is_eseguibile=True) l'output vive su
    object storage tenant-scoped: la response elimina l'output solo dopo un
    trasferimento completo e lo conserva se il client si disconnette. Per
    tutti gli altri task il reader
    `get_result_data(result_id)` apre l'oggetto S3-compatible tramite la
    storage_key della riga results.
    In entrambi i casi il payload viene streammato al client.

    SECURITY: verifica ownership via project_id del task.
    """
    time_started = time.perf_counter()

    task_info = _assert_task_group(task_id, current_user)

    try:
        time_celery_loaded = time.perf_counter()

        if task_info.status.value != "success":
            raise HTTPException(status_code=400, detail=f"Task non completato (stato: {task_info.status.value})")

        safe_task_id = "".join(c for c in task_id if c.isalnum() or c in "-_")
        # In produzione TaskService normalizza il risultato Celery in
        # TaskResultPayload. Il mapper gestisce sia quel contratto tipizzato
        # sia i dict legacy: filtrare qui per Mapping faceva perdere
        # ``is_eseguibile`` e instradava erroneamente il download sul blob DB.
        eseguibile_payload = _task_result_to_dict(task_info.result)
        project_id = getattr(task_info, "project_id", None)
        if not project_id and task_info.result:
            project_id = _task_result_project_id(task_info.result)
        eseguibile_record = _task_eseguibile_record(
            project_id, eseguibile_payload,
        )
        if safe_task_id == task_id and eseguibile_record is not None:
            eseguibile_group_id = str(
                eseguibile_record.get("group_id") or ""
            )
            _assert_eseguibile_task_owner(
                safe_task_id,
                current_user,
                expected_eseguibile_id=str(project_id),
                expected_group_id=eseguibile_group_id,
            )
            # intent: optional feature — fallback a DB blob se S3 lookup fallisce
            # outcome: fail-open con breadcrumb (exc_info per diagnosticare S3 down
            try:
                storage_keys = (
                    _eseg_output_store.find_output_keys(
                        safe_task_id, group_id=eseguibile_group_id,
                    )
                    if eseguibile_group_id else []
                )
            except Exception as storage_error:
                logger.error(
                    f"lookup eseguibili storage fallito: {storage_error}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=503,
                    detail="Storage output temporaneamente non disponibile",
                ) from storage_error

            if storage_keys:
                try:
                    storage_key = _eseg_output_store.select_output_key(
                        storage_keys,
                        expected_filename=str(
                            eseguibile_payload.get("output_file") or ""
                        ) or None,
                    )
                except RuntimeError as selection_error:
                    logger.error(
                        "Selezione output eseguibile ambigua task_id=%s: %s",
                        task_id,
                        selection_error,
                    )
                    raise HTTPException(
                        status_code=409,
                        detail="Output eseguibile ambiguo; cleanup manuale richiesto",
                    ) from selection_error
                storage_filename = safe_download_filename(
                    storage_key, fallback="output.bin",
                )
                response = build_consume_once_response(
                    task_id=safe_task_id,
                    group_id=eseguibile_group_id,
                    storage_key=storage_key,
                    download_filename=storage_filename,
                    fallback_filename="output.bin",
                    media_type=_mime_for_filename(storage_filename),
                )
                stream_size = int(response.headers.get("content-length") or 0)
                logger.info(
                    f"download task {task_id}: fast-path eseguibile (S3 stream) "
                    f"celery={time_celery_loaded - time_started:.3f}s "
                    f"size={stream_size / (1024*1024):.2f}MB "
                    f"key={storage_key}"
                )
                return response
            raise HTTPException(
                status_code=410,
                detail=(
                    "Il risultato di questo task non e' piu' disponibile. "
                    "Rilancia la pipeline per generare un nuovo output."
                ),
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
            # Senza result_id il task non e' downloadabile: payload incompleto
            # o TTL scaduto. 410 Gone esplicito invece di 404 ambiguo.
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

        stream = results_blob.open_result_stream(
            str(result_id), expected_project_id=str(project_id)
        )
        if stream is None:
            raise HTTPException(
                status_code=404,
                detail="Risultato del task non trovato (mismatch project_id)",
            )

        time_blob_loaded = time.perf_counter()

        # Sanitizza filename per prevenire header injection (CRLF
        safe_filename = safe_download_filename(
            stream["filename"], fallback="result.bin",
        )
        total = int(stream["size"])

        time_prepared = time.perf_counter()
        logger.info(
            f"download task {task_id}: timing celery={time_celery_loaded - time_started:.3f}s "
            f"blob_fetch={time_blob_loaded - time_celery_loaded:.3f}s "
            f"prep={time_prepared - time_blob_loaded:.3f}s "
            f"size={total / (1024*1024):.2f}MB filename={safe_filename}"
        )

        return CloseableStreamingResponse(
            stream["body"],
            media_type=_mime_for_filename(str(stream["filename"])),
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
