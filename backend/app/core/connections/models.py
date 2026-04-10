"""
Modelli Pydantic per il modulo Connections.

CRUD connessioni database (PostgreSQL, MySQL, SQL Server, Oracle, SQLite).
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime


class DatabaseConnection(BaseModel):
    """Configurazione completa di una connessione a database."""
    id: str
    name: str
    description: str = ""
    type: Literal["postgres", "mssql", "mysql", "oracle", "sqlite"]
    host: str
    port: int
    database: str
    db_schema: Optional[str] = None
    username: str
    password: str
    group_id: str = ""
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    can_edit: Optional[bool] = None  # calcolato per l'utente corrente


class ConnectionCreate(BaseModel):
    """Dati richiesti per creare una nuova connessione database."""
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field("", max_length=2000)
    type: Literal["postgres", "mssql", "mysql", "oracle", "sqlite"]
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    database: str = Field(..., min_length=1, max_length=255)
    db_schema: Optional[str] = Field(None, max_length=255)
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., max_length=1000)


class ConnectionUpdate(BaseModel):
    """Dati parziali per aggiornare una connessione esistente."""
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    type: Optional[Literal["postgres", "mssql", "mysql", "oracle", "sqlite"]] = None
    host: Optional[str] = Field(None, max_length=255)
    port: Optional[int] = Field(None, ge=1, le=65535)
    database: Optional[str] = Field(None, max_length=255)
    db_schema: Optional[str] = Field(None, max_length=255)
    username: Optional[str] = Field(None, max_length=255)
    password: Optional[str] = Field(None, max_length=1000)


class ConnectionList(BaseModel):
    """Wrapper per la lista di connessioni database."""
    connections: List[DatabaseConnection]


class ConnectionTestResult(BaseModel):
    """Risultato del test di connessione a un database."""
    success: bool
    message: str
    details: Optional[dict] = None
