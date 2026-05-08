"""Canonical orchestrator boundary for asynchronous Celery task management."""

from typing import Dict, Mapping, Optional, List
from datetime import datetime, timezone
from enum import Enum
from dataclasses import dataclass
from celery.result import AsyncResult
from workers.celery_app import celery_app
from app.config import settings
from app.utils.logger import get_logger

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
    CARICA = "carica"
    EXECUTE = "execute"
    PREVIEW = "preview"


@dataclass(frozen=True)
class TaskResultPayload:
    """Typed core contract for successful Celery task result metadata."""

    payload: Mapping[str, object]

    @classmethod
    def from_celery_meta(cls, meta: Mapping[str, object]) -> "TaskResultPayload":
        return cls(payload=dict(meta))

    @property
    def project_id(self) -> str:
        value = self.payload.get("project_id", "")
        return str(value) if value is not None else ""

    @property
    def result_id(self) -> Optional[str]:
        value = self.payload.get("result_id")
        return str(value) if value is not None else None

    @property
    def output_file(self) -> Optional[str]:
        value = self.payload.get("output_file")
        return str(value) if value is not None else None

    def to_dict(self) -> Dict[str, object]:
        return dict(self.payload)


@dataclass
class TaskInfo:
    """Informazioni su un task."""
    task_id: str
    task_type: TaskType
    project_id: str
    status: TaskStatus
    progress: int = 0
    message: str = ""
    result: Optional[TaskResultPayload] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.result, dict):
            self.result = TaskResultPayload.from_celery_meta(self.result)
    
    def to_dict(self) -> Dict[str, object]:
        """Converte in dizionario."""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "project_id": self.project_id,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "result": self.result.to_dict() if self.result else None,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True)
class ActiveTaskInfo:
    """Typed core contract for a Celery worker task listing row."""

    task_id: str
    name: str
    worker: str
    status: str

    @classmethod
    def from_celery_task(
        cls,
        worker: str,
        task: Mapping[str, object],
        status: str,
    ) -> "ActiveTaskInfo":
        return cls(
            task_id=str(task["id"]),
            name=str(task["name"]),
            worker=worker,
            status=status,
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "worker": self.worker,
            "status": self.status,
        }


class TaskService:
    """Servizio per gestione task asincroni."""

    # Cache locale task_id → project_id per risolvere il project_id
    # anche quando il task è in stato PENDING (Celery non ha ancora metadata
    # Risolve la race condition: frontend fa polling subito dopo la creazione
    # ma il worker non ha ancora scritto project_id nel result backend
    _task_project_cache: Dict[str, str] = {}
    _CACHE_MAX_SIZE = 500

    def _prune_cache(self):
        """Rimuove le entry più vecchie se la cache supera il limite."""
        if len(self._task_project_cache) > self._CACHE_MAX_SIZE:
            excess = len(self._task_project_cache) - self._CACHE_MAX_SIZE // 2
            keys = list(self._task_project_cache.keys())[:excess]
            for k in keys:
                self._task_project_cache.pop(k, None)

    def __init__(self):
        self.app = celery_app
    
    # CREAZIONE TASK
    
    def create_enrichment_task(
        self,
        project_id: str,
        canvas: Dict,
        output_format: str = "csv",
        force_recalculate: bool = False,
        cleanup_file_source_ids: Optional[List[str]] = None,
        is_eseguibile: bool = False,
        user_id: Optional[str] = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> TaskInfo:
        """
        Crea e avvia un task di enrichment.

        Args:
            project_id: ID del progetto
            canvas: Canvas con nodi e edges
            output_format: Formato output
            force_recalculate: Se True, riesegue anche se esiste cache
            cleanup_file_source_ids: Opzionale — lista di file source temporanei
                da eliminare al termine del task (usato dagli eseguibili per
                rimuovere i file caricati dall'utente dopo l'esecuzione).
            is_eseguibile: True se il task gira per un "eseguibile" pubblicato
                (record della tabella `eseguibili`). In questo caso il task
                salta qualsiasi scrittura su DB (runs/log_runs/results/node_rows/
                columns_metadata) e persiste l'output su disco in
                ESEGUIBILI_OUTPUTS_DIR/{task_id}/.

        Returns:
            TaskInfo con task_id
        """
        from workers.tasks.enrichment_task import execute_enrichment_task

        # Avvia task asincrono
        result = execute_enrichment_task.delay(
            project_id=project_id,
            canvas=canvas,
            output_format=output_format,
            force_recalculate=force_recalculate,
            cleanup_file_source_ids=cleanup_file_source_ids,
            is_eseguibile=is_eseguibile,
            user_id=user_id,
            intermediate_mode_override=intermediate_mode_override,
        )

        logger.info(f"Creato task enrichment: {result.id} per progetto {project_id}")

        # Cache per risolvere project_id durante PENDING (prima che il worker scriva metadata
        self._task_project_cache[result.id] = project_id
        self._prune_cache()

        return TaskInfo(
            task_id=result.id,
            task_type=TaskType.ENRICHMENT,
            project_id=project_id,
            status=TaskStatus.PENDING,
            progress=0,
            message="Task in coda...",
            created_at=datetime.now(timezone.utc).isoformat()
        )
    
    def create_carica_task(
        self,
        project_id: str,
        canvas: Dict,
        carica_node_data: Dict,
        output_format: str = "geojson",
        force_recalculate: bool = False,
        triggered_by: str = "manual",
        user_id: Optional[str] = None,
    ) -> TaskInfo:
        """
        Crea e avvia un task di caricamento su database.
        
        Args:
            project_id: ID del progetto
            canvas: Canvas con nodi e edges
            carica_node_data: Configurazione del nodo CARICA
            output_format: Formato output del nodo Result upstream
            force_recalculate: Se True, riesegue pipeline upstream
            triggered_by: "manual" o "cron"
            
        Returns:
            TaskInfo con task_id
        """
        from workers.tasks.carica_task import execute_carica_task
        
        # Avvia task asincrono
        result = execute_carica_task.delay(
            project_id=project_id,
            canvas=canvas,
            carica_node_data=carica_node_data,
            output_format=output_format,
            force_recalculate=force_recalculate,
            triggered_by=triggered_by,
            user_id=user_id,
        )
        
        logger.info(f"Creato task carica: {result.id} per progetto {project_id}")
        self._task_project_cache[result.id] = project_id
        self._prune_cache()

        return TaskInfo(
            task_id=result.id,
            task_type=TaskType.CARICA,
            project_id=project_id,
            status=TaskStatus.PENDING,
            progress=0,
            message="Task CARICA in coda...",
            created_at=datetime.now(timezone.utc).isoformat()
        )
    
    # RECUPERA STATO E PROGRESSO
    
    def create_pipeline_task(
        self,
        project_id: str,
        force_recalculate: bool = False,
        triggered_by: str = "manual",
        user_id: Optional[str] = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> TaskInfo:
        """Crea e avvia un task di esecuzione pipeline (ESTRAI/MANIPOLA/ARRICCHISCI).

        N9: `user_id` viene propagato alla task Celery per re-check
        permessi al pickup del worker. Se None (tipico delle cron
        schedulate), il task skippa il re-check.

        R-25 PR-B3: `intermediate_mode_override` viene propagato al task
        Celery per audit run-specific. None = bit-compat. Auth gate
        (superadmin-only) e' al boundary HTTP, non qui.
        """
        from workers.tasks.pipeline_task import execute_pipeline_task
        result = execute_pipeline_task.delay(
            project_id=project_id,
            force_recalculate=force_recalculate,
            triggered_by=triggered_by,
            user_id=user_id,
            intermediate_mode_override=intermediate_mode_override,
        )
        logger.info(f"Creato task execute: {result.id} per progetto {project_id}")
        self._task_project_cache[result.id] = project_id
        self._prune_cache()
        return TaskInfo(
            task_id=result.id,
            task_type=TaskType.EXECUTE,
            project_id=project_id,
            status=TaskStatus.PENDING,
            progress=0,
            message="Pipeline in coda...",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def create_preview_task(
        self,
        project_id: str,
        limit: int = 100,
        user_id: Optional[str] = None,
    ) -> TaskInfo:
        """Crea e avvia un task di preview pipeline."""
        from workers.tasks.pipeline_task import preview_pipeline_task
        result = preview_pipeline_task.delay(
            project_id=project_id,
            limit=limit,
            user_id=user_id,
        )
        logger.info(f"Creato task preview: {result.id} per progetto {project_id}")
        self._task_project_cache[result.id] = project_id
        self._prune_cache()
        return TaskInfo(
            task_id=result.id,
            task_type=TaskType.PREVIEW,
            project_id=project_id,
            status=TaskStatus.PENDING,
            progress=0,
            message="Preview in coda...",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

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
            # "progress" è la chiave standard (round 4+). "percent" è il fallback
            # per task avviati prima della normalizzazione e ancora in Redis
            # Cast esplicito a int: il worker può salvare float (es. 5.0 da processed/total*100
            progress = int(meta.get("progress", meta.get("percent", 0)))
            message = meta.get("message", "")
            is_error = meta.get("is_error", False)

            # Se completato con successo, meta contiene il risultato
            if status == TaskStatus.SUCCESS:
                # Fix: task che ritornano dict con success=False sono in realtà fallite
                if meta.get("success") is False:
                    status = TaskStatus.FAILURE
                    progress = 100
                    message = meta.get("message", meta.get("error", "Errore"))
                    task_result = TaskResultPayload.from_celery_meta(meta)
                else:
                    progress = 100
                    message = "Completato"
                    task_result = TaskResultPayload.from_celery_meta(meta)
            else:
                task_result = None

            # Se fallito, meta potrebbe contenere l'errore
            error = None
            if status == TaskStatus.FAILURE:
                if isinstance(meta, Exception):
                    error = str(meta)
                elif isinstance(meta, dict):
                    error = meta.get("error", meta.get("error_message", str(meta)))
                else:
                    error = str(meta)
        else:
            progress = 0
            message = str(meta) if meta else ""
            task_result = None
            error = str(meta) if status == TaskStatus.FAILURE else None
        
        # Estrai project_id dal risultato se disponibile
        project_id = ""
        if isinstance(task_result, TaskResultPayload):
            project_id = task_result.project_id
        # Fallback alla cache locale (risolve PENDING prima che il worker scriva metadata
        if not project_id:
            project_id = self._task_project_cache.get(task_id, "")
        
        # Determina task_type dal nome del task Celery
        # Nomi registrati: "pipeline.execute", "pipeline.preview", "carica.execute", "tasks.execute_enrichment"
        task_type = TaskType.EXECUTE  # default più sensato per pipeline
        if hasattr(result, 'name') and result.name:
            name_lower = result.name.lower()
            if 'carica' in name_lower:
                task_type = TaskType.CARICA
            elif 'enrichment' in name_lower or 'enrich' in name_lower:
                task_type = TaskType.ENRICHMENT
            elif 'preview' in name_lower:
                task_type = TaskType.PREVIEW
            elif 'pipeline' in name_lower or 'execute' in name_lower:
                task_type = TaskType.EXECUTE
        
        return TaskInfo(
            task_id=task_id,
            task_type=task_type,
            project_id=project_id,
            status=status,
            progress=progress,
            message=message,
            result=task_result if status == TaskStatus.SUCCESS else None,
            error=error
        )
    
    # ANNULLAMENTO TASK
    
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
            logger.error(f"[CRASH] Errore annullamento task {task_id}: {e}", exc_info=True)
            return False
    
    # RECUPERA RISULTATO
    
    def get_task_result(self, task_id: str) -> Optional[TaskResultPayload]:
        """
        Ottiene il risultato di un task completato.
        
        Args:
            task_id: ID del task
            
        Returns:
            Risultato del task o None se non completato
        """
        result = AsyncResult(task_id, app=self.app)
        
        if result.state == "SUCCESS" and isinstance(result.result, Mapping):
            return TaskResultPayload.from_celery_meta(result.result)
        return None
    
    # LISTA TASK ATTIVI
    
    def list_active_tasks(self) -> List[ActiveTaskInfo]:
        """
        Lista i task attualmente in esecuzione.
        
        Returns:
            Lista di task attivi
        """
        try:
            inspect = self.app.control.inspect()
            
            active = inspect.active() or {}
            reserved = inspect.reserved() or {}
            
            tasks: List[ActiveTaskInfo] = []
            
            # Task in esecuzione
            for worker, worker_tasks in active.items():
                for task in worker_tasks:
                    tasks.append(ActiveTaskInfo.from_celery_task(worker, task, "active"))
            
            # Task in coda
            for worker, worker_tasks in reserved.items():
                for task in worker_tasks:
                    tasks.append(ActiveTaskInfo.from_celery_task(worker, task, "reserved"))
            
            return tasks
            
        except Exception as e:
            logger.error(f"[CRASH] Errore listing task: {e}", exc_info=True)
            return []
    
    # PULIZIA TASK SCADUTI

    def cleanup_old_results(self, max_age_hours: int = 24) -> int:
        """
        Hook di manutenzione manuale: pulisce risultati task più vecchi di max_age_hours.

        Non viene chiamato automaticamente — Redis gestisce la scadenza via
        CELERY_RESULT_EXPIRES. Da invocare esplicitamente da uno script di
        manutenzione se si vuole pulizia anticipata.

        Args:
            max_age_hours: Età massima in ore

        Returns:
            Numero di risultati eliminati (sempre 0: Redis gestisce scadenza)
        """
        logger.info(f"Cleanup manuale risultati task più vecchi di {max_age_hours}h")
        return 0  # Redis gestisce scadenza automatica via CELERY_RESULT_EXPIRES


# Singleton
task_service = TaskService()
