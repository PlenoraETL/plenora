"""Canonical orchestrator boundary for asynchronous Celery task management."""

from __future__ import annotations

from types import MappingProxyType
from typing import Dict, Mapping, Optional, List, Protocol, TypeAlias
from datetime import datetime, timezone
from enum import Enum
from dataclasses import dataclass
from celery.result import AsyncResult
from workers.celery_app import celery_app
from app.utils.logger import get_logger

logger = get_logger(__name__)

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]


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
class CeleryLaunchResult:
    """Minimal Celery async result contract used by the submit boundary."""

    id: str


class CeleryLaunchTask(Protocol):
    """Minimal Celery task contract for launching work."""

    def delay(self, **kwargs: JsonValue) -> CeleryLaunchResult:
        ...


@dataclass(frozen=True)
class EnrichmentTaskPayload:
    project_id: str
    canvas: dict[str, JsonValue]
    output_format: str = "csv"
    force_recalculate: bool = False
    cleanup_file_source_ids: Optional[list[str]] = None
    is_eseguibile: bool = False
    user_id: Optional[str] = None
    intermediate_mode_override: Optional[str] = None


@dataclass(frozen=True)
class CaricaTaskPayload:
    project_id: str
    canvas: dict[str, JsonValue]
    carica_node_data: dict[str, JsonValue]
    output_format: str = "geojson"
    force_recalculate: bool = False
    triggered_by: str = "manual"
    user_id: Optional[str] = None


@dataclass(frozen=True)
class PipelineTaskPayload:
    project_id: str
    force_recalculate: bool = False
    triggered_by: str = "manual"
    user_id: Optional[str] = None
    intermediate_mode_override: Optional[str] = None


@dataclass(frozen=True)
class PreviewTaskPayload:
    project_id: str
    limit: int = 100
    user_id: Optional[str] = None


TaskLaunchPayload: TypeAlias = (
    EnrichmentTaskPayload | CaricaTaskPayload | PipelineTaskPayload | PreviewTaskPayload
)


@dataclass(frozen=True)
class TaskSubmitRequest:
    """Typed request for launching a Celery task from the orchestrator."""

    celery_task: CeleryLaunchTask
    task_type: TaskType
    project_id: str
    payload: TaskLaunchPayload
    queued_message: str
    log_label: str


def task_payload_to_celery_kwargs(
    payload: TaskLaunchPayload,
) -> Mapping[str, JsonValue]:
    """Adapt typed task payloads to Celery's keyword-argument launch API."""

    if isinstance(payload, EnrichmentTaskPayload):
        return MappingProxyType(
            {
                "project_id": payload.project_id,
                "canvas": payload.canvas,
                "output_format": payload.output_format,
                "force_recalculate": payload.force_recalculate,
                "cleanup_file_source_ids": payload.cleanup_file_source_ids,
                "is_eseguibile": payload.is_eseguibile,
                "user_id": payload.user_id,
                "intermediate_mode_override": payload.intermediate_mode_override,
            }
        )
    if isinstance(payload, CaricaTaskPayload):
        return MappingProxyType(
            {
                "project_id": payload.project_id,
                "canvas": payload.canvas,
                "carica_node_data": payload.carica_node_data,
                "output_format": payload.output_format,
                "force_recalculate": payload.force_recalculate,
                "triggered_by": payload.triggered_by,
                "user_id": payload.user_id,
            }
        )
    if isinstance(payload, PipelineTaskPayload):
        return MappingProxyType(
            {
                "project_id": payload.project_id,
                "force_recalculate": payload.force_recalculate,
                "triggered_by": payload.triggered_by,
                "user_id": payload.user_id,
                "intermediate_mode_override": payload.intermediate_mode_override,
            }
        )
    if isinstance(payload, PreviewTaskPayload):
        return MappingProxyType(
            {
                "project_id": payload.project_id,
                "limit": payload.limit,
                "user_id": payload.user_id,
            }
        )

    raise TypeError(f"Unsupported task launch payload: {type(payload).__name__}")


@dataclass(frozen=True)
class TaskResultField:
    """Single public Celery result field retained for API compatibility."""

    name: str
    value: JsonValue


@dataclass(frozen=True)
class TaskResultPayload:
    """Typed core contract for Celery task result metadata."""

    success: Optional[bool] = None
    project_id: str = ""
    result_id: Optional[str] = None
    output_file: Optional[str] = None
    output_filename: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    error_message: Optional[str] = None
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    rows_count: Optional[int] = None
    rows_written: Optional[int] = None
    total_rows: Optional[int] = None
    public_fields: tuple[TaskResultField, ...] = ()

    @classmethod
    def from_celery_meta(cls, meta: Mapping[str, object]) -> "TaskResultPayload":
        return task_result_payload_from_celery_meta(meta)


def _optional_str(value: object) -> Optional[str]:
    return str(value) if value is not None else None


def _required_str(value: object) -> str:
    return str(value) if value is not None else ""


def _optional_bool(value: object) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _optional_int(value: object) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _to_json_value(nested_value) for key, nested_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_value(nested_value) for nested_value in value]
    return str(value)


def task_result_payload_from_celery_meta(meta: Mapping[str, object]) -> TaskResultPayload:
    """Normalize Celery result metadata at the Celery boundary."""

    return TaskResultPayload(
        success=_optional_bool(meta.get("success")),
        project_id=_required_str(meta.get("project_id")),
        result_id=_optional_str(meta.get("result_id")),
        output_file=_optional_str(meta.get("output_file")),
        output_filename=_optional_str(meta.get("output_filename")),
        status=_optional_str(meta.get("status")),
        message=_optional_str(meta.get("message")),
        error=_optional_str(meta.get("error")),
        error_message=_optional_str(meta.get("error_message")),
        task_id=_optional_str(meta.get("task_id")),
        run_id=_optional_str(meta.get("run_id")),
        rows_count=_optional_int(meta.get("rows_count")),
        rows_written=_optional_int(meta.get("rows_written")),
        total_rows=_optional_int(meta.get("total_rows")),
        public_fields=tuple(
            TaskResultField(name=str(key), value=_to_json_value(value))
            for key, value in meta.items()
        ),
    )


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

    def submit_task(self, request: TaskSubmitRequest) -> TaskInfo:
        """Launch a Celery task and record the pending task metadata."""

        result = request.celery_task.delay(
            **task_payload_to_celery_kwargs(request.payload)
        )

        logger.info(
            f"Creato task {request.log_label}: {result.id} "
            f"per progetto {request.project_id}"
        )

        self._task_project_cache[result.id] = request.project_id
        self._prune_cache()

        return TaskInfo(
            task_id=result.id,
            task_type=request.task_type,
            project_id=request.project_id,
            status=TaskStatus.PENDING,
            progress=0,
            message=request.queued_message,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    
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
        """Crea e avvia un task di arricchimento."""
        from workers.tasks.enrichment_task import execute_enrichment_task

        return self.submit_task(
            TaskSubmitRequest(
                celery_task=execute_enrichment_task,
                task_type=TaskType.ENRICHMENT,
                project_id=project_id,
                payload=EnrichmentTaskPayload(
                    project_id=project_id,
                    canvas=canvas,
                    output_format=output_format,
                    force_recalculate=force_recalculate,
                    cleanup_file_source_ids=cleanup_file_source_ids,
                    is_eseguibile=is_eseguibile,
                    user_id=user_id,
                    intermediate_mode_override=intermediate_mode_override,
                ),
                queued_message="Task in coda...",
                log_label="enrichment",
            )
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
        """Crea e avvia un task CARICA."""
        from workers.tasks.carica_task import execute_carica_task

        return self.submit_task(
            TaskSubmitRequest(
                celery_task=execute_carica_task,
                task_type=TaskType.CARICA,
                project_id=project_id,
                payload=CaricaTaskPayload(
                    project_id=project_id,
                    canvas=canvas,
                    carica_node_data=carica_node_data,
                    output_format=output_format,
                    force_recalculate=force_recalculate,
                    triggered_by=triggered_by,
                    user_id=user_id,
                ),
                queued_message="Task CARICA in coda...",
                log_label="carica",
            )
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
        return self.submit_task(
            TaskSubmitRequest(
                celery_task=execute_pipeline_task,
                task_type=TaskType.EXECUTE,
                project_id=project_id,
                payload=PipelineTaskPayload(
                    project_id=project_id,
                    force_recalculate=force_recalculate,
                    triggered_by=triggered_by,
                    user_id=user_id,
                    intermediate_mode_override=intermediate_mode_override,
                ),
                queued_message="Pipeline in coda...",
                log_label="execute",
            )
        )

    def create_preview_task(
        self,
        project_id: str,
        limit: int = 100,
        user_id: Optional[str] = None,
    ) -> TaskInfo:
        """Crea e avvia un task di preview pipeline."""
        from workers.tasks.pipeline_task import preview_pipeline_task
        return self.submit_task(
            TaskSubmitRequest(
                celery_task=preview_pipeline_task,
                task_type=TaskType.PREVIEW,
                project_id=project_id,
                payload=PreviewTaskPayload(
                    project_id=project_id,
                    limit=limit,
                    user_id=user_id,
                ),
                queued_message="Preview in coda...",
                log_label="preview",
            )
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
