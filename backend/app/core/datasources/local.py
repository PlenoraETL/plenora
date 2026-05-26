"""Local and mounted-network folder data source backend."""

from pathlib import Path as _Path

import os
from datetime import datetime
from typing import Optional

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceInterface,
    DataSourceConnectionError,
    DataSourceConnectionTestResult,
    DataSourceFileNotFoundError,
    DataSourcePermissionError,
    FileInfo,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _classify_path_for_multi_host(path: str) -> str:
    """Classifica un path come accessibile multi-host o locale.

    Ritorna una di: 'network' | 'local' | 'unknown'.

    Heuristic (non perfetta):
      - UNC (`\\\\server\\share`) su Windows -> network
      - Linux: prefix in /mnt/, /media/, /net/, /nfs/ -> network
      - Windows drive letter (`C:\\...`) -> local
      - Unix absolute path standard (/home, /srv, /var, /opt) -> local
      - Altro -> unknown

    Senza controllare il mount table reale (richiederebbe root). Serve
    per advisory, non per decisioni critiche.
    """
    if not path:
        return "unknown"

    p = path.strip()

    # UNC path (Windows) — \\server\share\
    if p.startswith(("\\\\", "//")) and len(p) > 2:
        return "network"

    # Linux network mount prefix conventions
    linux_network_prefixes = ("/mnt/", "/media/", "/net/", "/nfs/", "/efs/")
    if any(p.startswith(prefix) for prefix in linux_network_prefixes):
        return "network"

    # Windows drive letter (C:\, D:\
    if len(p) >= 2 and p[1] == ":":
        return "local"

    # Linux tipico local: /home/, /srv/, /var/, /opt/, /tmp/, /root/
    linux_local_prefixes = ("/home/", "/srv/", "/var/", "/opt/", "/tmp/", "/root/", "/etc/")
    if any(p.startswith(prefix) for prefix in linux_local_prefixes):
        return "local"

    return "unknown"


class LocalDataSource(DataSourceInterface):
    """
    Backend per cartelle locali e cartelle di rete montate (UNC path).

    Non richiede connessione vera: connect verifica semplicemente
    che la root_path esista e sia leggibile.
    """

    def __init__(self, config: BackendConfig):
        super().__init__(config)
        self._connected = False

    def connect(self) -> None:
        root = self.config.root_path
        if not os.path.exists(root):
            raise DataSourceConnectionError(f"Cartella non trovata: {root}")
        if not os.access(root, os.R_OK):
            raise DataSourceConnectionError(f"Permessi di lettura insufficienti: {root}")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def test_connection(self) -> DataSourceConnectionTestResult:
        try:
            self.connect()
            root = self.config.root_path
            try:
                entries = os.listdir(root)
                file_count = sum(1 for e in entries if os.path.isfile(os.path.join(root, e)))
            except PermissionError:
                file_count = None

            # Local paths are valid but risky when Celery workers run on other hosts.
            path_class = _classify_path_for_multi_host(root)
            advisory = None
            if path_class == "local":
                advisory = (
                    "Path locale: accessibile solo al nodo corrente. "
                    "In setup multi-host usare UNC (\\\\server\\share), "
                    "NFS/EFS mount, o un datasource SFTP/SharePoint."
                )
            elif path_class == "unknown":
                advisory = (
                    "Impossibile classificare il path: verifica che sia "
                    "accessibile anche dai worker Celery se distribuiti."
                )

            return {
                "ok": True,
                "message": "Cartella accessibile",
                "details": {
                    "path": root,
                    "files_in_root": file_count,
                    "path_class": path_class,
                    "multi_host_advisory": advisory,
                },
            }
        except DataSourceConnectionError as e:
            return {"ok": False, "message": str(e), "details": {}}
        finally:
            self.disconnect()

    def list_files(
        self,
        path: str = "",
        pattern: Optional[str] = None,
        include_hash: bool = False,
        recursive: bool = False,
    ) -> list[FileInfo]:
        full_path = self._abs(path)
        if not os.path.exists(full_path):
            raise DataSourceFileNotFoundError(f"Cartella non trovata: {path!r}")

        results = []
        try:
            if recursive:
                for dirpath, dirnames, filenames in os.walk(full_path):
                    dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                    for filename in filenames:
                        if not self._matches_pattern(filename, pattern):
                            continue
                        entry_path = os.path.join(dirpath, filename)
                        try:
                            st = os.stat(entry_path)
                        except OSError as e:
                            logger.debug(
                                "local list_files(recursive): skip %r (stat non disponibile): %s: %s",
                                entry_path, type(e).__name__, e,
                            )
                            continue
                        hash_val = None
                        if include_hash:
                            try:
                                hash_val = self._compute_hash(self._raw_read(entry_path))
                            except OSError:
                                pass
                        rel = self._rel(entry_path)
                        results.append(FileInfo(
                            name=filename,
                            path=rel,
                            size=st.st_size,
                            modified_date=datetime.fromtimestamp(st.st_mtime),
                            type=self._extension(filename),
                            hash=hash_val,
                        ))
            else:
                with os.scandir(full_path) as it:
                    for entry in it:
                        if entry.name.startswith('.'):
                            continue
                        if entry.is_dir(follow_symlinks=True):
                            try:
                                st = entry.stat(follow_symlinks=True)
                            except OSError as e:
                                logger.debug(
                                    "local list_files: skip folder %r (stat non disponibile): %s: %s",
                                    entry.path, type(e).__name__, e,
                                )
                                continue
                            rel = self._rel(entry.path)
                            results.append(FileInfo(
                                name=entry.name,
                                path=rel,
                                size=0,
                                modified_date=datetime.fromtimestamp(st.st_mtime),
                                type='folder',
                                hash=None,
                            ))
                        elif entry.is_file(follow_symlinks=True):
                            if not self._matches_pattern(entry.name, pattern):
                                continue
                            try:
                                st = entry.stat(follow_symlinks=True)
                            except OSError as e:
                                logger.debug(
                                    "local list_files: skip file %r (stat non disponibile): %s: %s",
                                    entry.path, type(e).__name__, e,
                                )
                                continue
                            hash_val = None
                            if include_hash:
                                try:
                                    hash_val = self._compute_hash(self._raw_read(entry.path))
                                except OSError as e:
                                    logger.debug(
                                        "local list_files: hash non calcolato per %r: %s: %s",
                                        entry.path, type(e).__name__, e,
                                    )
                            rel = self._rel(entry.path)
                            results.append(FileInfo(
                                name=entry.name,
                                path=rel,
                                size=st.st_size,
                                modified_date=datetime.fromtimestamp(st.st_mtime),
                                type=self._extension(entry.name),
                                hash=hash_val,
                            ))
        except PermissionError as e:
            raise DataSourcePermissionError(f"Accesso negato: {full_path}") from e

        results.sort(key=lambda f: (0 if f.type == 'folder' else 1, f.modified_date), reverse=False)
        results.sort(key=lambda f: f.modified_date if f.type != 'folder' else datetime.max, reverse=True)
        folders = [r for r in results if r.type == 'folder']
        files   = [r for r in results if r.type != 'folder']
        files.sort(key=lambda f: f.modified_date, reverse=True)
        return folders + files

    def file_info(self, path: str) -> FileInfo:
        full_path = self._abs(path)
        if not os.path.exists(full_path):
            raise DataSourceFileNotFoundError(f"File non trovato: {path!r}")
        try:
            st = os.stat(full_path)
        except PermissionError as e:
            raise DataSourcePermissionError(f"Accesso negato: {full_path}") from e

        name = os.path.basename(full_path)
        return FileInfo(
            name=name,
            path=path,
            size=st.st_size,
            modified_date=datetime.fromtimestamp(st.st_mtime),
            type=self._extension(name),
        )

    def read_file(self, path: str) -> bytes:
        full_path = self._abs(path)
        if not os.path.exists(full_path):
            raise DataSourceFileNotFoundError(f"File non trovato: {path!r}")
        try:
            return self._raw_read(full_path)
        except PermissionError as e:
            raise DataSourcePermissionError(f"Accesso negato: {full_path}") from e

    def iter_read_chunks(self, path: str, *, chunk_size: int = 64 * 1024):
        """RAM-3 (2026-05-26): override streaming nativo per filesystem
        locale. Apre il file in binary mode e itera `chunk_size` byte
        per volta — peak RAM ~`chunk_size` invece di file size.
        """
        full_path = self._abs(path)
        if not os.path.exists(full_path):
            raise DataSourceFileNotFoundError(f"File non trovato: {path!r}")
        try:
            with open(full_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except PermissionError as e:
            raise DataSourcePermissionError(f"Accesso negato: {full_path}") from e

    def write_chunks(self, path: str, chunks) -> None:
        """RAM-B1 (2026-05-26): streaming write delegato al boundary
        `io.local_filesystem.write_local_chunks` (single audit point
        per scritture filesystem locale). Peak RAM ~chunk_size.
        """
        from app.core.io.local_filesystem import (
            LocalFilesystemError,
            write_local_chunks,
        )

        full_path = self._abs(path)
        try:
            write_local_chunks(full_path, chunks)
        except LocalFilesystemError as exc:
            cause = exc.__cause__
            if isinstance(cause, PermissionError):
                raise DataSourcePermissionError(
                    f"Scrittura negata: {full_path}"
                ) from cause
            raise DataSourceConnectionError(
                f"Errore scrittura: {cause if cause is not None else exc}"
            ) from (cause or exc)

    def write_file(self, path: str, content: bytes) -> None:
        # Boundary: la scrittura locale passa per `io.local_filesystem`
        # (data plane endpoint, vedi razionale nel modulo). I PermissionError
        # / OSError sono wrappati come LocalFilesystemError; qui li
        # riconvertiamo in datasource-specific per preservare l'API contract.
        from app.core.io.local_filesystem import (
            LocalFilesystemError,
            write_local_bytes,
        )

        full_path = self._abs(path)
        try:
            write_local_bytes(full_path, content)
        except LocalFilesystemError as exc:
            cause = exc.__cause__
            if isinstance(cause, PermissionError):
                raise DataSourcePermissionError(
                    f"Scrittura negata: {full_path}"
                ) from cause
            raise DataSourceConnectionError(
                f"Errore scrittura: {cause if cause is not None else exc}"
            ) from (cause or exc)

    def delete_file(self, path: str) -> None:
        # Boundary: data plane delete via `io.local_cleanup.delete_local_file`
        # (strict mode, solleva PermissionError / FileNotFoundError).
        from app.core.io.local_cleanup import delete_local_file

        full_path = self._abs(path)
        if not os.path.exists(full_path):
            raise DataSourceFileNotFoundError(f"File non trovato: {path!r}")
        try:
            delete_local_file(full_path)
        except PermissionError as e:
            raise DataSourcePermissionError(f"Cancellazione negata: {full_path}") from e

    def file_exists(self, path: str) -> bool:
        return os.path.isfile(self._abs(path))

    def _abs(self, relative: str) -> str:
        """Path assoluto = root + relativo, validato contro path traversal.

        Regole specifiche di local datasource:
          - rifiuta path assoluti (Unix, Windows drive letter, UNC)
          - verifica che il risultato sia contenuto dentro root_path

        Il check di containment (resolve + relative_to) e' delegato a
        `app.shared.resources.path_policy.assert_path_within` (R#4 cleanup).
        I check su path assoluti / drive letter restano inline perche'
        sono regole specifiche di questa datasource (le altre whitelist
        accettano path assoluti dentro RESULTS_DIR/TEMP_DIR/etc).

        Raises DataSourcePermissionError se l'input e' malformato o tenta
        di uscire dalla root configurata.
        """
        from app.shared.resources.path_policy import assert_path_within

        root = self.config.root_path.rstrip("/\\")
        root_abs = _Path(root).resolve()

        if not relative:
            return str(root_abs)

        # Blocca path assoluti Unix, Windows e UNC (il helper centralizzato
        # rifiuta solo UNC: gli altri check restano qui perche' specifici
        # di local datasource
        if relative.startswith(('/', '\\')):
            raise DataSourcePermissionError(
                f"Path assoluto non consentito: {relative!r}"
            )
        if len(relative) >= 2 and relative[1] == ':':
            raise DataSourcePermissionError(
                f"Drive letter non consentita: {relative!r}"
            )

        # Containment check: compone candidato e delega a
        # assert_path_within per resolve + relative_to. Traduciamo
        # ValueError in DataSourcePermissionError per uniformare il
        # tipo di eccezione che esce da questa datasource
        try:
            resolved = assert_path_within(root_abs / relative, [root_abs])
        except ValueError as e:
            raise DataSourcePermissionError(
                f"Path traversal rifiutato: {relative!r} esce da {root_abs}"
            ) from e
        return str(resolved)

    def _rel(self, absolute: str) -> str:
        """Path relativo alla root, separatori normalizzati a '/'."""
        rel = os.path.relpath(absolute, self.config.root_path)
        return rel.replace(os.sep, "/")

    _MAX_READ_SIZE = 500 * 1024 * 1024  # 500 MB

    def _raw_read(self, full_path: str) -> bytes:
        size = os.path.getsize(full_path)
        if size > self._MAX_READ_SIZE:
            raise ValueError(
                f"File troppo grande ({size // (1024*1024)}MB). "
                f"Limite: {self._MAX_READ_SIZE // (1024*1024)}MB"
            )
        with open(full_path, "rb") as f:
            return f.read()
