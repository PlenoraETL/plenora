"""
Task Service - Gestione task asincroni.

Fornisce interfaccia per:
- Creare nuovi task
- Ottenere stato/progress
- Annullare task in esecuzione
- Ottenere risultati
"""

import json
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, asdict
from celery.result import AsyncResult
from celery_app import celery_app
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class TaskStatus(str, Enum):
    """Stati possibili di un task."""
    PENDING = "pending"      # In coda, non ancora avviato
    STARTED = "started"      # Avviato, in esecuzione
    PROGRESS = "progress"    # In esecuzione con progress
    SUCCESS = "success"      # Completato con successo
    FAILURE = "failure"      # Fallito
    REVOKED = "revoked"      # Annullato
    RETRY = "retry"          # In retry dopo errore


class TaskType(str, Enum):
    """Tipi di task supportati."""
    ENRICHMENT = "enrichment"
    EXTRACTION = "extraction"
    TRANSFORMATION = "transformation"


@dataclass
class TaskInfo:
    """Informazioni su un task."""
    task_id: str
    task_type: TaskType
    project_id: str
    status: TaskStatus
    progress: int = 0
    message: str = ""
    result: Optional[Dict] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Converte in dizionario."""
        return {k: v.value if isinstance(v, Enum) else v for k, v in asdict(self).items()}


class TaskService:
    """Servizio per gestione task asincroni."""
    
    def __init__(self):
        self.app = celery_app
    
    # =========================================================================
    # CREATE TASK
    # =========================================================================
    
    def create_enrichment_task(
        self,
        project_id: str,
        canvas: Dict,
        output_format: str = "csv",
        force_recalculate: bool = False
    ) -> TaskInfo:
        """
        Crea e avvia un task di enrichment.
        
        Args:
            project_id: ID del progetto
            canvas: Canvas con nodi e edges
            output_format: Formato output
            force_recalculate: Se True, riesegue anche se esiste cache
            
        Returns:
            TaskInfo con task_id
        """
        from tasks.enrichment_task import execute_enrichment_task
        
        # Avvia task asincrono
        result = execute_enrichment_task.delay(
            project_id=project_id,
            canvas=canvas,
            output_format=output_format,
            force_recalculate=force_recalculate
        )
        
        logger.info(f"Creato task enrichment: {result.id} per progetto {project_id}")
        
        return TaskInfo(
            task_id=result.id,
            task_type=TaskType.ENRICHMENT,
            project_id=project_id,
            status=TaskStatus.PENDING,
            progress=0,
            message="Task in coda...",
            created_at=datetime.now().isoformat()
        )
    
    # =========================================================================
    # GET STATUS
    # =========================================================================
    
    def get_task_status(self, task_id: str) -> TaskInfo:
        """
        Ottiene lo stato attuale di un task.
        
        Args:
            task_id: ID del task
            
        Returns:
            TaskInfo con stato attuale
        """
        result = AsyncResult(task_id, app=self.app)
        
        # Mappa stati Celery ai nostri stati
        celery_to_status = {
            "PENDING": TaskStatus.PENDING,
            "STARTED": TaskStatus.STARTED,
            "PROGRESS": TaskStatus.PROGRESS,
            "SUCCESS": TaskStatus.SUCCESS,
            "FAILURE": TaskStatus.FAILURE,
            "REVOKED": TaskStatus.REVOKED,
            "RETRY": TaskStatus.RETRY,
        }
        
        status = celery_to_status.get(result.state, TaskStatus.PENDING)
        
        # Estrai info dal meta
        meta = result.info if result.info else {}
        
        # Se è un dict con i nostri dati custom
        if isinstance(meta, dict):
            progress = meta.get("progress", 0)
            message = meta.get("message", "")
            
            # Se completato con successo, meta contiene il risultato
            if status == TaskStatus.SUCCESS:
                progress = 100
                message = "Completato"
                task_result = meta
            else:
                task_result = None
                
            # Se fallito, meta potrebbe contenere l'errore
            error = None
            if status == TaskStatus.FAILURE:
                if isinstance(meta, Exception):
                    error = str(meta)
                elif isinstance(meta, dict):
                    error = meta.get("error", str(meta))
                else:
                    error = str(meta)
        else:
            progress = 0
            message = str(meta) if meta else ""
            task_result = None
            error = str(meta) if status == TaskStatus.FAILURE else None
        
        # Estrai project_id dal risultato se disponibile
        project_id = ""
        if isinstance(task_result, dict):
            project_id = task_result.get("project_id", "")
        
        return TaskInfo(
            task_id=task_id,
            task_type=TaskType.ENRICHMENT,  # TODO: salvare tipo task
            project_id=project_id,
            status=status,
            progress=progress,
            message=message,
            result=task_result if status == TaskStatus.SUCCESS else None,
            error=error
        )
    
    # =========================================================================
    # CANCEL TASK
    # =========================================================================
    
    def cancel_task(self, task_id: str) -> bool:
        """
        Annulla un task in esecuzione.
        
        Args:
            task_id: ID del task
            
        Returns:
            True se annullato con successo
        """
        try:
            self.app.control.revoke(task_id, terminate=True)
            logger.info(f"Task {task_id} annullato")
            return True
        except Exception as e:
            logger.error(f"Errore annullamento task {task_id}: {e}")
            return False
    
    # =========================================================================
    # GET RESULT
    # =========================================================================
    
    def get_task_result(self, task_id: str) -> Optional[Dict]:
        """
        Ottiene il risultato di un task completato.
        
        Args:
            task_id: ID del task
            
        Returns:
            Risultato del task o None se non completato
        """
        result = AsyncResult(task_id, app=self.app)
        
        if result.state == "SUCCESS":
            return result.result
        return None
    
    # =========================================================================
    # LIST TASKS
    # =========================================================================
    
    def list_active_tasks(self) -> List[Dict]:
        """
        Lista i task attualmente in esecuzione.
        
        Returns:
            Lista di task attivi
        """
        try:
            inspect = self.app.control.inspect()
            
            active = inspect.active() or {}
            reserved = inspect.reserved() or {}
            
            tasks = []
            
            # Task in esecuzione
            for worker, worker_tasks in active.items():
                for task in worker_tasks:
                    tasks.append({
                        "task_id": task["id"],
                        "name": task["name"],
                        "worker": worker,
                        "status": "active"
                    })
            
            # Task in coda
            for worker, worker_tasks in reserved.items():
                for task in worker_tasks:
                    tasks.append({
                        "task_id": task["id"],
                        "name": task["name"],
                        "worker": worker,
                        "status": "reserved"
                    })
            
            return tasks
            
        except Exception as e:
            logger.error(f"Errore listing task: {e}")
            return []
    
    # =========================================================================
    # CLEANUP
    # =========================================================================
    
    def cleanup_old_results(self, max_age_hours: int = 24) -> int:
        """
        Pulisce risultati task più vecchi di max_age_hours.
        
        Args:
            max_age_hours: Età massima in ore
            
        Returns:
            Numero di risultati eliminati
        """
        # I risultati in Redis scadono automaticamente (CELERY_RESULT_EXPIRES)
        # Questo metodo è per pulizia manuale se necessario
        logger.info(f"Cleanup risultati task più vecchi di {max_age_hours}h")
        return 0  # Redis gestisce scadenza automatica


# Singleton
task_service = TaskService()
