"""
Orchestratore pipeline — entry point unico per l'esecuzione.

Architettura
============
Questo modulo è il punto di ingresso per tutte le esecuzioni di pipeline.
Riceve un CanvasState dal frontend e lo instrada al modulo corretto
in base al tipo di progetto.

Routing:
- Tipo ESTRAI → core/estrai/executor.py (generazione SQL + esecuzione query)
- Tipo MANIPOLA → core/manipola/executor.py (pipeline trasformazione DataFrame)
- Tipo ARRICCHISCI → core/arricchisci/executor.py (arricchimento via API)

Post-esecuzione:
- Se presente nodo CARICA → avvia task asincrono per caricamento su database

Metodi principali:
- execute_pipeline(canvas, project_id, module_type) → esecuzione completa
- execute_preview(canvas, project_id, module_type) → anteprima con LIMIT

Questo file è intenzionalmente snello: tutta la logica di business è nei
moduli specializzati. L'orchestratore si limita a risolvere le dipendenze
(ProjectService, ConnectionService, etc.) e delegare.

Supporta anche il ricalcolo a cascata: quando un progetto MANIPOLA dipende
da un progetto ESTRAI sorgente, l'orchestratore può rieseguire il sorgente
prima di procedere con la trasformazione (force_recalculate=True).
"""

from typing import Dict, Any, Optional

from app.models import CanvasState
from app.services.project_service import ProjectService
from app.db.query_executor import QueryExecutor
from app.core.estrai.query_generator import QueryGenerator
from app.services.connection_service import ConnectionService
from app.config import settings
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

    # =========================================================================
    # PUNTI DI INGRESSO
    # Metodi chiamati da main.py per eseguire o visualizzare in anteprima.
    # =========================================================================

    def execute_pipeline(self, project_id: str, force_recalculate: bool = False, skip_carica: bool = False, run_id: str = None) -> PipelineArtifact:
        """Punto di ingresso principale: decide se estrarre, manipolare o arricchire.

        Ritorna un `PipelineArtifact` (subclass di dict, dict-like
        compat con i consumer legacy che leggono `result["success"]`
        ecc.). I consumer nuovi possono usare le proprieta' tipizzate
        `result.success`, `result.artifact_kind`, `result.to_run_artifact_ref()`.

        Args:
            project_id: ID del progetto da eseguire
            force_recalculate: Se True, forza il ricalcolo anche se ci sono risultati cached
            skip_carica: Se True, non avvia il task CARICA anche se presente nel canvas
                         (usato quando chiamato dal task CARICA stesso per evitare loop)
            run_id: ID run intermediate.db (opzionale, passato da pipeline_task)
        """
        logger.info(f"Esecuzione pipeline progetto: {project_id}", op="execute", project_id=project_id)

        metadata = self.project_service.get_project_metadata(project_id)
        canvas = self.project_service.get_project_canvas(project_id)

        if not metadata or not canvas:
            return PipelineArtifact.failure(
                project_id=project_id,
                message=f"Progetto {project_id} non trovato",
            )


        # Esegui la pipeline in base al tipo
        result = None
        if metadata.project_type == "estrai":
            result = self.estrai_executor.execute(project_id, canvas, run_id=run_id)

        elif metadata.project_type == "manipola":
            result = self.manipola_executor.execute(project_id, canvas, force_recalculate, run_id=run_id)

        elif metadata.project_type == "arricchisci":
            result = self.enrich_executor.execute(project_id, canvas, force_recalculate, run_id=run_id)

        else:
            return PipelineArtifact.failure(
                project_id=project_id,
                message=f"Tipo progetto '{metadata.project_type}' non supportato",
            )

        # Wrap il dict raw degli executor in PipelineArtifact (preserva
        # tutti i campi originali, aggiunge le proprieta' tipizzate).
        artifact = PipelineArtifact.from_executor_result(result, project_id=project_id)

        # Se la pipeline ha avuto successo, controlla se c'è un nodo CARICA
        # (skip se chiamato dal task CARICA stesso per evitare loop).
        # PipelineArtifact e' dict-like quindi `artifact["carica"] = ...`
        # funziona come prima del refactor.
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

        if metadata.project_type == "estrai":
            return self.estrai_executor.preview(project_id, canvas, limit)

        elif metadata.project_type == "manipola":
            return self.manipola_executor.preview(project_id, canvas, limit)

        elif metadata.project_type == "arricchisci":
            return self.enrich_executor.preview(project_id, canvas, limit)

        else:
            return {"success": False, "message": f"Preview non supportata per tipo '{metadata.project_type}'"}

    # =========================================================================
    # CARICA — Rilevamento e avvio task caricamento su database
    # =========================================================================

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
            from app.services.task_service import task_service
            tasks = []
            # Campi di identità del nodo che NON devono essere sovrascritti dal
            # merge con `data{}`. Se l'utente, per errore o malizia, mette
            # `id`/`type`/... dentro `data`, ignoriamo: cambierebbe l'identità
            # del nodo (es. far passare un nodo per un altro tipo, o cambiare
            # l'id che è la chiave usata dal task carica).
            _NODE_RESERVED_FIELDS = {
                'id', 'type', 'position', 'selected', 'dragging',
                'measured', 'positionAbsolute', 'width', 'height',
            }
            for carica_node in carica_nodes:
                node_dict = carica_node.model_dump() if hasattr(carica_node, 'model_dump') else vars(carica_node)
                # data{} è il campo annidato nel nodo canvas con tutta la configurazione CARICA
                # Merge: node_dict (top-level) + data{} (configurazione) — data{} ha priorità
                # ma SOLO sui campi non riservati (vedi _NODE_RESERVED_FIELDS).
                nested_data = node_dict.get('data') or carica_node.data or {}
                if not isinstance(nested_data, dict):
                    nested_data = {}
                safe_nested = {k: v for k, v in nested_data.items() if k not in _NODE_RESERVED_FIELDS}
                carica_node_data = {**node_dict, **safe_nested}
                # N11: arricchisci canvas_types da projects.columns_metadata
                # per evitare che CARICA perda il tipo semantico quando MANIPOLA
                # promuove silenziosamente int->float per NaN.
                from app.shared.canvas_types_enricher import enrich_canvas_types_from_metadata
                enrich_canvas_types_from_metadata(carica_node_data, project_id)
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
        # outcome: fail-open sanitizzato (stringa opaca al FE, dettaglio tecnico solo nel log)
        except Exception as e:
            logger.error(f"[CRASH] Errore avvio task CARICA: {e}", op="carica", project_id=project_id, exc_info=True)
            return {
                "error": "carica_launch_failed",
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

