"""
URBY-ETL — Data Source Backend: Cartella Locale / Rete
Supporta path locali (C:\\dati\\) e UNC (\\\\server\\share\\).
"""

import os
from datetime import datetime
from typing import Optional

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceInterface,
    DataSourceConnectionError,
    DataSourceFileNotFoundError,
    DataSourcePermissionError,
    FileInfo,
)


class LocalDataSource(DataSourceInterface):
    """
    Backend per cartelle locali e cartelle di rete montate (UNC path).

    Non richiede connessione vera: connect() verifica semplicemente
    che la root_path esista e sia leggibile.
    """

    def __init__(self, config: BackendConfig):
        super().__init__(config)
        self._connected = False

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def connect(self) -> None:
        root = self.config.root_path
        if not os.path.exists(root):
            raise DataSourceConnectionError(f"Cartella non trovata: {root}")
        if not os.access(root, os.R_OK):
            raise DataSourceConnectionError(f"Permessi di lettura insufficienti: {root}")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def test_connection(self) -> dict:
        try:
            self.connect()
            root = self.config.root_path
            try:
                entries = os.listdir(root)
                file_count = sum(1 for e in entries if os.path.isfile(os.path.join(root, e)))
            except PermissionError:
                file_count = None
            return {
                "ok": True,
                "message": "Cartella accessibile",
                "details": {"path": root, "files_in_root": file_count},
            }
        except DataSourceConnectionError as e:
            return {"ok": False, "message": str(e), "details": {}}
        finally:
            self.disconnect()

    # ── Le 6 operazioni ────────────────────────────────────────────────────

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
                # Ricorsivo: os.walk scende in tutte le sottocartelle
                for dirpath, dirnames, filenames in os.walk(full_path):
                    # Salta cartelle nascoste
                    dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                    for filename in filenames:
                        if not self._matches_pattern(filename, pattern):
                            continue
                        entry_path = os.path.join(dirpath, filename)
                        try:
                            st = os.stat(entry_path)
                        except OSError:
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
                # Non ricorsivo: mostra file E sottocartelle del livello corrente
                with os.scandir(full_path) as it:
                    for entry in it:
                        if entry.name.startswith('.'):
                            continue
                        if entry.is_dir(follow_symlinks=True):
                            # Aggiungi cartella
                            try:
                                st = entry.stat(follow_symlinks=True)
                            except OSError:
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
                            except OSError:
                                continue
                            hash_val = None
                            if include_hash:
                                try:
                                    hash_val = self._compute_hash(self._raw_read(entry.path))
                                except OSError:
                                    pass
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

        # Cartelle prima, poi file per data decrescente
        results.sort(key=lambda f: (0 if f.type == 'folder' else 1, f.modified_date), reverse=False)
        results.sort(key=lambda f: f.modified_date if f.type != 'folder' else datetime.max, reverse=True)
        # Riordina: cartelle in cima, file per data decrescente
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

    def write_file(self, path: str, content: bytes) -> None:
        full_path = self._abs(path)
        parent = os.path.dirname(full_path)
        try:
            os.makedirs(parent, exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(content)
        except PermissionError as e:
            raise DataSourcePermissionError(f"Scrittura negata: {full_path}") from e
        except OSError as e:
            raise DataSourceConnectionError(f"Errore scrittura: {e}") from e

    def delete_file(self, path: str) -> None:
        full_path = self._abs(path)
        if not os.path.exists(full_path):
            raise DataSourceFileNotFoundError(f"File non trovato: {path!r}")
        try:
            os.remove(full_path)
        except PermissionError as e:
            raise DataSourcePermissionError(f"Cancellazione negata: {full_path}") from e

    def file_exists(self, path: str) -> bool:
        return os.path.isfile(self._abs(path))

    # ── Helpers privati ─────────────────────────────────────────────────────

    def _abs(self, relative: str) -> str:
        """Path assoluto = root + relativo."""
        root = self.config.root_path.rstrip("/\\")
        rel = relative.lstrip("/\\")
        return os.path.join(root, rel) if rel else root

    def _rel(self, absolute: str) -> str:
        """Path relativo alla root, separatori normalizzati a '/'."""
        rel = os.path.relpath(absolute, self.config.root_path)
        return rel.replace(os.sep, "/")

    def _raw_read(self, full_path: str) -> bytes:
        with open(full_path, "rb") as f:
            return f.read()