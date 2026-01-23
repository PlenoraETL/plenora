"""
Tasks Router - Endpoint API per gestione task asincroni.

Endpoints:
- POST   /api/tasks                 - Crea nuovo task
- GET    /api/tasks/{task_id}       - Ottieni stato task
- DELETE /api/tasks/{task_id}       - Annulla task
- GET    /api/tasks/{task_id}/result - Ottieni risultato
- GET    /api/tasks/active          - Lista task attivi
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from enum import Enum
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


# ==================== MODELS ====================

class TaskType(str, Enum):
    ENRICHMENT = "enrichment"
    EXTRACTION = "extraction"
    TRANSFORMATION = "transformation"


class CreateTaskRequest(BaseModel):
    """Richiesta creazione task."""
    task_type: TaskType
    project_id: str
    canvas: Dict[str, Any]
    output_format: str = "csv"
    force_recalculate: bool = False


class TaskStatusResponse(BaseModel):
    """Risposta stato task."""
    task_id: str
    task_type: str
    project_id: str
    status: str
    progress: int
    message: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class ActiveTaskResponse(BaseModel):
    """Task attivo."""
    task_id: str
    name: str
    worker: str
    status: str


# ==================== ENDPOINTS ====================

@router.post("", response_model=TaskStatusResponse)
async def create_task(request: CreateTaskRequest):
    """
    Crea e avvia un nuovo task asincrono.
    
    Attualmente supporta solo task di tipo ENRICHMENT.
    """
    from services.task_service import task_service
    
    try:
        if request.task_type == TaskType.ENRICHMENT:
            task_info = task_service.create_enrichment_task(
                project_id=request.project_id,
                canvas=request.canvas,
                output_format=request.output_format,
                force_recalculate=request.force_recalculate
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Tipo task '{request.task_type}' non ancora supportato"
            )
        
        return TaskStatusResponse(**task_info.to_dict())
        
    except Exception as e:
        logger.error(f"Errore creazione task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """
    Ottieni lo stato attuale di un task.
    
    Stati possibili:
    - pending: in coda
    - started: avviato
    - progress: in esecuzione con % avanzamento
    - success: completato
    - failure: fallito
    - revoked: annullato
    """
    from services.task_service import task_service
    
    try:
        task_info = task_service.get_task_status(task_id)
        return TaskStatusResponse(**task_info.to_dict())
        
    except Exception as e:
        logger.error(f"Errore lettura stato task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{task_id}")
async def cancel_task(task_id: str):
    """
    Annulla un task in esecuzione.
    
    Il task verrà terminato e marcato come 'revoked'.
    """
    from services.task_service import task_service
    
    try:
        success = task_service.cancel_task(task_id)
        
        if success:
            return {"status": "cancelled", "task_id": task_id}
        else:
            raise HTTPException(
                status_code=500,
                detail="Impossibile annullare il task"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore annullamento task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{task_id}/result")
async def get_task_result(task_id: str):
    """
    Ottieni il risultato di un task completato.
    
    Se il task ha prodotto un file, ritorna il file.
    Altrimenti ritorna i dati JSON del risultato.
    """
    from services.task_service import task_service
    
    try:
        task_info = task_service.get_task_status(task_id)
        
        if task_info.status.value != "success":
            raise HTTPException(
                status_code=400,
                detail=f"Task non completato (stato: {task_info.status.value})"
            )
        
        result = task_info.result
        
        if not result:
            raise HTTPException(
                status_code=404,
                detail="Risultato non trovato"
            )
        
        # Se c'è un file output, ritornalo
        if result.get("output_file") and result.get("project_id"):
            project_id = result["project_id"]
            output_file = result["output_file"]
            
            file_path = settings.RESULTS_DIR / project_id / output_file
            
            if file_path.exists():
                return FileResponse(
                    path=str(file_path),
                    filename=output_file,
                    media_type="application/octet-stream"
                )
        
        # Altrimenti ritorna JSON
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore lettura risultato task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{task_id}/download")
async def download_task_result(task_id: str):
    """
    Scarica il file risultato di un task completato.
    """
    from services.task_service import task_service
    
    try:
        task_info = task_service.get_task_status(task_id)
        
        if task_info.status.value != "success":
            raise HTTPException(
                status_code=400,
                detail=f"Task non completato (stato: {task_info.status.value})"
            )
        
        result = task_info.result
        
        if not result or not result.get("output_file"):
            raise HTTPException(
                status_code=404,
                detail="File risultato non trovato"
            )
        
        project_id = result["project_id"]
        output_file = result["output_file"]
        
        file_path = settings.RESULTS_DIR / project_id / output_file
        
        if not file_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"File {output_file} non trovato"
            )
        
        return FileResponse(
            path=str(file_path),
            filename=output_file,
            media_type="application/octet-stream"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore download risultato task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/active/list", response_model=List[ActiveTaskResponse])
async def list_active_tasks():
    """
    Lista tutti i task attualmente in esecuzione o in coda.
    """
    from services.task_service import task_service
    
    try:
        tasks = task_service.list_active_tasks()
        return [ActiveTaskResponse(**t) for t in tasks]
        
    except Exception as e:
        logger.error(f"Errore listing task attivi: {e}")
        raise HTTPException(status_code=500, detail=str(e))
