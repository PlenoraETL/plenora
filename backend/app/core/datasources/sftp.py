"""SFTP data source backend."""

import io
import errno
import socket
import stat
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceInterface,
    DataSourceConnectionError,
    DataSourceConnectionTestResult,
    DataSourceFileNotFoundError,
    DataSourcePermissionError,
    FileInfo,
)
from app.shared.security.tls_policy import is_prod_like_env
from app.utils.logger import get_logger

logger = get_logger(__name__)

def _import_paramiko() -> Optional[Any]:
    try:
        import paramiko
        return paramiko
    except ImportError:
        return None


def _sanitize_sftp_error(e: Exception) -> str:
    """Map low-level SFTP/socket errors to safe user-facing messages.

    Raw exception strings can contain host, username, key fingerprint or auth
    method traces, so technical detail stays in server-side logs.
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
        # Host-key policy default env-aware:
        #  - prod/staging -> "reject" (MitM-safe, richiede known_hosts)
        #  - dev/test     -> "auto_add" (preserva ergonomia legacy)
        # Valore esplicito in `extra["host_key_policy"]` ha sempre precedenza
        # — i test contract esistenti (`test_sftp_security_contract.py`)
        # restano verdi perche' settano la policy esplicita.
        raw_policy = extra.get("host_key_policy")
        if raw_policy is None:
            policy = "reject" if is_prod_like_env() else "auto_add"
        else:
            policy = raw_policy

        if not host:
            raise DataSourceConnectionError("Host SFTP non configurato")

        try:
            ssh = paramiko.SSHClient()
            self._ssh = ssh

            if policy == "reject":
                ssh.load_system_host_keys()
                ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
            elif policy == "auto_add":
                # AutoAddPolicy accetta silenziosamente qualsiasi host key
                # (incluso un attaccante MitM). Log esplicito per security
                # monitoring: in prod/staging questo path richiede config
                # esplicita "auto_add", quindi e' un opt-in consapevole.
                logger.warning(
                    "[SECURITY] SFTP %s:%d usa AutoAddPolicy — host key non "
                    "verificata, rischio MitM. Per blindare imposta "
                    "host_key_policy='reject' e popola known_hosts.",
                    host, port,
                )
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            else:
                raise DataSourceConnectionError(
                    f"host_key_policy SFTP non valida: {policy!r}"
                )

            kwargs = {"hostname": host, "port": port, "username": username, "timeout": 15}

            if private_key_pem:
                kwargs["pkey"] = self._load_key(paramiko, private_key_pem, passphrase)
            elif password:
                kwargs["password"] = password
            else:
                raise DataSourceConnectionError(
                    "Credenziali SFTP mancanti: fornire 'password' o 'private_key'"
                )

            # Retry transient: timeout/RST/DNS sono frequenti su SFTP.
            # SSHException da auth/banner errato non e' transient; viene
            # rilevata fuori dal retry loop via except specifico sotto.
            # paramiko.AuthenticationException NON e' OSError, propaga
            # immediato senza retry.
            from app.shared.connections.retry import call_with_retry

            call_with_retry(
                lambda: ssh.connect(**kwargs),
                op_name=f"sftp.connect({host}:{port})",
                max_attempts=3,
                backoff_base=0.5,
                retriable_excs=(OSError, TimeoutError),
            )
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

        except DataSourceConnectionError:
            self.disconnect()
            raise
        except paramiko.AuthenticationException as e:
            logger.warning(
                f"[GESTITO] SFTP auth fallita: {type(e).__name__}: {e}",
                exc_info=True,
            )
            self.disconnect()
            raise DataSourceConnectionError(_sanitize_sftp_error(e)) from e
        except paramiko.SSHException as e:
            logger.warning(
                f"[GESTITO] SFTP SSH exception: {type(e).__name__}: {e}",
                exc_info=True,
            )
            self.disconnect()
            raise DataSourceConnectionError(_sanitize_sftp_error(e)) from e
        except OSError as e:
            logger.warning(
                f"[GESTITO] SFTP OS error: {type(e).__name__}: {e}",
                exc_info=True,
            )
            self.disconnect()
            raise DataSourceConnectionError(_sanitize_sftp_error(e)) from e

    def disconnect(self) -> None:
        for obj in (self._sftp, self._ssh):
            if obj:
                try:
                    obj.close()
                except Exception as e:
                    logger.debug(
                        "sftp disconnect: close() skipped (%s): %s: %s",
                        type(obj).__name__, type(e).__name__, e,
                    )
        self._sftp = None
        self._ssh = None

    def test_connection(self) -> DataSourceConnectionTestResult:
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
            # DataSourceConnectionError message is already sanitized by connect().
            logger.warning(
                f"[GESTITO] SFTP test_connection fallita: {type(e).__name__}: {e} "
                f"(cause: {type(e.__cause__).__name__ if e.__cause__ else 'none'})",
                exc_info=True,
            )
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
        self._check()
        path = self._validated_path(path, allow_empty=True)
        remote = path or "."
        try:
            entries = self._sftp.listdir_attr(remote)
        except IOError as e:
            self._raise_io_error(e, path, "lettura")

        results = []
        for entry in entries:
            # Le subdirectory sono navigabili nel browser FE: convenzione
            # `type='folder'` (mirror di `local`/`ftp`).
            if stat.S_ISDIR(entry.st_mode):
                if entry.filename in (".", ".."):
                    continue
                results.append(FileInfo(
                    name=entry.filename,
                    path=self._join_path(path, entry.filename),
                    size=0,
                    modified_date=datetime.fromtimestamp(entry.st_mtime or 0, tz=timezone.utc),
                    type="folder",
                    hash=None,
                ))
                continue
            if not stat.S_ISREG(entry.st_mode):
                continue
            if not self._matches_pattern(entry.filename, pattern):
                continue

            file_path = self._join_path(path, entry.filename)
            hash_val = None
            if include_hash:
                try:
                    hash_val = self._compute_stream_hash(file_path)
                except Exception as e:
                    logger.debug(
                        "sftp list_files: hash non calcolato per %r: %s: %s",
                        file_path, type(e).__name__, e,
                    )

            results.append(FileInfo(
                name=entry.filename,
                path=file_path,
                size=entry.st_size or 0,
                modified_date=datetime.fromtimestamp(entry.st_mtime or 0, tz=timezone.utc),
                type=self._extension(entry.filename),
                hash=hash_val,
            ))

        results.sort(key=lambda f: f.modified_date, reverse=True)
        return results

    def file_info(self, path: str) -> FileInfo:
        self._check()
        path = self._validated_path(path)
        try:
            attrs = self._sftp.stat(path)
        except IOError as e:
            self._raise_io_error(e, path, "lettura")

        name = path.split("/")[-1]
        return FileInfo(
            name=name,
            path=path,
            size=attrs.st_size or 0,
            modified_date=datetime.fromtimestamp(attrs.st_mtime or 0, tz=timezone.utc),
            type=self._extension(name),
        )

    def read_file(self, path: str) -> bytes:
        self._check()
        path = self._validated_path(path)
        buf = io.BytesIO()
        try:
            with self._sftp.open(path, "rb") as f:
                buf.write(f.read())
        except IOError as e:
            self._raise_io_error(e, path, "lettura")
        return buf.getvalue()

    def iter_read_chunks(
        self, path: str, *, chunk_size: int = 64 * 1024
    ) -> Iterator[bytes]:
        """Streaming read via paramiko file-like.

        `paramiko.SFTPClient.open(path, 'rb')` ritorna un file-like che
        supporta `.read(chunk_size)` con prefetch automatico. Peak RAM
        ~chunk_size, no double-buffer come in `read_file`."""
        self._check()
        path = self._validated_path(path)
        try:
            with self._sftp.open(path, "rb") as f:
                # paramiko ottimizza con prefetch per file > 32KB; lasciamo
                # default.
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except IOError as e:
            self._raise_io_error(e, path, "lettura")

    def write_file(self, path: str, content: bytes) -> None:
        self._check()
        path = self._validated_path(path)
        parent = "/".join(path.split("/")[:-1])
        if parent:
            self._mkdirs(parent)
        temp_path = self._temporary_path(path)
        try:
            with self._sftp.open(temp_path, "wb") as f:
                f.write(content)
            self._replace_from_temp(temp_path, path)
        except IOError as e:
            self._raise_io_error(e, path, "scrittura")
        finally:
            self._cleanup_remote(temp_path)

    def write_chunks(self, path: str, chunks) -> None:
        """Streaming write via paramiko
        file-like. Scrive chunk-per-chunk con peak RAM ~chunk_size."""
        self._check()
        path = self._validated_path(path)
        parent = "/".join(path.split("/")[:-1])
        if parent:
            self._mkdirs(parent)
        temp_path = self._temporary_path(path)
        try:
            with self._sftp.open(temp_path, "wb") as f:
                for chunk in chunks:
                    if chunk:
                        f.write(chunk)
            self._replace_from_temp(temp_path, path)
        except IOError as e:
            self._raise_io_error(e, path, "scrittura")
        finally:
            self._cleanup_remote(temp_path)

    def delete_file(self, path: str) -> None:
        self._check()
        path = self._validated_path(path)
        try:
            self._sftp.remove(path)
        except IOError as e:
            self._raise_io_error(e, path, "cancellazione")

    def file_exists(self, path: str) -> bool:
        self._check()
        path = self._validated_path(path)
        try:
            self._sftp.stat(path)
            return True
        except IOError as e:
            if self._is_not_found(e):
                return False
            self._raise_io_error(e, path, "verifica")

    def _check(self):
        if self._sftp is None:
            raise DataSourceConnectionError("Non connesso. Usa il context manager 'with'.")

    @staticmethod
    def _is_not_found(e: IOError) -> bool:
        msg = str(e).lower()
        return getattr(e, "errno", None) == errno.ENOENT or any(
            marker in msg for marker in ("no such file", "does not exist", "not found")
        )

    @classmethod
    def _raise_io_error(cls, e: IOError, path: str, action: str):
        if cls._is_not_found(e):
            raise DataSourceFileNotFoundError(f"File non trovato: {path!r}") from e
        if getattr(e, "errno", None) in (errno.EACCES, errno.EPERM):
            raise DataSourcePermissionError(f"Operazione SFTP negata: {path!r}") from e
        raise DataSourceConnectionError(
            f"Errore connessione SFTP durante {action}"
        ) from e

    def _mkdirs(self, remote_path: str) -> None:
        parts = remote_path.strip("/").split("/")
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            try:
                self._sftp.mkdir(current)
            except IOError as e:
                try:
                    attrs = self._sftp.stat(current)
                    if stat.S_ISDIR(attrs.st_mode):
                        continue
                except IOError:
                    pass
                self._raise_io_error(e, current, "creazione cartella")

    @staticmethod
    def _temporary_path(path: str) -> str:
        parent, _, _ = path.rpartition("/")
        temp_name = f"plenora-upload-{uuid.uuid4().hex}.part"
        return f"{parent}/{temp_name}" if parent else temp_name

    def _cleanup_remote(self, path: str) -> None:
        try:
            self._sftp.remove(path)
        except IOError:
            pass

    def _path_exists(self, path: str) -> bool:
        try:
            self._sftp.stat(path)
            return True
        except IOError as e:
            if getattr(e, "errno", None) == 2 or "no such file" in str(e).lower():
                return False
            raise

    def _replace_from_temp(self, temp_path: str, target_path: str) -> None:
        # OpenSSH posix-rename is an atomic overwrite when supported.
        posix_rename = getattr(self._sftp, "posix_rename", None)
        if callable(posix_rename):
            try:
                posix_rename(temp_path, target_path)
                return
            except IOError as e:
                msg = str(e).lower()
                if getattr(e, "errno", None) != 95 and "unsupported" not in msg:
                    raise

        if not self._path_exists(target_path):
            self._sftp.rename(temp_path, target_path)
            return

        backup_path = f"{temp_path}.backup"
        self._sftp.rename(target_path, backup_path)
        try:
            self._sftp.rename(temp_path, target_path)
        except Exception:
            try:
                self._sftp.rename(backup_path, target_path)
            except Exception as restore_error:
                logger.error("SFTP rollback replace fallito: %s", restore_error)
            raise
        self._cleanup_remote(backup_path)

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
