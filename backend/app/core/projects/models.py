"""
Modelli Pydantic per il modulo Projects.

CRUD progetti PLENORA-ETL con gestione canvas e metadata.
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal
from datetime import datetime


def _coerce_datetime(value: object) -> Optional[datetime]:
    """Normalizza datetime|str|None alla forma canonica `Optional[datetime]`.

    Il DAL puo' esporre `datetime | str | None`, mentre `ProjectMetadata`
    dichiara `Optional[datetime]`.

    Centralizzazione: helper modulo (Go-friendly) usato dal validator
    Pydantic del `ProjectMetadata`. Il DAL `ProjectRow` resta `datetime
    | str | None` perche' psycopg2 puo' ritornare il valore in entrambe
    le forme a seconda del driver build e della column type cast.
    """
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # `fromisoformat` supporta sia `2026-05-22T07:00:00+00:00` che
        # `2026-05-22T07:00:00`. Python 3.11+ accetta anche `Z` come
        # alias UTC; 3.10 richiede il replace esplicito.
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        return datetime.fromisoformat(normalized)
    raise TypeError(
        f"created_at/updated_at deve essere datetime, str ISO o None; "
        f"ricevuto {type(value).__name__}"
    )


class ProjectMetadata(BaseModel):
    """Metadati di un progetto PLENORA-ETL."""
    id: str
    name: str
    description: str = ""
    project_type: Literal["estrai", "manipola", "arricchisci"] = "estrai"
    default_connection_id: Optional[str] = None
    group_id: str = "default"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Stato di validazione canvas persistito: True dopo una
    # POST /validate senza errori; resettato a False a ogni salvataggio del canvas.
    canvas_validated: bool = False
    # Tag liberi per organizzare i progetti nella dashboard (#10).
    tags: List[str] = Field(default_factory=list)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _validate_timestamps(cls, value: object) -> Optional[datetime]:
        return _coerce_datetime(value)


class CanvasVersionRef(BaseModel):
    """Riferimento tipizzato a una versione salvata del canvas (#12).

    Boundary tipizzato (no broad dict return): id sempre presente; gli altri
    campi sono popolati in elenco e assenti nella risposta di creazione.
    """
    id: str
    label: Optional[str] = None
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None


class ProjectCreate(BaseModel):
    """Dati per creare un nuovo progetto."""
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field("", max_length=2000)
    project_type: Literal["estrai", "manipola", "arricchisci"] = "estrai"
    default_connection_id: Optional[str] = Field(None, max_length=100)


class ProjectList(BaseModel):
    """Wrapper per la lista di progetti."""
    projects: List[ProjectMetadata]
