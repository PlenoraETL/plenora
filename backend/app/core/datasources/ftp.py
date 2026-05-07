"""
PLENORA-ETL — Data Source Backend: FTP

Supporta FTP attivo/passivo con TLS opzionale (FTPS).
"""

import ftplib
import io
import socket
from datetime import datetime
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


def _sanitize_ftp_error(e: Exception) -> str:
    """F5-B2 (2026-04-24): mappa eccezioni ftplib/socket a messaggi canonici
    user-facing, SENZA esporre str(e) che puo' contenere banner, response
    code dettagli, hostname nel raw string. Il dettaglio tecnico resta
    nei log server-side (logger.warning/error con exc_info=True).
    """
    if isinstance(e, ftplib.error_perm):
        return "Accesso FTP negato o risorsa non disponibile"
    if isinstance(e, ftplib.error_temp):
        return "Errore temporaneo del server FTP, riprova piu' tardi"
    if isinstance(e, socket.timeout):
        return "Timeout connessione FTP"
    if isinstance(e, socket.gaierror):
        return "Host FTP non risolvibile"
    if isinstance(e, ConnectionRefusedError):
        return "Connessione FTP rifiutata"
    if isinstance(e, OSError):
        return "Connessione FTP rifiutata"
    return "Connessione FTP fallita"

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceInterface,
    DataSourceConnectionError,
    DataSourceFileNotFoundError,
    DataSourcePermissionError,
    FileInfo,
)


class FTPDataSource(DataSourceInterface):

    def __init__(self, config: BackendConfig):
        super().__init__(config)
        self._ftp: Optional[ftplib.FTP] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def connect(self) -> None:
        extra = self.config.extra
        host = extra.get("host", "")
        port = int(extra.get("port", 21))
        username = extra.get("username", "")
        password = extra.get("password", "")
        tls = extra.get("tls", False)
        passive = extra.get("passive", True)

        if not host:
            raise DataSourceConnectionError("Host FTP non configurato")

        try:
            if tls:
                ftp = ftplib.FTP_TLS()
                ftp.connect(host=host, port=port, timeout=15)
                ftp.login(user=username, passwd=password)
                ftp.prot_p()    # canale dati cifrato
            else:
                ftp = ftplib.FTP()
                ftp.connect(host=host, port=port, timeout=15)
                ftp.login(user=username, passwd=password)

            ftp.set_pasv(passive)
            self._ftp = ftp

            # Posiziona nella root configurata
            root = self.config.root_path
            if root and root != "/":
                try:
                    self._ftp.cwd(root)
                except ftplib.error_perm as e:
                    logger.warning(
                        f"[GESTITO] FTP cwd root fallito: root={root!r} "
                        f"{type(e).__name__}: {e}",
                        exc_info=True,
                    )
                    raise DataSourceConnectionError(
                        f"Root path FTP non accessibile: {root!r}"
                    ) from e
        except ftplib.all_errors as e:
            logger.warning(
                f"[GESTITO] FTP connect fallita: {type(e).__name__}: {e}",
                exc_info=True,
            )
            # Chiudi connessione se è stata aperta ma l'inizializzazione è fallita
            if self._ftp:
                try:
                    self._ftp.quit()
                except Exception:
                    pass
                self._ftp = None
            raise DataSourceConnectionError(_sanitize_ftp_error(e)) from e

    def disconnect(self) -> None:
        if self._ftp:
            try:
                self._ftp.quit()
            except Exception as e:
                logger.debug("FTP quit fallito, tento close: %s", e)
                try:
                    self._ftp.close()
                except Exception as e2:
                    logger.warning("FTP close fallito: %s", e2)
            self._ftp = None

    def test_connection(self) -> dict:
        try:
            self.connect()
            files = self._ftp.nlst()
            return {
                "ok": True,
                "message": "Connessione FTP riuscita",
                "details": {
                    "host": self.config.extra.get("host"),
                    "tls": self.config.extra.get("tls", False),
                    "passive": self.config.extra.get("passive", True),
                    "root_entries": len(files),
                },
            }
        except DataSourceConnectionError as e:
            # F5-B2: `e` qui e' il nostro wrapper con messaggio gia' canonico
            # (vedi connect() + _sanitize_ftp_error), quindi str(e) e' safe.
            # Log dettaglio tecnico dell'eventuale original cause.
            logger.warning(
                f"[GESTITO] FTP test_connection fallita: {type(e).__name__}: {e} "
                f"(cause: {type(e.__cause__).__name__ if e.__cause__ else 'none'})",
                exc_info=True,
            )
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
        self._check()
        remote = path or "."
        results = []

        try:
            # MLSD (RFC 3659) — metadata strutturati se supportato
            entries = list(self._ftp.mlsd(remote, facts=["size", "modify", "type"]))
            for name, facts in entries:
                if facts.get("type") != "file":
                    continue
                if not self._matches_pattern(name, pattern):
                    continue
                file_path = self._join_path(path, name)
                hash_val = self._maybe_hash(file_path, include_hash)
                results.append(FileInfo(
                    name=name,
                    path=file_path,
                    size=int(facts.get("size", 0)),
                    modified_date=self._parse_mlsd_time(facts.get("modify", "")),
                    type=self._extension(name),
                    hash=hash_val,
                ))
        except ftplib.error_perm:
            # Server senza MLSD — fallback a NLST + SIZE + MDTM
            results = self._list_nlst(path, pattern, include_hash)
        except ftplib.all_errors as e:
            logger.warning(
                f"[GESTITO] FTP list_files fallita: {type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(f"Errore list_files FTP: {_sanitize_ftp_error(e)}") from e

        results.sort(key=lambda f: f.modified_date, reverse=True)
        return results

    def _list_nlst(self, path: str, pattern: Optional[str], include_hash: bool) -> list[FileInfo]:
        remote = path or "."
        try:
            names = self._ftp.nlst(remote)
        except ftplib.error_perm as e:
            raise DataSourceFileNotFoundError(f"Path non trovato: {path!r}") from e

        results = []
        for full_name in names:
            name = full_name.split("/")[-1]
            if not self._matches_pattern(name, pattern):
                continue
            # intent: optional feature / outcome: fail-open (entry skip su NLST metadata)
            try:
                size = self._ftp.size(full_name) or 0
                mdtm = self._ftp.sendcmd(f"MDTM {full_name}")
                modified_date = self._parse_mdtm(mdtm)
            except ftplib.all_errors as e:
                logger.debug(
                    "ftp list_nlst: skip entry %r (metadata unavailable): %s: %s",
                    full_name, type(e).__name__, e,
                )
                continue
            file_path = self._join_path(path, name)
            hash_val = self._maybe_hash(file_path, include_hash)
            results.append(FileInfo(
                name=name,
                path=file_path,
                size=size,
                modified_date=modified_date,
                type=self._extension(name),
                hash=hash_val,
            ))
        return results

    def file_info(self, path: str) -> FileInfo:
        self._check()
        name = path.split("/")[-1]
        try:
            size = self._ftp.size(path)
            if size is None:
                raise DataSourceFileNotFoundError(f"File non trovato: {path!r}")
            mdtm = self._ftp.sendcmd(f"MDTM {path}")
            modified_date = self._parse_mdtm(mdtm)
        except ftplib.error_perm as e:
            raise DataSourceFileNotFoundError(f"File non trovato: {path!r}") from e
        except ftplib.all_errors as e:
            logger.warning(
                f"[GESTITO] FTP file_info fallita: path={path!r} "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(f"Errore file_info FTP: {_sanitize_ftp_error(e)}") from e

        return FileInfo(
            name=name,
            path=path,
            size=size,
            modified_date=modified_date,
            type=self._extension(name),
        )

    def read_file(self, path: str) -> bytes:
        self._check()
        buf = io.BytesIO()
        try:
            self._ftp.retrbinary(f"RETR {path}", buf.write)
        except ftplib.error_perm as e:
            if "550" in str(e):
                raise DataSourceFileNotFoundError(f"File non trovato: {path!r}") from e
            raise DataSourcePermissionError(f"Accesso negato: {path!r}") from e
        except ftplib.all_errors as e:
            logger.warning(
                f"[GESTITO] FTP read_file fallita: path={path!r} "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(f"Errore read_file FTP: {_sanitize_ftp_error(e)}") from e
        return buf.getvalue()

    def write_file(self, path: str, content: bytes) -> None:
        self._check()
        parent = "/".join(path.split("/")[:-1])
        if parent:
            self._mkdirs(parent)
        buf = io.BytesIO(content)
        try:
            self._ftp.storbinary(f"STOR {path}", buf)
        except ftplib.error_perm as e:
            raise DataSourcePermissionError(f"Scrittura negata: {path!r}") from e
        except ftplib.all_errors as e:
            logger.warning(
                f"[GESTITO] FTP write_file fallita: path={path!r} "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(f"Errore write_file FTP: {_sanitize_ftp_error(e)}") from e

    def delete_file(self, path: str) -> None:
        self._check()
        try:
            self._ftp.delete(path)
        except ftplib.error_perm as e:
            if "550" in str(e):
                raise DataSourceFileNotFoundError(f"File non trovato: {path!r}") from e
            raise DataSourcePermissionError(f"Cancellazione negata: {path!r}") from e
        except ftplib.all_errors as e:
            logger.warning(
                f"[GESTITO] FTP delete_file fallita: path={path!r} "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(f"Errore delete_file FTP: {_sanitize_ftp_error(e)}") from e

    def file_exists(self, path: str) -> bool:
        self._check()
        try:
            return self._ftp.size(path) is not None
        except ftplib.all_errors:
            return False

    # ── Helpers privati ─────────────────────────────────────────────────────

    def _check(self):
        if self._ftp is None:
            raise DataSourceConnectionError("Non connesso. Usa il context manager 'with'.")

    def _maybe_hash(self, path: str, include_hash: bool) -> Optional[str]:
        if not include_hash:
            return None
        # intent: optional feature / outcome: fail-open (hash opzionale)
        try:
            return self._compute_hash(self.read_file(path))
        except Exception as e:
            logger.debug(
                "ftp _maybe_hash: hash non calcolato per %r: %s: %s",
                path, type(e).__name__, e,
            )
            return None

    def _mkdirs(self, remote_path: str) -> None:
        """Crea ricorsivamente directory remote (ignora errori se già esistono)."""
        parts = remote_path.lstrip("/").split("/")
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            # intent: optional feature / outcome: fail-open (mkdir idempotent — dir esistente)
            try:
                self._ftp.mkd(current)
            except ftplib.error_perm as e:
                logger.debug(
                    "ftp _mkdirs: mkd %r ignorato (probabile dir esistente): %s",
                    current, e,
                )
                pass

    @staticmethod
    def _parse_mlsd_time(s: str) -> datetime:
        """Parsa timestamp MLSD: YYYYMMDDHHmmss[.fff]"""
        if not s:
            return datetime(1970, 1, 1)
        try:
            return datetime.strptime(s[:14], "%Y%m%d%H%M%S")
        except ValueError:
            return datetime(1970, 1, 1)

    @staticmethod
    def _parse_mdtm(response: str) -> datetime:
        """Parsa risposta MDTM: '213 YYYYMMDDHHmmss'"""
        try:
            ts = response.split(" ", 1)[1].strip()[:14]
            return datetime.strptime(ts, "%Y%m%d%H%M%S")
        except (IndexError, ValueError):
            return datetime(1970, 1, 1)
