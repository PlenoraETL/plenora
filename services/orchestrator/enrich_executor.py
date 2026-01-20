"""
Enrich Executor - Gestisce esecuzione progetti ARRICCHISCI.

Responsabilità:
- Esecuzione pipeline di arricchimento tramite API esterne
- Integrazione con api_connection_service
"""

from typing import Dict, Any

from models import CanvasState
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class EnrichExecutor:
    """Gestisce esecuzione per progetti ARRICCHISCI."""
    
    def __init__(self, project_service):
        self.project_service = project_service
        self.results_base_dir = settings.RESULTS_DIR

    def execute(
        self, 
        project_id: str, 
        canvas: CanvasState, 
        force_recalculate: bool = False,
        format: str = 'csv'
    ) -> Dict[str, Any]:
        """Esegue la pipeline di arricchimento tramite API esterne."""
        logger.info(f"Esecuzione progetto Arricchisci: {project_id}")
        
        from services.api_connection_service import api_connection_service
        
        canvas_dict = canvas.model_dump() if hasattr(canvas, 'model_dump') else canvas
        
        result = api_connection_service.execute_enrichment(
            project_id=project_id,
            canvas=canvas_dict,
            format=format,
            force_recalculate=force_recalculate
        )
        
        if result.get("success"):
            return {
                "success": True,
                "output_path": str(self.results_base_dir / project_id / result.get("output_file", "")),
                "rows_count": result.get("rows_processed", 0),
                "columns": result.get("columns", []),
                "message": f"Arricchimento completato: {result.get('rows_processed', 0)} righe processate"
            }
        else:
            return {
                "success": False,
                "message": result.get("error", "Errore durante l'arricchimento")
            }
