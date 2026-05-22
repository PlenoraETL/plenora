"""
Modelli Pydantic per il modulo Projects.

CRUD progetti PLENORA-ETL con gestione canvas e metadata.
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal
from datetime import datetime


def _coerce_datetime(value: object) -> Optional[datetime]:
    """Normalizza datetime|str|None alla forma canonica `Optional[datetime]`.

    Boundary cleanup B1 (audit P2): pre-fix `ProjectRow` esponeva
    `datetime | str | None` al DAL, ma `ProjectMetadata` Pydantic
    dichiarava `Optional[datetime]`. Senza validator la stringa ISO
    veniva passata as-is e Pydantic rifiutava su strict mode.

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

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _validate_timestamps(cls, value: object) -> Optional[datetime]:
        return _coerce_datetime(value)


class ProjectCreate(BaseModel):
    """Dati per creare un nuovo progetto."""
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field("", max_length=2000)
    project_type: Literal["estrai", "manipola", "arricchisci"] = "estrai"
    default_connection_id: Optional[str] = Field(None, max_length=100)


class ProjectList(BaseModel):
    """Wrapper per la lista di progetti."""
    projects: List[ProjectMetadata]
