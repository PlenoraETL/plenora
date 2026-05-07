"""
Router API per il modulo Orchestrator.

Esecuzione pipeline, preview, download risultati e gestione task Celery.
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel, field_validator
from typing import Literal, Optional, Dict, Any, List

from app.config import settings
from app.utils.logger import get_logger
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from app.shared.models import ExecuteRequest
from app.services.task_service import TaskType
from app.utils.router_utils import require_service
from app.shared.audit_action import audit_action
from app.core.auth.dependencies import get_current_user, require_not_esecutore
from app.shared.contracts.auth import CurrentUser
from app.core.auth.ownership import assert_group_access

# ── Content-Type per formato file ─────────────────────────────────────────
_MIME_TYPES = {
    "csv":     "text/csv",
    "xlsx":    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json":    "application/json",
    "geojson": "application/geo+json",
    "gpkg":    "application/geopackage+sqlite3",
    "shp":     "application/zip",   # shapefile è sempre zippato
    "gdb":     "application/zip",
    "zip":     "application/zip",
    "parquet": "application/octet-stream",
}

def _mime_for_filename(filename: str) -> str:
    """Ritorna il Content-Type corretto basandosi sull'estensione del file."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _MIME_TYPES.get(ext, "application/octet-stream")


logger = get_logger(__name__)


def _assert_project_group(project_id: str, current_user) -> None:
    """Verifica che il progetto appartenga al gruppo dell'utente corrente.

    Helper centralizzato per gli endpoint orchestrator che operano su
    project_id. Solleva 404 se il progetto non esiste, 403 se appartiene
    a un altro gruppo. Superadmin bypass (assert_group_access lo gestisce).
    """
    from app.services.project_service import ProjectService
    meta = ProjectService().get_project_metadata(project_id)
    if not meta:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.PROJECT_NOT_FOUND,
                "message": f"Progetto '{project_id}' non trovato",
            },
        )
    assert_group_access(meta, current_user, f"Progetto '{project_id}'")


def _assert_task_group(task_id: str, current_user) -> None:
    """Verifica che il task appartenga al gruppo dell'utente via project_id.

    Estrae project_id dal task, poi verifica ownership sul progetto.
    Se il progetto non esiste più (cancellato), solo admin/superadmin possono
    operare sul task.
    """
    from app.services.task_service import task_service
    # intent: boundary broad→404 / outcome: fail-closed user-facing + breadcrumb
    # diagnostico server-side (DB/Redis transient distinguibile in log, non in UX)
    try:
        task_info = task_service.get_task_status(task_id)
    except Exception as e:
        logger.warning(
            f"[GESTITO] _assert_task_group: lookup task_id={task_id} fallito: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.TASK_NOT_FOUND,
                "message": f"Task '{task_id}' non trovato",
            },
        )

    project_id = getattr(task_info, "project_id", None)
    if not project_id and task_info.result:
        project_id = task_info.result.get("project_id") if isinstance(task_info.result, dict) else None

    if not project_id:
        # Task senza project_id: accessibile solo ad admin+
        if current_user.role not in ("admin", "superadmin"):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.TASK_NOT_FOUND,
                    "message": "Task non trovato",
                },
            )
        return

    from app.services.project_service import ProjectService
    meta = ProjectService().get_project_metadata(project_id)
    if meta:
        assert_group_access(meta, current_user, f"Task '{task_id}'")
    elif project_id.startswith("exec_"):
        # Task da eseguibile: il project_id è un eseguibile_id, non un project_id.
        # Verifica accesso tramite la tabella eseguibili (owned + shared).
        from app.core.eseguibili import db as eseguibili_db
        from app.core.eseguibili.router import _check_visible
        record = eseguibili_db.get_eseguibile(project_id)
        if record:
            _check_visible(record, current_user)
        elif current_user.role not in ("admin", "superadmin"):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.TASK_NOT_FOUND,
                    "message": "Task non trovato",
                },
            )
    elif current_user.role not in ("admin", "superadmin"):
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.TASK_NOT_FOUND,
                "message": "Task non trovato",
            },
        )


# Due router: uno per pipeline (prefix projects) e uno per tasks
pipeline_router = APIRouter(prefix="/projects", tags=["orchestrator"])
tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])

# Servizi inizializzati da main.py
_orchestrator = None


def init_orchestrator(orchestrator):
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
):
    """Avvia la pipeline come task Celery asincrono.

    SECURITY: assert_group_access verifica che il progetto appartenga al
    gruppo dell'utente. Senza questo check, un utente di gruppoA potrebbe
    eseguire la pipeline di un progetto di gruppoB (cross-tenant execution).

    R-25 PR-B3: il param `intermediate_mode` e' opt-in superadmin per
    audit run-specific. Il dual-auth (require_not_esecutore PER il
    base-flow + check superadmin SOLO se override richiesto) preserva
    la backward-compat: caller esistenti senza param continuano a girare
    con i loro ruoli abituali.
    """
    require_service(_orchestrator, "OrchestratorService")
    _assert_project_group(project_id, current_user)

    # Auth gate addizionale solo se intermediate_mode e' valorizzato.
    if intermediate_mode is not None and current_user.role != "superadmin":
        raise HTTPException(
            status_code=403,
            detail="intermediate_mode override richiede ruolo superadmin",
        )

    try:
        from app.services.task_service import task_service
        force_recalculate = request.force_recalculate if request else False
        task_info = task_service.create_pipeline_task(
            project_id=project_id,
            force_recalculate=force_recalculate,
            user_id=current_user.user_id,  # N9: re-check permessi al pickup
            intermediate_mode_override=intermediate_mode,
        )
        result = task_info.to_dict()
        result["async"] = True  # segnala a useExecution di avviare il polling
        return result
    except Exception as e:
        logger.error(f"[CRASH] Errore avvio task execute: {e}", op="execute", project_id=project_id, exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@pipeline_router.get("/{project_id}/preview")
def preview_project(
    project_id: str,
    limit: int = Query(100, ge=1, le=10000, description="Numero righe preview (cap 10k per evitare OOM worker)"),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Avvia preview come task Celery asincrono.

    SECURITY: assert_group_access per cross-tenant isolation.
    N5 (2026-04-17): `limit` ha cap 10k per prevenire DoS via OOM worker.
    Un attacker con `?limit=999999999` senza cap materializzava milioni di
    righe in RAM del worker Celery -> crash + rifiuto servizio altri job.
    """
    require_service(_orchestrator, "OrchestratorService")
    _assert_project_group(project_id, current_user)
    try:
        from app.services.task_service import task_service
        task_info = task_service.create_preview_task(
            project_id=project_id,
            limit=limit,
            user_id=current_user.user_id,  # N9
        )
        result = task_info.to_dict()
        result["async"] = True
        return result
    except Exception as e:
        logger.error(f"[CRASH] Errore avvio task preview: {e}", op="preview", project_id=project_id, exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@pipeline_router.get("/{project_id}/download")
def download_project_result(project_id: str, file: Optional[str] = None, current_user: CurrentUser = Depends(require_not_esecutore)):
    """
    Scarica il file risultato di un progetto.

    SECURITY: assert_group_access per cross-tenant isolation. Senza questo
    check, un utente potrebbe scaricare risultati di progetti di altri tenant.

    Parametri:
        file: nome file specifico (opzionale) — usato per scaricare file
              accessori come il report errori di arricchimento.
              Se omesso, restituisce il file più recente.
    """
    _assert_project_group(project_id, current_user)
    try:
        results_dir = settings.RESULTS_DIR / project_id

        if file:
            if not results_dir.exists():
                raise HTTPException(404, f"File '{file}' non trovato per progetto '{project_id}'")
            from app.shared.path_policy import assert_path_within
            safe_name = Path(file).name
            try:
                target = assert_path_within(
                    results_dir / safe_name, [results_dir]
                )
            except ValueError:
                raise_typed_http(
                    code=error_codes.ACCESS_DENIED,
                    status_code=403,
                    message="Accesso negato",
                )
            if not target.exists():
                raise HTTPException(404, f"File '{safe_name}' non trovato per progetto '{project_id}'")
            return FileResponse(
                path=str(target),
                filename=safe_name,
                media_type=_mime_for_filename(safe_name),
            )

        # Fast-path legacy: se il filesystem ha risultati (progetti pre-R#1
        # mai migrati al result store moderno — DB BYTEA o S3), servi
        # il file più recente senza toccare il result store.
        if results_dir.exists():
            files = list(results_dir.glob("*.*"))
            if files:
                latest_file = max(files, key=lambda x: x.stat().st_mtime)
                return FileResponse(
                    path=str(latest_file),
                    filename=latest_file.name,
                    media_type=_mime_for_filename(latest_file.name),
                )

        # Fallback post-R#1: i result blob vivono nel DB, il file temp è
        # già stato cancellato da `save_result_blob`. Recupera l'ultimo
        # result dal service DB e ritorna i bytes inline.
        from app.db import results_db_service
        from fastapi.responses import Response

        latest = results_db_service.get_latest_result(project_id)
        if not latest:
            raise HTTPException(404, f"Nessun risultato trovato per progetto '{project_id}'")

        blob = results_db_service.get_result_data(latest["id"])
        if blob is None:
            raise HTTPException(404, f"Blob risultato non disponibile per progetto '{project_id}'")

        filename = latest.get("filename") or f"{project_id}.{latest.get('format', 'csv')}"
        return Response(
            content=blob,
            media_type=_mime_for_filename(filename),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(blob)),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore download progetto {project_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


class CreateTaskRequest(BaseModel):
    task_type: TaskType
    project_id: str
    canvas: Dict[str, Any]
    output_format: str = "csv"
    force_recalculate: bool = False
    carica_node_data: Optional[Dict[str, Any]] = None
    triggered_by: str = "manual"


class TaskStatusResponse(BaseModel):
    task_id: str
    task_type: str
    project_id: str
    status: str
    progress: float
    message: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    @field_validator("progress", mode="before")
    @classmethod
    def coerce_progress_to_int(cls, v) -> int:
        """Pydantic v2 è strict: il worker Celery può salvare progress come float (es. 5.0)."""
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0


@tasks_router.post("", response_model=TaskStatusResponse)
def create_task(request: CreateTaskRequest, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Crea e avvia un nuovo task asincrono.

    SECURITY: verifica che `request.project_id` appartenga al gruppo
    dell'utente PRIMA di creare qualsiasi task. Senza questo check, un
    utente autenticato puo' accodare task su progetti di altri tenant
    conoscendone l'id (cross-tenant data exfil).
    """
    from app.services.task_service import task_service
    from app.shared.authz_helpers import (
        assert_project_access, assert_canvas_resources_access,
    )

    # Authz guard cross-tenant su project_id
    assert_project_access(request.project_id, current_user)
    # Authz guard sui nodi del canvas: blocca canvas costruiti ad arte
    # che referenziano file_source/project_source di altri tenant.
    # Validare qui significa che il task Celery riceve un canvas gia'
    # verificato lato HTTP.
    assert_canvas_resources_access(
        request.canvas if isinstance(request.canvas, dict) else (
            request.canvas.model_dump() if hasattr(request.canvas, "model_dump") else {}
        ),
        current_user,
    )

    try:
        if request.task_type == TaskType.ENRICHMENT:
            task_info = task_service.create_enrichment_task(
                project_id=request.project_id,
                canvas=request.canvas,
                output_format=request.output_format,
                force_recalculate=request.force_recalculate,
                user_id=current_user.user_id,  # N9
            )
        elif request.task_type == TaskType.CARICA:
            if not request.carica_node_data:
                raise HTTPException(status_code=400, detail="carica_node_data è obbligatorio per task CARICA")
            # N11: arricchisci canvas_types da project columns_metadata
            from app.shared.canvas_types_enricher import enrich_canvas_types_from_metadata
            enrich_canvas_types_from_metadata(request.carica_node_data, request.project_id)
            task_info = task_service.create_carica_task(
                project_id=request.project_id,
                canvas=request.canvas,
                carica_node_data=request.carica_node_data,
                output_format=request.output_format,
                force_recalculate=request.force_recalculate,
                triggered_by=request.triggered_by,
                user_id=current_user.user_id,  # N9
            )
        else:
            raise HTTPException(status_code=400, detail=f"Tipo task '{request.task_type}' non supportato")

        return TaskStatusResponse(**task_info.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore creazione task: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@tasks_router.get("/{task_id}")
def get_task_status(task_id: str, current_user: CurrentUser = Depends(get_current_user)):
    """Stato attuale di un task.

    SECURITY: verifica ownership via project_id del task.
    """
    _assert_task_group(task_id, current_user)
    from app.services.task_service import task_service
    from fastapi.responses import JSONResponse
    from decimal import Decimal
    import math

    def _sanitize(obj):
        """Converte ricorsivamente tipi non-JSON-serializable in tipi Python nativi."""
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_sanitize(v) for v in obj]
        if isinstance(obj, Decimal):
            f = float(obj)
            return int(f) if f == int(f) else f
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            if obj == int(obj):
                return int(obj)
            return obj
        # pandas NaT, Timestamp, date
        t = type(obj).__name__
        if t in ("NaTType", "NaT"):
            return None
        if t == "Timestamp":
            return obj.isoformat()
        if t == "date":
            return obj.isoformat()
        # numpy integer/float types
        if t.startswith("int") and t != "int":
            return int(obj)
        if t.startswith("float") and t != "float":
            return float(obj)
        return obj

    try:
        task_info = task_service.get_task_status(task_id)
        result = task_info.to_dict()
        result["progress"] = int(result.get("progress") or 0)

        if result.get("status") == "pending" and not result.get("created_at"):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.TASK_NOT_FOUND,
                    "message": f"Task {task_id} non trovato",
                },
            )

        return JSONResponse(content=_sanitize(result))

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
def cancel_task(task_id: str, current_user: CurrentUser = Depends(get_current_user)):
    """Annulla un task in esecuzione.

    SECURITY: verifica ownership via project_id del task.
    """
    _assert_task_group(task_id, current_user)
    from app.services.task_service import task_service
    
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
def cleanup_task_completely(task_id: str, current_user: CurrentUser = Depends(get_current_user)):
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
    from app.services.task_service import task_service
    import app.db.results_db_service as _results_db

    # 1. Estrai project_id + result_id dal task (se presenti)
    project_id = None
    task_result_payload: dict = {}
    try:
        task_info = task_service.get_task_status(task_id)
        project_id = task_info.project_id
        if task_info.result:
            task_result_payload = task_info.result or {}
            if not project_id:
                project_id = task_result_payload.get("project_id")
    except Exception as e:
        logger.warning(f"cleanup: impossibile leggere task_info: {e}")

    # 2. Tenant check: senza project_id non possiamo verificare ownership.
    if not project_id:
        raise HTTPException(
            status_code=404,
            detail="Task non trovato o senza progetto associato — cleanup non eseguibile",
        )
    # intent: boundary tenant-check / outcome: fail-open solo per progetto-gia-eliminato
    # breadcrumb: DB transient vs progetto genuinamente mancante distinguibile in log
    try:
        from app.services.project_service import ProjectService
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

    # Cleanup scoped AL SINGOLO task_id (NON all'intero progetto!)
    # Rimuoviamo:
    #   - il record `result` collegato a questo task_id
    #   - le righe logs / log_runs con run_id == task_id
    #   - il file output specifico (se registrato nel payload)
    # NON tocchiamo `RESULTS_DIR/{project_id}` ne' altri risultati del
    # progetto: un cleanup wide sarebbe data-loss grave (un click su
    # "cleanup task" distruggerebbe lo storico dell'intero progetto).

    # Accumulatore step cleanup falliti. Ogni step e' best-effort (un fallimento
    # non blocca i successivi) ma l'esito aggregato finisce nel body 207 cosi'
    # che il caller sappia quali step non sono andati a buon fine.
    failed_steps: List[str] = []

    # intent: cascade step best-effort / outcome: fail-open con aggregazione
    # 3. Result row: solo il record di questo task, non tutti del progetto
    try:
        result_id = task_result_payload.get("result_id")
        if result_id:
            deleted = _results_db.delete_result(result_id)
            if deleted:
                logger.info(f"cleanup: result_id '{result_id}' eliminato (task {task_id})")
        else:
            # Nessun result_id nel payload: non cancelliamo nulla da results
            # per non rischiare di toccare record di altri task.
            logger.debug(f"cleanup: task {task_id} senza result_id nel payload, skip results")
    except Exception as e:
        logger.warning(f"cleanup: errore delete_result: {e}", exc_info=True)
        failed_steps.append("delete_result")

    # 4. File output specifico del task (se persistito su disco)
    # Il task Celery salva il nome file in `output_file`. Proviamo a
    # rimuoverlo se presente dentro RESULTS_DIR/{project_id}/ — ma NON
    # rimuoviamo l'intera directory, che e' condivisa tra task.
    try:
        output_file = task_result_payload.get("output_file")
        if output_file:
            from pathlib import Path as _P
            candidate = settings.RESULTS_DIR / project_id / output_file
            root = (settings.RESULTS_DIR / project_id).resolve()
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
                if resolved.is_file():
                    resolved.unlink()
                    logger.info(f"cleanup: file output rimosso: {resolved.name}")
            except ValueError:
                # Path traversal guard: file fuori da RESULTS_DIR, skip sicuro
                # (non contato in failed_steps: e' una safety skip, non un fallimento)
                logger.warning(f"cleanup: output_file fuori da RESULTS_DIR, skip")
    except Exception as e:
        logger.warning(f"cleanup: errore rimozione output_file: {e}", exc_info=True)
        failed_steps.append("unlink_output_file")

    try:
        from app.db.log_runs_db_service import delete_log_run_by_run_id
        delete_log_run_by_run_id(task_id)
    except Exception as e:
        logger.warning(f"cleanup: errore delete log_runs: {e}", exc_info=True)
        failed_steps.append("delete_log_runs")

    # 6. Forget del result Celery (libera Redis)
    try:
        from celery.result import AsyncResult
        ar = AsyncResult(task_id, app=task_service.app)
        ar.forget()
    except Exception as e:
        logger.warning(f"cleanup: errore forget Celery: {e}", exc_info=True)
        failed_steps.append("celery_forget")

    # Esito aggregato: se qualche step ha fallito, 207 Multi-Status con body
    # JSON che espone al caller quali step non sono stati completati. Altrimenti
    # 204 No Content (path invariato). FE consumer (axios) tratta 207 come
    # success (validateStatus default 200-299), quindi nessun breaking change
    # UX — il body e' leggibile dai caller futuri che vogliono differenziare.
    if failed_steps:
        from fastapi.responses import JSONResponse
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
):
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
    import time as _time
    _t_start = _time.perf_counter()

    _assert_task_group(task_id, current_user)
    from app.services.task_service import task_service
    from fastapi.responses import (
        Response as _Response,
        FileResponse,
        StreamingResponse as _StreamingResponse,
    )
    from app.config import settings as _settings

    try:
        task_info = task_service.get_task_status(task_id)
        _t_celery = _time.perf_counter()

        if task_info.status.value != "success":
            raise HTTPException(status_code=400, detail=f"Task non completato (stato: {task_info.status.value})")

        # ── FAST-PATH ESEGUIBILE: streaming da object storage (S3) ──
        # Sanitizza task_id contro path traversal / injection in prefix.
        safe_task_id = "".join(c for c in task_id if c.isalnum() or c in "-_")
        if safe_task_id == task_id:
            from app.core.eseguibili import storage as _eseg_storage
            # intent: optional feature — fallback a DB blob se S3 lookup fallisce
            # outcome: fail-open con breadcrumb (exc_info per diagnosticare S3 down)
            try:
                _keys = _eseg_storage.find_keys(safe_task_id)
            except Exception as _e:
                logger.warning(f"lookup eseguibili storage fallito: {_e}", exc_info=True)
                _keys = []

            if _keys:
                # Tipicamente 1 file per task. Se multipli, primo e' OK
                # (convention: un solo output per task eseguibile).
                _key = _keys[0]
                _filename = _key.rsplit("/", 1)[-1]
                # intent: boundary broad→HTTP500 / outcome: fail-closed
                try:
                    _stream = _eseg_storage.open_stream(_key)
                except Exception as _e:
                    logger.error(f"[CRASH] open_stream eseguibile {_key}: {_e}", exc_info=True)
                    raise HTTPException(status_code=500, detail="Errore apertura stream output")

                def _cleanup_after_send(_tid=safe_task_id):
                    try:
                        _eseg_storage.delete_by_task(_tid)
                    except Exception as _e:
                        logger.warning(f"Cleanup post-download S3 fallito per {_tid}: {_e}")

                background_tasks.add_task(_cleanup_after_send)
                _size = _stream.get("size") or 0
                logger.info(
                    f"download task {task_id}: fast-path eseguibile (S3 stream) "
                    f"celery={_t_celery - _t_start:.3f}s "
                    f"size={_size / (1024*1024):.2f}MB "
                    f"key={_key}"
                )
                _headers = {
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Content-Disposition": f'attachment; filename="{_filename}"',
                }
                if _size:
                    _headers["Content-Length"] = str(_size)
                return _StreamingResponse(
                    _stream["body"],
                    media_type=_mime_for_filename(_filename),
                    headers=_headers,
                )

        # ── FALLBACK: lettura dal result persistito (DB BYTEA o S3) ──
        # Ogni task_id ha il proprio result_id nel payload Celery. Usiamo
        # quello per leggere ESATTAMENTE il result generato da questo task,
        # NON `get_latest_result(project_id)`: due task sullo stesso
        # project condividono il "latest" e si scambierebbero l'output
        # in base a chi completa per ultimo (race cross-task).
        result = task_info.result or {}
        project_id = result.get("project_id")
        if not project_id:
            raise HTTPException(status_code=404, detail="project_id non trovato nel risultato del task")

        result_id = result.get("result_id")
        if not result_id:
            # Senza result_id il task non e' downloadabile: il payload
            # Celery e' incompleto (probabile TTL scaduto o task molto
            # vecchio). 410 Gone esplicito invece di 404 ambiguo.
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

        _t_blob = _time.perf_counter()

        # Sanitizza filename per prevenire header injection (CRLF)
        safe_filename = blob.filename.replace("\r", "").replace("\n", "").replace('"', "'")

        # StreamingResponse + chunked yield: evita di mantenere il
        # blob intero in memoria nel buffer di Starlette/uvicorn e
        # abbassa il time-to-first-byte percepito dal client. Per blob
        # grandi (50+ MB di output Excel/parquet) il pattern
        # `Response(content=blob.data)` faceva una copia completa del
        # buffer prima di iniziare il send, raddoppiando l'occupazione
        # di RAM e ritardando l'inizio del download.
        _CHUNK = 1 << 20  # 1 MB
        data = blob.data
        total = len(data)

        def _stream_chunks():
            for i in range(0, total, _CHUNK):
                yield data[i:i + _CHUNK]

        _t_done = _time.perf_counter()
        logger.info(
            f"download task {task_id}: timing celery={_t_celery - _t_start:.3f}s "
            f"blob_fetch={_t_blob - _t_celery:.3f}s "
            f"prep={_t_done - _t_blob:.3f}s "
            f"size={total / (1024*1024):.2f}MB filename={safe_filename}"
        )

        return _StreamingResponse(
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
