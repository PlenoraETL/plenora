"""
Orchestratore pipeline — entry point unico per l'esecuzione.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol

from app import config as app_config
from app.core.connections.service import ConnectionService
from app.core.estrai.query_generator import QueryGenerator
from app.core.orchestrator import task_service as task_service_module
from app.core.projects.service import ProjectService
from app.core.projects.canvas_models import CanvasState
from app.core.resources import memory_guard
from app.core.query_execution.query_executor import QueryExecutor
from app.shared import error_codes
from app.shared.contracts.payloads import JsonObject
from app.shared.execution.executor_protocol import CanvasExecutorProtocol, ExecutionResult
from app.shared.execution import carica_dispatcher
from app.utils.logger import get_logger

from app.config import settings
from app.core.artifacts import PipelineArtifact

logger = get_logger(__name__)
ExecutorDispatch = Mapping[str, CanvasExecutorProtocol]


def canvas_validation_gate_blocks(canvas_validated: bool) -> bool:
    """True se il gate canvas-validation (§12) deve bloccare l'esecuzione.

    Gate ON (``CANVAS_VALIDATION_GATE_ENABLED``, default True) + progetto NON
    validato (stato persistito ``canvas_validated``, tenuto fresco dal cascade
    cross-progetto + settato da POST /validate). Usato agli entry-point top-level
    (handler API /execute, scheduler), NON nei metodi di esecuzione condivisi
    (che girano anche per sub-esecuzioni interne valide).
    """
    if not settings.get("CANVAS_VALIDATION_GATE_ENABLED", False):
        return False
    return not canvas_validated


class EstraiExecutorProtocol(CanvasExecutorProtocol, Protocol):
    def execute(
        self,
        project_id: str,
        canvas: CanvasState,
        run_id: Optional[str] = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> ExecutionResult | JsonObject:
        ...


class TransformExecutorProtocol(CanvasExecutorProtocol, Protocol):
    def execute(
        self,
        project_id: str,
        canvas: CanvasState,
        force_recalculate: bool = False,
        run_id: Optional[str] = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> ExecutionResult | JsonObject:
        ...


class OrchestratorService:
    """
    Gestisce l'esecuzione a cascata dei progetti.
    Se un progetto è di tipo 'manipola', esegue ricorsivamente i progetti sorgente.
    Dopo l'esecuzione, se presente un nodo CARICA, avvia il task di caricamento.
    """
    
    def __init__(
        self, 
        project_service: ProjectService, 
        executor: QueryExecutor, 
        generator: QueryGenerator,
        connection_service: ConnectionService,
        features_service=None,
        *,
        estrai_executor: EstraiExecutorProtocol,
        manipola_executor: TransformExecutorProtocol,
        enrich_executor: TransformExecutorProtocol,
    ):
        self.project_service = project_service
        self.executor = executor
        self.generator = generator
        self.connection_service = connection_service
        self.features_service = features_service

        self.estrai_executor = estrai_executor
        self.manipola_executor = manipola_executor
        self.enrich_executor = enrich_executor

        for _mtype, _exc in [
            ("estrai",      self.estrai_executor),
            ("manipola",    self.manipola_executor),
            ("arricchisci", self.enrich_executor),
        ]:
            assert isinstance(_exc, CanvasExecutorProtocol), (
                f"Executor for '{_mtype}' ({type(_exc).__name__}) does not "
                f"satisfy CanvasExecutorProtocol (missing execute or preview)"
            )

    @property
    def _executor_dispatch(self) -> ExecutorDispatch:
        """Typed dispatch table: module_type → CanvasExecutorProtocol.


        Built as a property (not cached) so that tests replacing a concrete
        executor attribute (e.g. ``svc.estrai_executor = MagicMock``) see
        the updated object without patching the dict separately.

        Consumers:
        - ``execute_preview``: dispatches ``preview`` entirely through this table.
        - ``execute_pipeline``: uses this table as the canonical "supported
          module types" registry (guard only); execute calls still go through
          the concrete attributes to pass engine-specific kwargs that are not
          part of the Protocol's minimal surface (e.g. ``force_recalculate``).
        """
        return {
            "estrai":      self.estrai_executor,
            "manipola":    self.manipola_executor,
            "arricchisci": self.enrich_executor,
        }

    # PUNTI DI INGRESSO
    # Metodi chiamati da main.py per eseguire o visualizzare in anteprima

    def _check_pipeline_memory(self) -> None:
        # RSS guard pre-stage: se il worker e' gia' sovraccarico, fallisce
        # early con 503 invece di avviare una pipeline pesante che farebbe
        # OOM kernel SIGKILL. Guard disabilitabile via cap <= 0.
        try:
            _rss_cap = int(
                app_config.settings.get("WORKER_RSS_MAX_BYTES", 1_500_000_000)
            )
            memory_guard.check_worker_rss(
                cap_bytes=_rss_cap,
                stage="pipeline_execute",
            )
        except ImportError:
            pass  # memory_guard non disponibile, skip

    def _coerce_canvas(self, canvas: CanvasState | Mapping[str, Any]) -> CanvasState:
        if isinstance(canvas, CanvasState):
            return canvas
        return CanvasState.model_validate(canvas)

    def execute_pipeline_from_contract(
        self,
        project_id: str,
        project_type: str,
        canvas: CanvasState | Mapping[str, Any],
        force_recalculate: bool = False,
        skip_carica: bool = False,
        run_id: str = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> PipelineArtifact:
        """Execute a data-plane contract supplied by the control plane.

        This path does not read project metadata/canvas from ``ProjectService``.
        It is the boundary a future Go control plane can call through Celery:
        the worker receives an immutable JSON canvas plus the project type and
        runs the Python data engine only.
        """
        logger.info(
            f"Esecuzione pipeline da contract: {project_id}",
            op="execute",
            project_id=project_id,
        )
        self._check_pipeline_memory()
        return self._execute_loaded_pipeline(
            project_id=project_id,
            project_type=project_type,
            canvas=self._coerce_canvas(canvas),
            force_recalculate=force_recalculate,
            skip_carica=skip_carica,
            run_id=run_id,
            intermediate_mode_override=intermediate_mode_override,
        )

    def execute_pipeline(self, project_id: str, force_recalculate: bool = False, skip_carica: bool = False, run_id: str = None, intermediate_mode_override: Optional[str] = None) -> PipelineArtifact:
        """Punto di ingresso principale: decide se estrarre, manipolare o arricchire.

        Ritorna un `PipelineArtifact` (subclass di dict, dict-like
        compat con i consumer legacy che leggono `result["success"]`
        ecc.). I consumer nuovi possono usare le proprieta' tipizzate
        `result.success`, `result.artifact_kind`, `result.to_run_artifact_ref`.

        Args:
            project_id: ID del progetto da eseguire
            force_recalculate: Se True, forza il ricalcolo anche se ci sono risultati cached
            skip_carica: Se True, non avvia il task CARICA anche se presente nel canvas
                         (usato quando chiamato dal task CARICA stesso per evitare loop)
            run_id: ID run intermediate storage (opzionale, passato da pipeline_task)
        """
        logger.info(f"Esecuzione pipeline progetto: {project_id}", op="execute", project_id=project_id)
        self._check_pipeline_memory()

        metadata = self.project_service.get_project_metadata(project_id)
        canvas = self.project_service.get_project_canvas(project_id)

        if not metadata or not canvas:
            return PipelineArtifact.failure(
                project_id=project_id,
                message=f"Progetto {project_id} non trovato",
            )

        # NB: il gate canvas-validation (§12) NON vive qui: execute_pipeline è
        # chiamato anche da sub-esecuzioni interne (carica, arricchisci-upstream)
        # che gatererebbe erroneamente. Il gate sta agli entry-point top-level:
        # l'handler API POST /{id}/execute (fail-fast) e ExecutionManager (scheduler).
        return self._execute_loaded_pipeline(
            project_id=project_id,
            project_type=metadata.project_type,
            canvas=canvas,
            force_recalculate=force_recalculate,
            skip_carica=skip_carica,
            run_id=run_id,
            intermediate_mode_override=intermediate_mode_override,
        )

    def _execute_loaded_pipeline(
        self,
        *,
        project_id: str,
        project_type: str,
        canvas: CanvasState,
        force_recalculate: bool,
        skip_carica: bool,
        run_id: str = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> PipelineArtifact:
        if project_type not in self._executor_dispatch:
            return PipelineArtifact.failure(
                project_id=project_id,
                message=f"Tipo progetto '{project_type}' non supportato",
            )

        if project_type == "estrai":
            executor_result = self.estrai_executor.execute(
                project_id, canvas, run_id=run_id,
                intermediate_mode_override=intermediate_mode_override,
            )
        elif project_type == "manipola":
            executor_result = self.manipola_executor.execute(
                project_id, canvas, force_recalculate, run_id=run_id,
                intermediate_mode_override=intermediate_mode_override,
            )
        else:  # "arricchisci" — already validated by dispatch guard above
            executor_result = self.enrich_executor.execute(
                project_id, canvas, force_recalculate, run_id=run_id,
                intermediate_mode_override=intermediate_mode_override,
            )

        exec_result = ExecutionResult.from_executor_dict(executor_result)
        artifact = PipelineArtifact.from_execution_result(exec_result, project_id=project_id)

        # Se la pipeline ha avuto successo, controlla se c'è un nodo CARICA
        # skip se chiamato dal task CARICA stesso per evitare loop
        # PipelineArtifact e' dict-like quindi `artifact["carica"] = ...`
        # funziona come prima del refactor
        if artifact.success and not skip_carica:
            carica_info = self._execute_carica_if_present(project_id, canvas)
            if carica_info:
                artifact["carica"] = carica_info

        return artifact

    def execute_preview(self, project_id: str, limit: int = 100) -> JsonObject:
        """Esegue una preview dei dati senza salvare file.

        NB: la preview non produce artifact, quindi resta un dict raw —
        `PipelineArtifact` modella l'output di una run, non di un preview.
        """
        logger.info(f"Preview progetto: {project_id} (limit={limit})", op="preview", project_id=project_id)

        metadata = self.project_service.get_project_metadata(project_id)
        canvas = self.project_service.get_project_canvas(project_id)

        if not metadata or not canvas:
            return {"success": False, "message": f"Progetto {project_id} non trovato"}

        executor = self._executor_dispatch.get(metadata.project_type)
        if executor is None:
            return {"success": False, "message": f"Preview non supportata per tipo '{metadata.project_type}'"}
        return executor.preview(project_id, canvas, limit)

    # CARICA — Rilevamento e avvio task caricamento su database

    def _execute_carica_if_present(self, project_id: str, canvas: CanvasState) -> Optional[JsonObject]:
        """
        Controlla se nel canvas c'è un nodo CARICA collegato al nodo Output.
        Se presente, avvia il task Celery per il caricamento su database.

        Delega il loop di lancio al launcher canonico condiviso
        (``task_service.launch_carica_tasks_for_canvas``), usato anche dal path
        enrichment standalone. Policy d'errore all-or-nothing (``resilient=False``):
        un fallimento di lancio propaga e viene tradotto qui nell'errore
        sanitizzato ``CARICA_LAUNCH_FAILED`` (nessun dettaglio tecnico al FE).
        """
        try:
            return task_service_module.task_service.launch_carica_tasks_for_canvas(
                project_id,
                canvas,
                output_contract_loader=self.project_service.get_project_output_contract,
                output_format="geojson",
                triggered_by="pipeline",
                resilient=False,
                on_unconnected=lambda node: logger.warning(
                    f"Nodo {carica_dispatcher.node_type(node)} "
                    f"({carica_dispatcher.node_id(node)}) non collegato al nodo Output"
                ),
                on_missing_output=lambda: logger.warning(
                    "Nodo Output (result) non trovato nel canvas"
                ),
            )

        # intent: optional feature — pipeline success indipendente da launch CARICA
        # outcome: fail-open sanitizzato (stringa opaca al FE, dettaglio tecnico solo nel log)
        except Exception as e:
            logger.error(f"[CRASH] Errore avvio task CARICA: {e}", op="carica", project_id=project_id, exc_info=True)
            return {
                "error": error_codes.CARICA_LAUNCH_FAILED,
                "message": "Errore nell'avvio del caricamento (consulta log)"
            }
