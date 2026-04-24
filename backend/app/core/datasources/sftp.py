"""
PLENORA-ETL — Data Source Backend: SFTP
Connessione SSH/SFTP con autenticazione tramite password o chiave SSH privata.
Dipendenza: paramiko (pip install paramiko)

Configurazione extra attesa in config.extra:
    host (str)                          es. "sftp.server.it"
    port (int)                          default 22
    username (str)
    password (str)                      opzionale se si usa chiave SSH
    private_key (str)                   contenuto PEM della chiave privata
    private_key_passphrase (str)        passphrase chiave, opzionale
    host_key_policy ("auto_add"|"reject")  default "auto_add"
"""

import io
import logging
import socket
import stat
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceInterface,
    DataSourceConnectionError,
    DataSourceFileNotFoundError,
    DataSourcePermissionError,
    FileInfo,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import paramiko


def _import_paramiko():
    try:
        import paramiko
        return paramiko
    except ImportError:
        return None


def _sanitize_sftp_error(e: Exception) -> str:
    """F5-B2 (2026-04-24): mappa eccezioni paramiko/socket a messaggi
    canonici user-facing, SENZA esporre str(e) che puo' contenere host,
    username, key fingerprint, auth method trace. Il dettaglio tecnico
    resta nei log server-side (logger.warning/error con exc_info=True).
    """
    paramiko = _import_paramiko()
    if paramiko is not None:
        if isinstance(e, paramiko.AuthenticationException):
            return "Autenticazione SFTP fallita: credenziali non valide"
        if isinstance(e, paramiko.BadHostKeyException):
            return "Host key SSH non verificabile"
        if isinstance(e, paramiko.SSHException):
            return "Errore protocollo SSH"
    if isinstance(e, socket.timeout):
        return "Timeout connessione SFTP"
    if isinstance(e, socket.gaierror):
        return "Host SFTP non risolvibile"
    if isinstance(e, ConnectionRefusedError):
        return "Connessione SFTP rifiutata"
    if isinstance(e, OSError):
        return "Connessione SFTP rifiutata"
    return "Connessione SFTP fallita"


class SFTPDataSource(DataSourceInterface):

    def __init__(self, config: BackendConfig):
        super().__init__(config)
        self._ssh = None
        self._sftp = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def connect(self) -> None:
        paramiko = _import_paramiko()
        if paramiko is None:
            raise DataSourceConnectionError(
                "Libreria 'paramiko' non installata. Esegui: pip install paramiko"
            )

        extra = self.config.extra
        host = extra.get("host", "")
        port = int(extra.get("port", 22))
        username = extra.get("username", "")
        password = extra.get("password", "")
        private_key_pem = extra.get("private_key", "")
        passphrase = extra.get("private_key_passphrase") or None
        policy = extra.get("host_key_policy", "auto_add")

        if not host:
            raise DataSourceConnectionError("Host SFTP non configurato")

        try:
            ssh = paramiko.SSHClient()

            if policy == "reject":
                ssh.load_system_host_keys()
                ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
            else:
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            kwargs = {"hostname": host, "port": port, "username": username, "timeout": 15}

            if private_key_pem:
                kwargs["pkey"] = self._load_key(paramiko, private_key_pem, passphrase)
            elif password:
                kwargs["password"] = password
            else:
                raise DataSourceConnectionError(
                    "Credenziali SFTP mancanti: fornire 'password' o 'private_key'"
                )

            ssh.connect(**kwargs)
            self._ssh = ssh
            self._sftp = ssh.open_sftp()

            root = self.config.root_path
            if root:
                try:
                    self._sftp.chdir(root)
                except IOError as e:
                    logger.warning(
                        f"[GESTITO] SFTP chdir root fallito: root={root!r} "
                        f"{type(e).__name__}: {e}",
                        exc_info=True,
                    )
                    raise DataSourceConnectionError(
                        f"Root path SFTP non accessibile: {root!r}"
                    ) from e

        except paramiko.AuthenticationException as e:
            logger.warning(
                f"[GESTITO] SFTP auth fallita: {type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(_sanitize_sftp_error(e)) from e
        except paramiko.SSHException as e:
            logger.warning(
                f"[GESTITO] SFTP SSH exception: {type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(_sanitize_sftp_error(e)) from e
        except OSError as e:
            logger.warning(
                f"[GESTITO] SFTP OS error: {type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(_sanitize_sftp_error(e)) from e

    def disconnect(self) -> None:
        for obj in (self._sftp, self._ssh):
            if obj:
                # intent: optional feature / outcome: fail-open (close best-effort)
                try:
                    obj.close()
                except Exception as e:
                    logger.debug(
                        "sftp disconnect: close() skipped (%s): %s: %s",
                        type(obj).__name__, type(e).__name__, e,
                    )
        self._sftp = None
        self._ssh = None

    def test_connection(self) -> dict:
        try:
            self.connect()
            entries = self._sftp.listdir_attr(".")
            file_count = sum(1 for e in entries if stat.S_ISREG(e.st_mode))
            auth = "chiave_ssh" if self.config.extra.get("private_key") else "password"
            return {
                "ok": True,
                "message": "Connessione SFTP riuscita",
                "details": {
                    "host": self.config.extra.get("host"),
                    "port": self.config.extra.get("port", 22),
                    "auth_method": auth,
                    "files_in_root": file_count,
                },
            }
        except DataSourceConnectionError as e:
            # F5-B2: `e` qui e' il nostro wrapper con messaggio gia' canonico
            # (vedi connect() + _sanitize_sftp_error), quindi str(e) e' safe.
            logger.warning(
                f"[GESTITO] SFTP test_connection fallita: {type(e).__name__}: {e} "
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
        try:
            entries = self._sftp.listdir_attr(remote)
        except IOError as e:
            raise DataSourceFileNotFoundError(f"Path non trovato: {path!r}") from e

        results = []
        for entry in entries:
            if not stat.S_ISREG(entry.st_mode):
                continue
            if not self._matches_pattern(entry.filename, pattern):
                continue

            file_path = self._join_path(path, entry.filename)
            hash_val = None
            if include_hash:
                # intent: optional feature / outcome: fail-open (hash opzionale)
                try:
                    hash_val = self._compute_hash(self.read_file(file_path))
                except Exception as e:
                    logger.debug(
                        "sftp list_files: hash non calcolato per %r: %s: %s",
                        file_path, type(e).__name__, e,
                    )

            results.append(FileInfo(
                name=entry.filename,
                path=file_path,
                size=entry.st_size or 0,
                modified_date=datetime.fromtimestamp(entry.st_mtime or 0),
                type=self._extension(entry.filename),
                hash=hash_val,
            ))

        results.sort(key=lambda f: f.modified_date, reverse=True)
        return results

    def file_info(self, path: str) -> FileInfo:
        self._check()
        try:
            attrs = self._sftp.stat(path)
        except IOError as e:
            raise DataSourceFileNotFoundError(f"File non trovato: {path!r}") from e

        name = path.split("/")[-1]
        return FileInfo(
            name=name,
            path=path,
            size=attrs.st_size or 0,
            modified_date=datetime.fromtimestamp(attrs.st_mtime or 0),
            type=self._extension(name),
        )

    def read_file(self, path: str) -> bytes:
        self._check()
        buf = io.BytesIO()
        try:
            with self._sftp.open(path, "rb") as f:
                buf.write(f.read())
        except IOError as e:
            if "No such file" in str(e) or "does not exist" in str(e):
                raise DataSourceFileNotFoundError(f"File non trovato: {path!r}") from e
            raise DataSourcePermissionError(f"Accesso negato: {path!r}") from e
        return buf.getvalue()

    def write_file(self, path: str, content: bytes) -> None:
        self._check()
        parent = "/".join(path.split("/")[:-1])
        if parent:
            self._mkdirs(parent)
        try:
            with self._sftp.open(path, "wb") as f:
                f.write(content)
        except IOError as e:
            raise DataSourcePermissionError(f"Scrittura negata: {path!r}") from e

    def delete_file(self, path: str) -> None:
        self._check()
        try:
            self._sftp.remove(path)
        except IOError as e:
            if "No such file" in str(e) or "does not exist" in str(e):
                raise DataSourceFileNotFoundError(f"File non trovato: {path!r}") from e
            raise DataSourcePermissionError(f"Cancellazione negata: {path!r}") from e

    def file_exists(self, path: str) -> bool:
        self._check()
        try:
            self._sftp.stat(path)
            return True
        except IOError:
            return False

    # ── Helpers privati ─────────────────────────────────────────────────────

    def _check(self):
        if self._sftp is None:
            raise DataSourceConnectionError("Non connesso. Usa il context manager 'with'.")

    def _mkdirs(self, remote_path: str) -> None:
        parts = remote_path.strip("/").split("/")
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            # intent: optional feature / outcome: fail-open (mkdir idempotent — dir esistente)
            try:
                self._sftp.mkdir(current)
            except IOError as e:
                logger.debug(
                    "sftp _mkdirs: mkdir %r ignorato (probabile dir esistente): %s",
                    current, e,
                )

    @staticmethod
    def _load_key(paramiko, pem: str, passphrase: Optional[str]):
        """
        Carica chiave SSH privata da stringa PEM.
        Prova RSA → Ed25519 → ECDSA → DSS in sequenza.
        """
        passphrase_bytes = passphrase.encode() if passphrase else None
        for cls in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey]:
            try:
                buf = io.StringIO(pem)
                return cls.from_private_key(buf, password=passphrase_bytes)
            except paramiko.SSHException:
                continue
        raise DataSourceConnectionError(
            "Impossibile caricare la chiave SSH. Formati supportati: RSA, Ed25519, ECDSA, DSS"
        )