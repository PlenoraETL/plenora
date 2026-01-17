import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import List, Optional
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

from models import (
    ProjectMetadata, 
    ProjectCreate, 
    ProjectList,
    CanvasState
)


class ProjectService:
    """Servizio per gestione progetti"""
    
    def __init__(self):
            self.PROJECTS_DIR = settings.PROJECTS_DIR
    
    def _generate_project_id(self, name: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '_')).strip()
        safe_name = safe_name.replace(' ', '_')[:30]
        return f"{safe_name}_{timestamp}_{short_uuid}"
    
    def _get_project_path(self, project_id: str) -> Path:
        """Ottieni path cartella progetto"""
        return self.PROJECTS_DIR / project_id
    
    def _get_metadata_path(self, project_id: str) -> Path:
        """Ottieni path file metadata.json"""
        return self._get_project_path(project_id) / "metadata.json"
    
    def _get_canvas_path(self, project_id: str) -> Path:
        """Ottieni path file canvas.json"""
        return self._get_project_path(project_id) / "canvas.json"
    
    def create_project(self, project_data: ProjectCreate) -> ProjectMetadata:
        project_id = self._generate_project_id(project_data.name)
        project_path = self._get_project_path(project_id)
        project_path.mkdir(parents=True, exist_ok=True)
        
        now = datetime.now()
        metadata = ProjectMetadata(
            id=project_id,
            name=project_data.name,
            description=project_data.description,
            project_type=project_data.project_type, # <--- Salvataggio del tipo
            default_connection_id=project_data.default_connection_id,
            created_at=now,
            updated_at=now
        )
        
        # Salva metadata
        with open(self._get_metadata_path(project_id), "w", encoding="utf-8") as f:
            f.write(metadata.model_dump_json(indent=2))
            
        # Crea canvas vuoto
        canvas = CanvasState(nodes=[], edges=[])
        with open(self._get_canvas_path(project_id), "w", encoding="utf-8") as f:
            f.write(canvas.model_dump_json(indent=2))
            
        return metadata

    
    def list_projects(self) -> ProjectList:
        projects = []
        if not self.PROJECTS_DIR.exists():
            return ProjectList(projects=[])
            
        for project_dir in self.PROJECTS_DIR.iterdir():
            if project_dir.is_dir():
                metadata_path = project_dir / "metadata.json"
                if metadata_path.exists():
                    try:
                        with open(metadata_path, "r", encoding="utf-8") as f:
                            metadata = ProjectMetadata.model_validate_json(f.read())
                            projects.append(metadata)
                    except Exception as e:
                        logger.error(f"Errore lettura progetto {project_dir.name}: {e}")
        
        return ProjectList(projects=sorted(projects, key=lambda x: x.updated_at, reverse=True))
    
    def get_project_metadata(self, project_id: str) -> Optional[ProjectMetadata]:
        path = self._get_metadata_path(project_id)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return ProjectMetadata.model_validate_json(f.read())
    
    def get_project_canvas(self, project_id: str) -> Optional[CanvasState]:
        path = self._get_canvas_path(project_id)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return CanvasState.model_validate_json(f.read())
    
    def update_canvas(self, project_id: str, canvas: CanvasState) -> bool:
        if not self._get_project_path(project_id).exists():
            return False
        
        with open(self._get_canvas_path(project_id), "w", encoding="utf-8") as f:
            f.write(canvas.model_dump_json(indent=2))
        
        metadata = self.get_project_metadata(project_id)
        if metadata:
            metadata.updated_at = datetime.now()
            with open(self._get_metadata_path(project_id), "w", encoding="utf-8") as f:
                f.write(metadata.model_dump_json(indent=2))
        return True
    
    def delete_project(self, project_id: str) -> bool:
        project_path = self._get_project_path(project_id)
        if not project_path.exists():
            return False
        
        for file_path in project_path.iterdir():
            file_path.unlink()
        project_path.rmdir()
        return True
    
    def project_exists(self, project_id: str) -> bool:
        """Verifica se progetto esiste"""
        return self._get_project_path(project_id).exists()
        
    # ✅ AGGIUNGI QUESTO METODO a project_service.py

    def update_project_columns_metadata(self, project_id: str, columns_metadata: list):
        """
        Salva i metadati delle colonne (nome + tipo) nel metadata del progetto.
        
        columns_metadata: [{"name": "provincia", "dtype": "str"}, {"name": "superficie", "dtype": "num"}]
        """
        try:
            metadata_path = self.PROJECTS_DIR / project_id / "metadata.json"
            
            if not metadata_path.exists():
                logger.warning(f"Metadata non trovato per {project_id}")
                return False
            
            # Leggi metadata esistente
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            # Aggiungi/aggiorna columns_metadata
            metadata['columns_metadata'] = columns_metadata
            
            # Riscrivi
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Salvati {len(columns_metadata)} tipi colonne per {project_id}")
            return True
            
        except Exception as e:
            logger.error(f"Errore salvataggio columns_metadata: {e}")
            return False