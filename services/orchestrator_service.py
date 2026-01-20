"""
Orchestrator Service - Entry point per l'esecuzione delle pipeline.

REFACTORED: Suddiviso in moduli separati:
- orchestrator/estrai_executor.py: Logica ESTRAI
- orchestrator/manipola_executor.py: Logica MANIPOLA  
- orchestrator/enrich_executor.py: Logica ARRICCHISCI
- orchestrator/transform_operations.py: Operazioni base
- orchestrator/spatial_operations.py: Operazioni geospaziali

Responsabilità di questo file:
- Entry point unico (execute_pipeline, execute_preview)
- Routing ai moduli specifici
- Gestione dipendenze e servizi
"""

from typing import Dict, Any

from models import CanvasState
from services.project_service import ProjectService
from services.query_executor import QueryExecutor
from services.query_generator import QueryGenerator
from services.connection_service import ConnectionService
from config import settings
from utils.logger import get_logger

from .orchestrator import (
    EstraiExecutor,
    ManipolaExecutor,
    EnrichExecutor
)

logger = get_logger(__name__)


class OrchestratorService:
    """
    Gestisce l'esecuzione a cascata dei progetti.
    Se un progetto è di tipo 'manipola', esegue ricorsivamente i progetti sorgente.
    """
    
    def __init__(
        self, 
        project_service: ProjectService, 
        executor: QueryExecutor, 
        generator: QueryGenerator,
        connection_service: ConnectionService
    ):
        self.project_service = project_service
        self.executor = executor
        self.generator = generator
        self.connection_service = connection_service
        self.results_base_dir = settings.RESULTS_DIR
        
        # Inizializza executor per ogni tipo di progetto
        self.estrai_executor = EstraiExecutor(
            project_service=project_service,
            executor=executor,
            generator=generator,
            connection_service=connection_service
        )
        
        self.manipola_executor = ManipolaExecutor(
            project_service=project_service,
            orchestrator_callback=self.execute_pipeline  # Per esecuzione ricorsiva
        )
        
        self.enrich_executor = EnrichExecutor(
            project_service=project_service
        )

    # =========================================================================
    # ENTRY POINTS
    # =========================================================================

    def execute_pipeline(self, project_id: str, force_recalculate: bool = False) -> Dict[str, Any]:
        """Punto di ingresso principale: decide se estrarre, manipolare o arricchire."""
        logger.info(f"Esecuzione pipeline progetto: {project_id}")
        logger.debug(f"Force recalculate: {force_recalculate}")
        
        metadata = self.project_service.get_project_metadata(project_id)
        canvas = self.project_service.get_project_canvas(project_id)
        
        if not metadata or not canvas:
            return {"success": False, "message": f"Progetto {project_id} non trovato"}

        logger.debug(f"Tipo progetto: {metadata.project_type}")
        
        if metadata.project_type == "estrai":
            return self.estrai_executor.execute(project_id, canvas)
        
        elif metadata.project_type == "manipola":
            return self.manipola_executor.execute(project_id, canvas, force_recalculate)
        
        elif metadata.project_type == "arricchisci":
            return self.enrich_executor.execute(project_id, canvas, force_recalculate)

        return {"success": False, "message": f"Tipo progetto '{metadata.project_type}' non supportato"}

    def execute_preview(self, project_id: str, limit: int = 100) -> Dict[str, Any]:
        """Esegue una preview dei dati senza salvare file."""
        logger.info(f"Preview progetto: {project_id} (limit={limit})")
        
        metadata = self.project_service.get_project_metadata(project_id)
        canvas = self.project_service.get_project_canvas(project_id)
        
        if not metadata or not canvas:
            return {"success": False, "message": f"Progetto {project_id} non trovato"}
        
        if metadata.project_type == "estrai":
            return self.estrai_executor.preview(project_id, canvas, limit)
        
        elif metadata.project_type == "manipola":
            return self.manipola_executor.preview(project_id, canvas, limit)
        
        else:
            return {"success": False, "message": f"Preview non supportata per tipo '{metadata.project_type}'"}

    # =========================================================================
    # UTILITY METHODS (per retrocompatibilità)
    # =========================================================================

    def _build_canvas_type_map(self, canvas: CanvasState) -> Dict[str, str]:
        """Proxy per retrocompatibilità."""
        return self.estrai_executor.build_canvas_type_map(canvas)

    def _extract_srid_from_canvas(self, canvas: CanvasState):
        """Proxy per retrocompatibilità."""
        return self.estrai_executor.extract_srid_from_canvas(canvas)