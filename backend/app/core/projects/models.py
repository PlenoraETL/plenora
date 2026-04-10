"""
Modelli Pydantic per il modulo Projects.

CRUD progetti URBY-ETL con gestione canvas e metadata.
"""

from pydantic import BaseModel
from typing import List, Optional, Literal
from datetime import datetime


class ProjectMetadata(BaseModel):
    """Metadati di un progetto URBY-ETL."""
    id: str
    name: str
    description: str = ""
    project_type: Literal["estrai", "manipola", "arricchisci"] = "estrai"
    default_connection_id: Optional[str] = None
    group_id: str = "default"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProjectCreate(BaseModel):
    """Dati per creare un nuovo progetto."""
    name: str
    description: str = ""
    project_type: Literal["estrai", "manipola", "arricchisci"] = "estrai"
    default_connection_id: Optional[str] = None


class ProjectList(BaseModel):
    """Wrapper per la lista di progetti."""
    projects: List[ProjectMetadata]
