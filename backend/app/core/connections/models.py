"""
Modelli Pydantic per il modulo Connections.

CRUD connessioni database (PostgreSQL, MySQL, SQL Server, Oracle, SQLite).
"""

from pydantic import BaseModel
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


class ConnectionCreate(BaseModel):
    """Dati richiesti per creare una nuova connessione database."""
    name: str
    description: str = ""
    type: Literal["postgres", "mssql", "mysql", "oracle", "sqlite"]
    host: str
    port: int
    database: str
    db_schema: Optional[str] = None
    username: str
    password: str


class ConnectionUpdate(BaseModel):
    """Dati parziali per aggiornare una connessione esistente."""
    name: Optional[str] = None
    description: Optional[str] = None
    type: Optional[Literal["postgres", "mssql", "mysql", "oracle", "sqlite"]] = None
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    db_schema: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


class ConnectionList(BaseModel):
    """Wrapper per la lista di connessioni database."""
    connections: List[DatabaseConnection]


class ConnectionTestResult(BaseModel):
    """Risultato del test di connessione a un database."""
    success: bool
    message: str
    details: Optional[dict] = None
