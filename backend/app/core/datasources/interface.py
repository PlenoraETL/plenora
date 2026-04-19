"""
PLENORA-ETL — Data Source Interface
Classe base astratta comune a tutti i backend di storage.

Il Data Source è un componente "stupido": solo trasporto bytes raw.
Nessuna logica di parsing o business — quella appartiene a MANIPOLA.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import fnmatch
import hashlib


# ==================== ECCEZIONI ====================

class DataSourceError(Exception):
    """Eccezione base per errori Data Source."""
    pass

class DataSourceConnectionError(DataSourceError):
    """Errore di connessione allo storage."""
    pass

class DataSourceFileNotFoundError(DataSourceError):
    """File o directory non trovata nello storage."""
    pass

class DataSourcePermissionError(DataSourceError):
    """Permessi insufficienti."""
    pass


# ==================== DATA CLASSES ====================

@dataclass
class FileInfo:
    """Metadata di un singolo file nello storage."""
    name: str
    path: str
    size: int                       # bytes
    modified_date: datetime
    type: str                       # estensione senza punto, lowercase (es. "csv")
    hash: Optional[str] = None      # md5, solo se include_hash=True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "modified_date": self.modified_date.isoformat(),
            "type": self.type,
            "hash": self.hash,
        }


@dataclass
class BackendConfig:
    """
    Configurazione passata al backend dal DataSourceService.
    Estratta dal JSON su filesystem, senza logica Pydantic.
    """
    id: str
    type: str            # "local" | "ftp" | "sftp" | "sharepoint"
    root_path: str
    extra: dict = field(default_factory=dict)   # tutti gli altri campi specifici


# ==================== INTERFACCIA ASTRATTA ====================

class DataSourceInterface(ABC):
    """
    Interfaccia astratta per tutti i backend Data Source.

    Espone 6 operazioni uniformi + context manager per la connessione.
    Le implementazioni concrete si occupano SOLO di trasporto (bytes raw).

    Uso tipico:
        with LocalDataSource(config) as ds:
            files = ds.list_files(pattern="export_*.csv")
            content = ds.read_file(files[0].path)
    """

    def __init__(self, config: BackendConfig):
        self.config = config

    # --- Context manager ---------------------------------------------------

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    # --- Lifecycle ---------------------------------------------------------

    @abstractmethod
    def connect(self) -> None:
        """
        Apre la connessione allo storage.
        Raises: DataSourceConnectionError
        """

    @abstractmethod
    def disconnect(self) -> None:
        """Chiude la connessione. Deve essere idempotente (no exception se già chiusa)."""

    @abstractmethod
    def test_connection(self) -> dict:
        """
        Testa la connessione e ritorna un report strutturato.

        Returns:
            {"ok": True,  "message": "...", "details": {...}}
            {"ok": False, "message": "...", "details": {...}}
        """

    # --- Le 6 operazioni ---------------------------------------------------

    @abstractmethod
    def list_files(
        self,
        path: str = "",
        pattern: Optional[str] = None,
        include_hash: bool = False,
        recursive: bool = False,
    ) -> list[FileInfo]:
        """
        Lista i file in una sotto-cartella dello storage.

        Args:
            path: Percorso relativo alla root configurata. "" = root.
            pattern: Glob pattern (es. "export_*.csv"). None = tutti i file.
            include_hash: Se True calcola md5 per ogni file (più lento su rete).

        Returns:
            Lista FileInfo ordinata per modified_date DESC (più recente prima).

        Raises:
            DataSourceConnectionError, DataSourceFileNotFoundError
        """

    @abstractmethod
    def file_info(self, path: str) -> FileInfo:
        """
        Metadata di un singolo file senza scansionare la cartella.

        Args:
            path: Percorso relativo alla root.

        Raises:
            DataSourceFileNotFoundError
        """

    @abstractmethod
    def read_file(self, path: str) -> bytes:
        """
        Legge il contenuto raw di un file.

        Returns: Contenuto come bytes.
        Raises: DataSourceFileNotFoundError, DataSourcePermissionError
        """

    @abstractmethod
    def write_file(self, path: str, content: bytes) -> None:
        """
        Scrive bytes in un file (crea o sovrascrive).
        Crea le directory intermedie se necessario.

        Raises: DataSourcePermissionError, DataSourceError
        """

    @abstractmethod
    def delete_file(self, path: str) -> None:
        """
        Cancella un file.

        Raises: DataSourceFileNotFoundError, DataSourcePermissionError
        """

    @abstractmethod
    def file_exists(self, path: str) -> bool:
        """
        Verifica se un file esiste. Non solleva eccezioni (ritorna False se non trovato).
        """

    # --- Helpers condivisi -------------------------------------------------

    def _extension(self, filename: str) -> str:
        """Estensione senza punto, lowercase. '' se assente."""
        if "." not in filename:
            return ""
        return filename.rsplit(".", 1)[-1].lower()

    def _matches_pattern(self, filename: str, pattern: Optional[str]) -> bool:
        """Verifica glob pattern. Se pattern è None, ritorna sempre True."""
        if pattern is None:
            return True
        return fnmatch.fnmatch(filename, pattern)

    def _compute_hash(self, content: bytes) -> str:
        """md5 del contenuto, hex string."""
        return hashlib.md5(content).hexdigest()

    def _join_path(self, *parts: str) -> str:
        """Unisce parti di path usando '/', rimuovendo slash ridondanti."""
        segments = []
        for p in parts:
            segments.extend(s for s in p.strip("/").split("/") if s)
        return "/".join(segments)