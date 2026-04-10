"""
Router API per il modulo Orchestrator.

Esecuzione pipeline, preview, download risultati e gestione task Celery.

Endpoints:
- POST /api/projects/{id}/execute       → esegue pipeline
- GET  /api/projects/{id}/preview        → preview dati
- GET  /api/projects/{id}/download       → scarica risultato
- POST /api/tasks                        → crea task asincrono
- GET  /api/tasks/{id}                   → stato task
- DELETE /api/tasks/{id}                 → annulla task
- GET  /api/tasks/{id}/download          → download risultato task
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel, field_validator
from typing import Optional, Dict, Any, List

from app.config import settings
from app.utils.logger import get_logger
from app.shared.models import ExecuteRequest
from app.services.task_service import TaskType
from app.utils.router_utils import require_service
from app.core.auth.auth import get_current_user, require_not_esecutore, assert_group_access, CurrentUser

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
        raise HTTPException(404, f"Progetto '{project_id}' non trovato")
    assert_group_access(meta, current_user, f"Progetto '{project_id}'")


def _assert_task_group(task_id: str, current_user) -> None:
    """Verifica che il task appartenga al gruppo dell'utente via project_id.

    Estrae project_id dal task, poi verifica ownership sul progetto.
    Se il progetto non esiste più (cancellato), solo admin/superadmin possono
    operare sul task.
    """
    from app.services.task_service import task_service
    try:
        task_info = task_service.get_task_status(task_id)
    except Exception:
        raise HTTPException(404, f"Task '{task_id}' non trovato")

    project_id = getattr(task_info, "project_id", None)
    if not project_id and task_info.result:
        project_id = task_info.result.get("project_id") if isinstance(task_info.result, dict) else None

    if not project_id:
        # Task senza project_id: accessibile solo ad admin+
        if current_user.role not in ("admin", "superadmin"):
            raise HTTPException(404, "Task non trovato")
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
            raise HTTPException(404, "Task non trovato")
    elif current_user.role not in ("admin", "superadmin"):
        raise HTTPException(404, "Task non trovato")


# Due router: uno per pipeline (prefix projects) e uno per tasks
pipeline_router = APIRouter(prefix="/projects", tags=["orchestrator"])
tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])

# Servizi inizializzati da main.py
_orchestrator = None


def init_orchestrator(orchestrator):
    """Inizializza il modulo. Chiamare da main.py dopo setup."""
    global _orchestrator
    _orchestrator = orchestrator


# ==================== PIPELINE EXECUTION ====================

@pipeline_router.post("/{project_id}/execute")
def execute_project_pipeline(project_id: str, request: ExecuteRequest = None, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Avvia la pipeline come task Celery asincrono.

    SECURITY: assert_group_access verifica che il progetto appartenga al
    gruppo dell'utente. Senza questo check, un utente di gruppoA potrebbe
    eseguire la pipeline di un progetto di gruppoB (cross-tenant execution).
    """
    require_service(_orchestrator, "OrchestratorService")
    _assert_project_group(project_id, current_user)
    try:
        from app.services.task_service import task_service
        force_recalculate = request.force_recalculate if request else False
        task_info = task_service.create_pipeline_task(
            project_id=project_id,
            force_recalculate=force_recalculate,
        )
        result = task_info.to_dict()
        result["async"] = True  # segnala a useExecution di avviare il polling
        return result
    except Exception as e:
        logger.error(f"[CRASH] Errore avvio task execute: {e}", op="execute", project_id=project_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")


@pipeline_router.get("/{project_id}/preview")
def preview_project(project_id: str, limit: int = 100, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Avvia preview come task Celery asincrono.

    SECURITY: assert_group_access per cross-tenant isolation.
    """
    require_service(_orchestrator, "OrchestratorService")
    _assert_project_group(project_id, current_user)
    try:
        from app.services.task_service import task_service
        task_info = task_service.create_preview_task(project_id=project_id, limit=limit)
        result = task_info.to_dict()
        result["async"] = True
        return result
    except Exception as e:
        logger.error(f"[CRASH] Errore avvio task preview: {e}", op="preview", project_id=project_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")


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

        if not results_dir.exists():
            raise HTTPException(404, f"Nessun risultato trovato per progetto '{project_id}'")

        if file:
            safe_name = Path(file).name
            target = (results_dir / safe_name).resolve()
            # Protezione path traversal: verifica che il file sia dentro results_dir
            if not str(target).startswith(str(results_dir.resolve())):
                raise HTTPException(403, "Accesso negato")
            if not target.exists():
                raise HTTPException(404, f"File '{safe_name}' non trovato per progetto '{project_id}'")
            return FileResponse(
                path=str(target),
                filename=safe_name,
                media_type=_mime_for_filename(safe_name),
            )

        files = list(results_dir.glob("*.*"))
        if not files:
            raise HTTPException(404, "Nessun file risultato trovato")

        latest_file = max(files, key=lambda x: x.stat().st_mtime)
        return FileResponse(
            path=str(latest_file),
            filename=latest_file.name,
            media_type=_mime_for_filename(latest_file.name),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore download progetto {project_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")



# ==================== TASK ASINCRONI (CELERY) ====================

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
    """Crea e avvia un nuovo task asincrono."""
    from app.services.task_service import task_service

    try:
        if request.task_type == TaskType.ENRICHMENT:
            task_info = task_service.create_enrichment_task(
                project_id=request.project_id,
                canvas=request.canvas,
                output_format=request.output_format,
                force_recalculate=request.force_recalculate
            )
        elif request.task_type == TaskType.CARICA:
            if not request.carica_node_data:
                raise HTTPException(status_code=400, detail="carica_node_data è obbligatorio per task CARICA")
            task_info = task_service.create_carica_task(
                project_id=request.project_id,
                canvas=request.canvas,
                carica_node_data=request.carica_node_data,
                output_format=request.output_format,
                force_recalculate=request.force_recalculate,
                triggered_by=request.triggered_by
            )
        else:
            raise HTTPException(status_code=400, detail=f"Tipo task '{request.task_type}' non supportato")

        return TaskStatusResponse(**task_info.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore creazione task: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")


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
            raise HTTPException(status_code=404, detail=f"Task {task_id} non trovato")

        return JSONResponse(content=_sanitize(result))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore lettura stato task {task_id}: {e}", exc_info=True)
        raise HTTPException(status_code=404, detail=f"Task {task_id} non trovato")


@tasks_router.delete("/{task_id}")
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
        raise HTTPException(status_code=500, detail="Errore interno del server")


@tasks_router.delete("/{task_id}/full-cleanup", status_code=204)
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
    """
    from app.services.task_service import task_service
    import app.db.results_db_service as _results_db
    import sqlite3
    import shutil

    # 1. Estrai project_id dal task (se presente)
    project_id = None
    try:
        task_info = task_service.get_task_status(task_id)
        project_id = task_info.project_id
        if not project_id and task_info.result:
            project_id = task_info.result.get("project_id")
    except Exception as e:
        logger.warning(f"cleanup: impossibile leggere task_info: {e}")

    # 2. Tenant check: senza project_id non possiamo verificare ownership.
    if not project_id:
        raise HTTPException(
            status_code=404,
            detail="Task non trovato o senza progetto associato — cleanup non eseguibile",
        )
    try:
        from app.services.project_service import ProjectService
        _meta = ProjectService().get_project_metadata(project_id)
    except Exception:
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

    # 2. Elimina i risultati salvati (results.db) per questo project_id
    if project_id:
        try:
            n = _results_db.delete_results_for_project(project_id)
            if n:
                logger.info(f"cleanup: eliminate {n} righe da results.db per {project_id}")
        except Exception as e:
            logger.warning(f"cleanup: errore delete_results_for_project: {e}")

        # 3. Elimina file su disco eventualmente persistenti
        try:
            results_dir = settings.RESULTS_DIR / project_id
            if results_dir.exists():
                shutil.rmtree(results_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"cleanup: errore rimozione results_dir: {e}")

        # 4. Elimina log entries con questo project_id
        try:
            log_db_path = settings.LOGS_DIR / "log.db"
            if log_db_path.exists():
                with sqlite3.connect(str(log_db_path), timeout=5) as conn:
                    conn.execute("DELETE FROM logs WHERE project_id = ?", (project_id,))
                    conn.commit()
        except Exception as e:
            logger.warning(f"cleanup: errore delete logs: {e}")

    # 5. Forget del result Celery (libera Redis)
    try:
        from celery.result import AsyncResult
        ar = AsyncResult(task_id, app=task_service.app)
        ar.forget()
    except Exception as e:
        logger.warning(f"cleanup: errore forget Celery: {e}")

    return None  # 204 No Content


@tasks_router.get("/{task_id}/download")
def download_task_result(task_id: str, current_user: CurrentUser = Depends(get_current_user)):
    """Scarica il file risultato di un task dal DB SQLite.

    SECURITY: verifica ownership via project_id del task.
    """
    _assert_task_group(task_id, current_user)
    from app.services.task_service import task_service
    from fastapi.responses import Response as _Response
    import app.db.db_service as _db

    try:
        task_info = task_service.get_task_status(task_id)

        if task_info.status.value != "success":
            raise HTTPException(status_code=400, detail=f"Task non completato (stato: {task_info.status.value})")

        result = task_info.result or {}
        project_id = result.get("project_id")
        if not project_id:
            raise HTTPException(status_code=404, detail="project_id non trovato nel risultato del task")

        # Leggi dal DB
        row = _db.get_latest_result(project_id)
        if not row:
            raise HTTPException(status_code=404, detail="Nessun risultato trovato nel DB per questo progetto")

        data = _db.get_result_data(row["id"])
        if not data:
            raise HTTPException(status_code=404, detail="Dati risultato non trovati")

        return _Response(
            content=data,
            media_type=_mime_for_filename(row["filename"]),
            headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore download risultato task {task_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")


