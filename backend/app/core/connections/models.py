"""
Modelli Pydantic per il modulo Connections.

CRUD connessioni database (PostgreSQL, MySQL, SQL Server, Oracle, SQLite).
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional, Literal
from datetime import datetime

from app.shared.connections.mssql_tls import MssqlTlsMode


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
    mssql_tls_mode: MssqlTlsMode = "verify"
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
    mssql_tls_mode: MssqlTlsMode = "verify"


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
    mssql_tls_mode: Optional[MssqlTlsMode] = None


class ConnectionList(BaseModel):
    """Wrapper per la lista di connessioni database."""
    connections: List[DatabaseConnection]


class ConnectionTestResult(BaseModel):
    """Risultato del test di connessione a un database."""
    success: bool
    message: str
    details: Optional[dict] = None


class ConnectionSetMemberInput(BaseModel):
    """Associazione pubblica selector -> connessione salvata."""

    model_config = ConfigDict(extra="forbid")

    selector_key: str = Field(
        ...,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    label: str = Field(..., min_length=1, max_length=255)
    connection_id: str = Field(..., min_length=1, max_length=200)
    active: bool = True
    sort_order: int = Field(0, ge=0, le=1_000_000)


class ConnectionSetMember(ConnectionSetMemberInput):
    """Membro restituito senza credenziali della connessione."""

    connection_name: str = ""
    connection_type: str = ""


class ConnectionSetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field("", max_length=2000)
    selector_label: str = Field("Cliente", min_length=1, max_length=120)
    members: List[ConnectionSetMemberInput] = Field(default_factory=list)


class ConnectionSetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    selector_label: Optional[str] = Field(None, min_length=1, max_length=120)
    members: Optional[List[ConnectionSetMemberInput]] = None


class ConnectionSet(BaseModel):
    id: str
    group_id: str
    name: str
    description: str = ""
    selector_label: str = "Cliente"
    members: List[ConnectionSetMember] = Field(default_factory=list)
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    can_edit: Optional[bool] = None


class ConnectionSetList(BaseModel):
    connection_sets: List[ConnectionSet]


class ConnectionSetOption(BaseModel):
    """Opzione runtime: non espone connection_id né dettagli sensibili."""

    key: str
    label: str
