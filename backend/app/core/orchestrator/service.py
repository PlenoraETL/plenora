"""
Orchestratore pipeline — entry point unico per l'esecuzione.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

from typing import Dict, Any, Optional

from app.models import CanvasState
from app.core.projects.service import ProjectService
from app.db.query_executor import QueryExecutor
from app.core.estrai.query_generator import QueryGenerator
from app.core.connections.service import ConnectionService
from app.config import settings
from app.shared import error_codes
from app.shared.execution.executor_protocol import CanvasExecutorProtocol, ExecutionResult
from app.utils.logger import get_logger

# Import dai moduli separati
from app.core.estrai.executor import EstraiExecutor
from app.core.manipola.executor import ManipolaExecutor
from app.core.arricchisci.executor import EnrichExecutor
from app.core.artifacts import PipelineArtifact

logger = get_logger(__name__)


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
        features_service=None
    ):
        self.project_service = project_service
        self.executor = executor
        self.generator = generator
        self.connection_service = connection_service
        self.features_service = features_service
        self.results_base_dir = settings.RESULTS_DIR
        
        # Inizializza executor per ogni tipo di progetto
        self.estrai_executor = EstraiExecutor(
            project_service=project_service,
            executor=executor,
            generator=generator,
            connection_service=connection_service,
            features_service=features_service
        )
        
        self.manipola_executor = ManipolaExecutor(
            project_service=project_service,
            orchestrator_callback=self.execute_pipeline  # Per esecuzione ricorsiva
        )

        self.enrich_executor = EnrichExecutor(
            project_service=project_service
        )

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
    def _executor_dispatch(self) -> Dict[str, CanvasExecutorProtocol]:
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

        # RSS guard pre-stage: se il worker e' gia' sovraccarico, fallisce
        # early con 503 invece di avviare una pipeline pesante che farebbe
        # OOM kernel SIGKILL. Guard disabilitabile via cap <= 0
        try:
            from app.shared.memory_guard import check_worker_rss
            from app.config import settings as _settings
            _rss_cap = int(_settings.get("WORKER_RSS_MAX_BYTES", 1_500_000_000))
            check_worker_rss(cap_bytes=_rss_cap, stage="pipeline_execute")
        except ImportError:
            pass  # memory_guard non disponibile, skip

        metadata = self.project_service.get_project_metadata(project_id)
        canvas = self.project_service.get_project_canvas(project_id)

        if not metadata or not canvas:
            return PipelineArtifact.failure(
                project_id=project_id,
                message=f"Progetto {project_id} non trovato",
            )


        if metadata.project_type not in self._executor_dispatch:
            return PipelineArtifact.failure(
                project_id=project_id,
                message=f"Tipo progetto '{metadata.project_type}' non supportato",
            )

        if metadata.project_type == "estrai":
            executor_result = self.estrai_executor.execute(
                project_id, canvas, run_id=run_id,
                intermediate_mode_override=intermediate_mode_override,
            )
        elif metadata.project_type == "manipola":
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

    def execute_preview(self, project_id: str, limit: int = 100) -> Dict[str, Any]:
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

    def _execute_carica_if_present(self, project_id: str, canvas: CanvasState) -> Optional[Dict[str, Any]]:
        """
        Controlla se nel canvas c'è un nodo CARICA collegato al nodo Output.
        Se presente, avvia il task Celery per il caricamento su database.
        """
        carica_nodes = self._find_all_carica_nodes(canvas)
        if not carica_nodes:
            return None

        logger.info(f"Trovati {len(carica_nodes)} nodi CARICA, avvio task", op="carica", project_id=project_id)

        try:
            from app.core.orchestrator.task_service import task_service
            tasks = []
            # Campi di identità del nodo che NON devono essere sovrascritti dal
            # merge con `data{}`. Se l'utente, per errore o malizia, mette
            # `id`/`type`/... dentro `data`, ignoriamo: cambierebbe l'identità
            # del nodo (es. far passare un nodo per un altro tipo, o cambiare
            # l'id che è la chiave usata dal task carica
            _NODE_RESERVED_FIELDS = {
                'id', 'type', 'position', 'selected', 'dragging',
                'measured', 'positionAbsolute', 'width', 'height',
            }
            for carica_node in carica_nodes:
                node_dict = carica_node.model_dump() if hasattr(carica_node, 'model_dump') else vars(carica_node)
                # data{} è il campo annidato nel nodo canvas con tutta la configurazione CARICA
                # Merge: node_dict (top-level) + data{} (configurazione) — data{} ha priorità
                # ma SOLO sui campi non riservati (vedi _NODE_RESERVED_FIELDS
                nested_data = node_dict.get('data') or carica_node.data or {}
                if not isinstance(nested_data, dict):
                    nested_data = {}
                safe_nested = {k: v for k, v in nested_data.items() if k not in _NODE_RESERVED_FIELDS}
                carica_node_data = {**node_dict, **safe_nested}
                # N11: arricchisci canvas_types da projects.columns_metadata
                # per evitare che CARICA perda il tipo semantico quando MANIPOLA
                # promuove silenziosamente int->float per NaN
                from app.shared.execution.canvas_types_enricher import enrich_canvas_types_from_metadata
                enrich_canvas_types_from_metadata(
                    carica_node_data,
                    project_id,
                    self.project_service.get_project_columns_metadata,
                )
                logger.info(
                    f"Nodo {carica_node.id} — connection_id: '{carica_node_data.get('connection_id', 'MANCANTE')}', "
                    f"dest_type: '{carica_node_data.get('dest_type', 'MANCANTE')}', "
                    f"table: '{carica_node_data.get('table_name', 'MANCANTE')}', "
                    f"data_keys: {list(nested_data.keys()) if nested_data else 'VUOTO'}",
                    op="carica", project_id=project_id
                )
                task_info = task_service.create_carica_task(
                    project_id=project_id,
                    canvas=canvas.model_dump(),
                    carica_node_data=carica_node_data,
                    output_format="geojson",
                    force_recalculate=False,
                    triggered_by="pipeline"
                )
                logger.info(f"Task CARICA avviato: {task_info.task_id} (nodo: {carica_node.id})", op="carica", project_id=project_id)
                tasks.append({"task_id": task_info.task_id, "node_id": carica_node.id})

            return {
                "tasks": tasks,
                "message": f"{len(tasks)} task CARICA avviati"
            }

        # intent: optional feature — pipeline success indipendente da launch CARICA
        # outcome: fail-open sanitizzato (stringa opaca al FE, dettaglio tecnico solo nel log
        except Exception as e:
            logger.error(f"[CRASH] Errore avvio task CARICA: {e}", op="carica", project_id=project_id, exc_info=True)
            return {
                "error": error_codes.CARICA_LAUNCH_FAILED,
                "message": "Errore nell'avvio del caricamento (consulta log)"
            }

    def _find_carica_node(self, canvas: CanvasState) -> Optional[Any]:
        """Trova il primo nodo CARICA collegato all'output (legacy)."""
        nodes = self._find_all_carica_nodes(canvas)
        return nodes[0] if nodes else None

    def _find_all_carica_nodes(self, canvas: CanvasState) -> list:
        """Trova tutti i nodi CARICA collegati al nodo Output."""
        result = []
        for node in canvas.nodes:
            if node.type in ('carica', 'carica_arcgis', 'datasource_node'):
                if self._is_connected_to_output(node, canvas):
                    result.append(node)
                else:
                    logger.warning(f"Nodo {node.type} ({node.id}) non collegato al nodo Output")
        return result

    def _is_connected_to_output(self, carica_node: Any, canvas: CanvasState) -> bool:
        """Verifica che il nodo CARICA sia collegato al nodo Output (result)."""
        output_node_id = next(
            (n.id for n in canvas.nodes if n.type == 'result'), None
        )

        if not output_node_id:
            logger.warning("Nodo Output (result) non trovato nel canvas")
            return False

        return any(
            e.source == output_node_id and e.target == carica_node.id
            for e in canvas.edges
        )
