"""
Modelli Pydantic per il modulo Data Source.

Coprono: configurazioni CRUD, risultati operazioni, scan_state per scheduler.
"""

from pydantic import BaseModel
from typing import List, Optional, Literal
from datetime import datetime


class DataSourceCreate(BaseModel):
    """Dati per creare un nuovo Data Source."""
    name: str
    description: str = ""
    type: Literal["local", "ftp", "sftp", "sharepoint"]

    # Campi comuni
    root_path: str = ""

    # Campi FTP / SFTP
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None

    # SFTP: chiave SSH
    private_key: Optional[str] = None          # Contenuto PEM
    private_key_passphrase: Optional[str] = None
    host_key_policy: Optional[Literal["auto_add", "reject"]] = "reject"

    # FTP: opzioni trasporto
    tls: Optional[bool] = False
    passive: Optional[bool] = True

    # SharePoint: OAuth2 Client Credentials
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    site_id: Optional[str] = None
    site_url: Optional[str] = None
    drive_id: Optional[str] = None


class DataSourceUpdate(BaseModel):
    """Dati parziali per aggiornare un Data Source esistente."""
    name: Optional[str] = None
    description: Optional[str] = None
    root_path: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    private_key: Optional[str] = None
    private_key_passphrase: Optional[str] = None
    host_key_policy: Optional[Literal["auto_add", "reject"]] = None
    tls: Optional[bool] = None
    passive: Optional[bool] = None
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    site_id: Optional[str] = None
    site_url: Optional[str] = None
    drive_id: Optional[str] = None


class DataSource(BaseModel):
    """Rappresentazione completa di un Data Source (risposta API)."""
    id: str
    name: str
    description: str = ""
    type: Literal["local", "ftp", "sftp", "sharepoint"]
    root_path: str = ""

    # Campi specifici — presenti o None secondo il tipo
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None          # oscurato '***' nelle risposte
    private_key: Optional[str] = None       # oscurato '***' nelle risposte
    private_key_passphrase: Optional[str] = None
    host_key_policy: Optional[str] = None
    tls: Optional[bool] = None
    passive: Optional[bool] = None
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None     # oscurato '***' nelle risposte
    site_id: Optional[str] = None
    site_url: Optional[str] = None
    drive_id: Optional[str] = None

    group_id: str = ""
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    can_edit: Optional[bool] = None


class DataSourceList(BaseModel):
    """Wrapper lista Data Source."""
    datasources: List[DataSource]


class DataSourceTestResult(BaseModel):
    """Risultato del test di connessione."""
    success: bool
    message: str
    details: Optional[dict] = None


class FileInfoResponse(BaseModel):
    """Metadata di un file nello storage — risposta API."""
    name: str
    path: str
    size: int
    modified_date: datetime
    type: str
    hash: Optional[str] = None


class ListFilesRequest(BaseModel):
    """Richiesta list_files."""
    path: str = ""
    pattern: Optional[str] = None
    include_hash: bool = False


class WriteFileRequest(BaseModel):
    """Non usata per upload multipart — placeholder per documentazione."""
    path: str


class ProcessedEntry(BaseModel):
    """Traccia un file già processato dallo scheduler."""
    name: str
    processed_at: datetime
    pipeline_id: str


class SnapshotEntry(BaseModel):
    """Snapshot di un file per rilevamento modifiche."""
    name: str
    size: int
    modified: Optional[str] = None   # ISO string


class ScanState(BaseModel):
    """
    Stato runtime per rilevamento file nuovi da parte dello scheduler.
    Salvato in PostgreSQL su datasources.scan_state.
    """
    last_check: Optional[datetime] = None
    strategy: Literal["modified_date", "inventory", "pattern"] = "modified_date"
    snapshot: List[SnapshotEntry] = []
    processed: List[ProcessedEntry] = []
