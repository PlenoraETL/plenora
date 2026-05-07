"""
PLENORA-ETL — Data Source Backend: Cartella Locale / Rete

Supporta path locali e UNC evitando assunzioni sul filesystem del worker.
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

            # Multi-host advisory: avvisa se il path sembra local-only
            # Non blocca — solo informativo. Utile in setup con Celery
            # worker distribuiti: su un nodo diverso questo path NON sara'
            # raggiungibile e task che usano questo datasource falliranno
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
                        # intent: optional feature / outcome: fail-open (entry skip su stat inaccessibile
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
                # Non ricorsivo: mostra file E sottocartelle del livello corrente
                with os.scandir(full_path) as it:
                    for entry in it:
                        if entry.name.startswith('.'):
                            continue
                        if entry.is_dir(follow_symlinks=True):
                            # Aggiungi cartella
                            # intent: optional feature / outcome: fail-open (folder skip su stat inaccessibile
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
                            # intent: optional feature / outcome: fail-open (file skip su stat inaccessibile
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
                                # intent: optional feature / outcome: fail-open (hash opzionale
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
        """Path assoluto = root + relativo, validato contro path traversal.

        Regole specifiche di local datasource:
          - rifiuta path assoluti (Unix, Windows drive letter, UNC)
          - verifica che il risultato sia contenuto dentro root_path

        Il check di containment (resolve + relative_to) e' delegato a
        `app.shared.path_policy.assert_path_within` (R#4 cleanup).
        I check su path assoluti / drive letter restano inline perche'
        sono regole specifiche di questa datasource (le altre whitelist
        accettano path assoluti dentro RESULTS_DIR/TEMP_DIR/etc).

        Raises DataSourcePermissionError se l'input e' malformato o tenta
        di uscire dalla root configurata.
        """
        from pathlib import Path as _Path
        from app.shared.path_policy import assert_path_within

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
