"""
PLENORA-ETL — Data Source Interface

Classe base astratta comune a tutti i backend di storage.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Iterator, Mapping, Optional
from typing_extensions import TypedDict
import fnmatch
import hashlib


DEFAULT_CHUNK_SIZE = 64 * 1024  # 64KB — equilibrio tra syscall overhead e RAM peak


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


def validate_relative_path(path: str, *, allow_empty: bool = False) -> str:
    """Validate and normalize an untrusted datasource-relative path."""
    if not isinstance(path, str):
        raise DataSourcePermissionError("Path Data Source non valido")
    if "\x00" in path:
        raise DataSourcePermissionError("Path Data Source contiene caratteri non validi")
    if not path:
        if allow_empty:
            return ""
        raise DataSourcePermissionError("Path Data Source vuoto")
    if path.startswith(("/", "\\")) or (len(path) >= 2 and path[1] == ":"):
        raise DataSourcePermissionError(f"Path assoluto non consentito: {path!r}")
    if "\\" in path:
        raise DataSourcePermissionError(f"Separatore non consentito nel path: {path!r}")

    segments = [segment for segment in path.split("/") if segment]
    if any(segment in (".", "..") for segment in segments):
        raise DataSourcePermissionError(f"Path traversal rifiutato: {path!r}")
    if not segments:
        if allow_empty:
            return ""
        raise DataSourcePermissionError("Path Data Source vuoto")
    return "/".join(segments)


class FileInfoPayload(TypedDict):
    name: str
    path: str
    size: int
    modified_date: str
    type: str
    hash: str | None


class DataSourceConnectionTestResult(TypedDict):
    ok: bool
    message: str
    details: Mapping[str, object]


@dataclass
class FileInfo:
    """Metadata di un singolo file nello storage."""
    name: str
    path: str
    size: int                       # bytes
    modified_date: datetime
    type: str                       # estensione senza punto, lowercase (es. "csv"
    hash: Optional[str] = None      # md5, solo se include_hash=True

    def to_dict(self) -> FileInfoPayload:
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


    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _ = (exc_val, exc_tb)
        self.disconnect()
        return False


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
    def test_connection(self) -> DataSourceConnectionTestResult:
        """
        Testa la connessione e ritorna un report strutturato.

        Returns:
            {"ok": True, "message": "...", "details": {...}}
            {"ok": False, "message": "...", "details": {...}}
        """


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

    def iter_read_chunks(
        self,
        path: str,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> Iterator[bytes]:
        """Legge il file a chunk per minimizzare il RAM peak.

        Default implementation: fallback a `read_file()` + suddivisione
        del bytes intero (NON riduce il peak, ma fornisce l'interface
        uniforme per `StreamingResponse` nel router).

        I backend con vero streaming nativo (LocalDataSource: file handle
        chunked read; SftpDataSource futuro: paramiko prefetch) overridano
        questo metodo per davvero fare streaming senza buffering completo.

        Args:
            path: file da leggere (relativo alla root).
            chunk_size: dimensione del singolo chunk (default 64KB).

        Yields:
            Sequenza di `bytes` con `len(chunk) <= chunk_size`. L'ultimo
            chunk puo' essere piu' piccolo o vuoto.

        Raises:
            DataSourceFileNotFoundError, DataSourcePermissionError —
            come `read_file()`.
        """
        # Default safe: legge tutto, poi suddivide. Override per
        # streaming vero.
        data = self.read_file(path)
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    @abstractmethod
    def write_file(self, path: str, content: bytes) -> None:
        """
        Scrive bytes in un file (crea o sovrascrive).
        Crea le directory intermedie se necessario.

        Raises: DataSourcePermissionError, DataSourceError
        """

    def write_chunks(self, path: str, chunks: Iterable[bytes]) -> None:
        """Scrive un file consumando un iterable di bytes chunk.

        Default implementation: accumula tutti i chunk in `bytearray` e
        delega a `write_file()`. Backwards-compatible: i backend che
        non override-ano non perdono peak ma espongono l'interface
        uniforme.

        I backend con vero streaming nativo override-ano questo metodo:
          - LocalDataSource: delega a io.local_filesystem chunked write — peak O(chunk_size)
          - SftpDataSource: paramiko SFTPClient file-like in write mode — peak O(chunk_size)
          - FtpDataSource: ftplib storbinary con callback file-like adapter — peak O(chunk_size)
          - SharePointDataSource: default fallback (REST API tipicamente
            richiede content-length, no chunked upload nativo)

        Args:
            path: file di destinazione (relativo alla root).
            chunks: iterable di `bytes`. Puo' essere una list, un
                generator, o un iterator. Consumato una sola volta.

        Raises:
            DataSourcePermissionError, DataSourceError — come `write_file()`.
        """
        # Default safe: accumula. Override per streaming vero.
        buf = bytearray()
        for chunk in chunks:
            buf.extend(chunk)
        self.write_file(path, bytes(buf))

    @abstractmethod
    def delete_file(self, path: str) -> None:
        """
        Cancella un file.

        Raises: DataSourceFileNotFoundError, DataSourcePermissionError
        """

    @abstractmethod
    def file_exists(self, path: str) -> bool:
        """
        Verifica se un file esiste. Ritorna False soltanto per un not-found
        confermato; errori di rete o permessi restano eccezioni tipate.
        """


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

    def _validated_path(self, path: str, *, allow_empty: bool = False) -> str:
        return validate_relative_path(path, allow_empty=allow_empty)
