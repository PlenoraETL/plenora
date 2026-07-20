"""FTP and FTPS data source backend."""

import ftplib
import io
import queue
import socket
import ssl
import threading
import uuid
from datetime import datetime, timezone
from typing import Iterator, Optional

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceConnectionError,
    DataSourceConnectionTestResult,
    DataSourceFileNotFoundError,
    DataSourceInterface,
    DataSourcePermissionError,
    FileInfo,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _sanitize_ftp_error(e: Exception) -> str:
    """Map low-level FTP/socket errors to safe user-facing messages.

    Raw exception strings can contain banners, response details or hostnames,
    so the technical detail stays in server-side logs.
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


class FTPDataSource(DataSourceInterface):

    def __init__(self, config: BackendConfig):
        super().__init__(config)
        self._ftp: Optional[ftplib.FTP] = None

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

        # Retry transient: glitch di rete (DNS, RST, timeout) sono frequenti
        # su FTP; ftplib.error_temp e socket OSError sono retriable.
        # ftplib.error_perm (auth, 5xx codes) NON retriable -> propaga
        # immediato. AuthenticationException tipo eccezioni di credenziali
        # vengono sollevate come error_perm.
        from app.shared.connections.retry import call_with_retry

        def _do_connect():
            ftp = None
            try:
                if tls:
                    # FTP_TLS defaults to CERT_NONE in CPython.  Supplying a
                    # default context enables CA and hostname verification.
                    ftp = ftplib.FTP_TLS(context=ssl.create_default_context())
                    ftp.connect(host=host, port=port, timeout=15)
                    ftp.login(user=username, passwd=password)
                    ftp.prot_p()
                else:
                    ftp = ftplib.FTP()
                    ftp.connect(host=host, port=port, timeout=15)
                    ftp.login(user=username, passwd=password)
                ftp.set_pasv(passive)
                return ftp
            except Exception:
                if ftp is not None:
                    try:
                        ftp.close()
                    except Exception:
                        pass
                raise

        try:
            ftp = call_with_retry(
                _do_connect,
                op_name=f"ftp.connect({host}:{port})",
                max_attempts=3,
                backoff_base=0.5,
                retriable_excs=(ftplib.error_temp, OSError, TimeoutError),
            )
            self._ftp = ftp

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

    def test_connection(self) -> DataSourceConnectionTestResult:
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
            # DataSourceConnectionError message is already sanitized by connect().
            logger.warning(
                f"[GESTITO] FTP test_connection fallita: {type(e).__name__}: {e} "
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
        results = []

        try:
            entries = list(self._ftp.mlsd(remote, facts=["size", "modify", "type"]))
            for name, facts in entries:
                entry_type = facts.get("type")
                # Le subdirectory sono navigabili: il browser FE le
                # mostra come cartelle (mirror del backend `local`,
                # che usa `type='folder'`). Saltiamo le sentinel `.`
                # e `..` (`cdir`/`pdir` in MLSD).
                if entry_type == "dir":
                    if name in (".", ".."):
                        continue
                    results.append(FileInfo(
                        name=name,
                        path=self._join_path(path, name),
                        size=0,
                        modified_date=self._parse_mlsd_time(
                            facts.get("modify", "")
                        ),
                        type="folder",
                        hash=None,
                    ))
                    continue
                if entry_type != "file":
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
        # Salviamo il pwd corrente cosi' il probe-via-cwd e' reversibile
        # senza rompere lo stato della connessione condivisa.
        try:
            saved_pwd = self._ftp.pwd()
        except ftplib.all_errors:
            saved_pwd = None
        for full_name in names:
            name = full_name.split("/")[-1]
            if name in (".", ".."):
                continue
            try:
                size = self._ftp.size(full_name) or 0
                mdtm = self._ftp.sendcmd(f"MDTM {full_name}")
                modified_date = self._parse_mdtm(mdtm)
            except ftplib.all_errors as e:
                # NLST non discrimina file/directory. Se SIZE/MDTM
                # falliscono, proviamo a entrare nel path: success =
                # directory. Lasciamo il pwd com'era prima al termine
                # del probe.
                is_dir = False
                try:
                    self._ftp.cwd(full_name)
                    is_dir = True
                finally:
                    if is_dir and saved_pwd:
                        try:
                            self._ftp.cwd(saved_pwd)
                        except ftplib.all_errors:
                            pass
                if is_dir:
                    results.append(FileInfo(
                        name=name,
                        path=self._join_path(path, name),
                        size=0,
                        modified_date=datetime.now(timezone.utc),
                        type="folder",
                        hash=None,
                    ))
                else:
                    logger.debug(
                        "ftp list_nlst: skip entry %r (metadata unavailable): %s: %s",
                        full_name, type(e).__name__, e,
                    )
                continue
            if not self._matches_pattern(name, pattern):
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
        path = self._validated_path(path)
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
        path = self._validated_path(path)
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

    def iter_read_chunks(
        self, path: str, *, chunk_size: int = 64 * 1024
    ) -> Iterator[bytes]:
        """Streaming read via ftplib retrbinary + queue.

        `retrbinary` chiama il callback per ogni chunk; usiamo
        una queue thread-safe per yieldare i chunk al consumer del
        generator senza accumulare tutto in memoria.

        La cancellation cooperativa via threading.Event evita producer
        bloccati su `q.put()` quando il consumer abbandona il generator.
        Sotto load i thread orfani si accumulavano e prosciugavano sia
        il pool thread che le sessioni FTP.

        Fix: callback FTP rileva il cancel event e solleva una
        eccezione interna (_FTPCancelled) che bypassa retrbinary. Il
        finally drena la queue per sbloccare eventuali q.put ancora in
        volo prima del join.
        """
        self._check()
        path = self._validated_path(path)
        q: queue.Queue = queue.Queue(maxsize=4)  # bounded buffer
        sentinel = object()
        err_holder: list = []
        cancel_event = threading.Event()

        class _FTPCancelled(Exception):
            """Segnale interno per uscire da retrbinary su cancel consumer."""

        def _callback(chunk: bytes) -> None:
            # Poll-put: invece di bloccare per sempre su queue piena,
            # ricontrolliamo il cancel event ogni secondo. Se cancellato
            # raise per interrompere retrbinary; altrimenti procediamo.
            while not cancel_event.is_set():
                try:
                    q.put(chunk, timeout=1.0)
                    return
                except queue.Full:
                    continue
            raise _FTPCancelled()

        def _producer() -> None:
            try:
                self._ftp.retrbinary(
                    f"RETR {path}",
                    _callback,
                    blocksize=chunk_size,
                )
            except _FTPCancelled:
                pass  # consumer ha abbandonato: uscita pulita
            except Exception as e:
                err_holder.append(e)
            finally:
                # Never drop the sentinel while a live consumer can still be
                # waiting on q.get().  Cancellation is the only valid reason
                # to stop trying.
                while not cancel_event.is_set():
                    try:
                        q.put(sentinel, timeout=0.1)
                        break
                    except queue.Full:
                        continue

        t = threading.Thread(target=_producer, daemon=True)
        t.start()
        try:
            while True:
                try:
                    item = q.get(timeout=0.5)
                except queue.Empty:
                    if not t.is_alive():
                        break
                    continue
                if item is sentinel:
                    break
                yield item
        finally:
            # Signal cancel + drena la queue per liberare un eventuale
            # q.put pendente del producer (timeout 1.0). Senza il drain
            # il producer poteva restare bloccato un secondo extra per
            # ogni chunk ancora in coda.
            cancel_event.set()
            # Drain briefly; a socket blocked in retrbinary is released by
            # disconnect() when the surrounding service context exits.
            for _ in range(20):
                if not t.is_alive():
                    break
                try:
                    q.get(timeout=0.05)
                except queue.Empty:
                    pass
            t.join(timeout=0.1)
        if err_holder:
            e = err_holder[0]
            if isinstance(e, ftplib.error_perm):
                if "550" in str(e):
                    raise DataSourceFileNotFoundError(f"File non trovato: {path!r}") from e
                raise DataSourcePermissionError(f"Accesso negato: {path!r}") from e
            raise DataSourceConnectionError(f"Errore iter_read_chunks FTP: {_sanitize_ftp_error(e)}") from e

    def write_file(self, path: str, content: bytes) -> None:
        self._check()
        path = self._validated_path(path)
        parent = "/".join(path.split("/")[:-1])
        if parent:
            self._mkdirs(parent)
        temp_path = self._temporary_path(path)
        buf = io.BytesIO(content)
        try:
            self._ftp.storbinary(f"STOR {temp_path}", buf)
            self._replace_from_temp(temp_path, path)
        except ftplib.error_perm as e:
            raise DataSourcePermissionError(f"Scrittura negata: {path!r}") from e
        except ftplib.all_errors as e:
            logger.warning(
                f"[GESTITO] FTP write_file fallita: path={path!r} "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(f"Errore write_file FTP: {_sanitize_ftp_error(e)}") from e
        finally:
            self._cleanup_remote(temp_path)

    def write_chunks(self, path: str, chunks) -> None:
        """Streaming write via ftplib storbinary + adapter file-like.

        `storbinary` legge da un file-like in
        blocks; wrappiamo l'iterable con un adapter che concatena i
        chunk al volo (peak ~chunk_size)."""
        self._check()
        path = self._validated_path(path)
        parent = "/".join(path.split("/")[:-1])
        if parent:
            self._mkdirs(parent)

        # File-like adapter sopra l'iterable di bytes: ftplib chiama
        # `read(blocksize)` finche' non ottiene bytes vuoti.
        chunks_iter = iter(chunks)
        leftover = bytearray()

        class _ChunksAdapter:
            def read(self_inner, n: int = -1) -> bytes:
                if n <= 0:
                    n = 8192
                while len(leftover) < n:
                    try:
                        nxt = next(chunks_iter)
                    except StopIteration:
                        break
                    if nxt:
                        leftover.extend(nxt)
                out = bytes(leftover[:n])
                del leftover[:n]
                return out

        temp_path = self._temporary_path(path)
        try:
            self._ftp.storbinary(f"STOR {temp_path}", _ChunksAdapter())
            self._replace_from_temp(temp_path, path)
        except ftplib.error_perm as e:
            raise DataSourcePermissionError(f"Scrittura negata: {path!r}") from e
        except ftplib.all_errors as e:
            logger.warning(
                f"[GESTITO] FTP write_chunks fallita: path={path!r} "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(f"Errore write_chunks FTP: {_sanitize_ftp_error(e)}") from e
        finally:
            self._cleanup_remote(temp_path)

    def delete_file(self, path: str) -> None:
        self._check()
        path = self._validated_path(path)
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
        path = self._validated_path(path)
        try:
            return self._ftp.size(path) is not None
        except ftplib.error_perm as e:
            if "550" in str(e):
                return False
            raise DataSourcePermissionError(f"Verifica accesso negata: {path!r}") from e
        except ftplib.all_errors as e:
            raise DataSourceConnectionError(
                f"Errore file_exists FTP: {_sanitize_ftp_error(e)}"
            ) from e

    def _check(self):
        if self._ftp is None:
            raise DataSourceConnectionError("Non connesso. Usa il context manager 'with'.")

    def _maybe_hash(self, path: str, include_hash: bool) -> Optional[str]:
        if not include_hash:
            return None
        try:
            return self._compute_stream_hash(path)
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
            try:
                self._ftp.mkd(current)
            except ftplib.error_perm as e:
                if self._directory_exists(current):
                    continue
                raise DataSourcePermissionError(
                    f"Creazione cartella FTP negata: {current!r}"
                ) from e

    def _directory_exists(self, path: str) -> bool:
        try:
            saved = self._ftp.pwd()
            self._ftp.cwd(path)
        except ftplib.all_errors:
            return False
        try:
            self._ftp.cwd(saved)
        except ftplib.all_errors as e:
            raise DataSourceConnectionError(
                "Impossibile ripristinare la directory FTP corrente"
            ) from e
        return True

    @staticmethod
    def _temporary_path(path: str) -> str:
        parent, _, _ = path.rpartition("/")
        temp_name = f"plenora-upload-{uuid.uuid4().hex}.part"
        return f"{parent}/{temp_name}" if parent else temp_name

    def _cleanup_remote(self, path: str) -> None:
        try:
            self._ftp.delete(path)
        except ftplib.all_errors:
            pass

    def _replace_from_temp(self, temp_path: str, target_path: str) -> None:
        """Promote a complete upload, preserving an existing target."""
        try:
            self._ftp.rename(temp_path, target_path)
            return
        except ftplib.error_perm as direct_error:
            try:
                target_exists = self._ftp.size(target_path) is not None
            except ftplib.error_perm as size_error:
                if "550" in str(size_error):
                    target_exists = False
                else:
                    raise direct_error
            if not target_exists:
                raise direct_error

        backup_path = f"{temp_path}.backup"
        self._ftp.rename(target_path, backup_path)
        try:
            self._ftp.rename(temp_path, target_path)
        except Exception:
            try:
                self._ftp.rename(backup_path, target_path)
            except Exception as restore_error:
                logger.error("FTP rollback replace fallito: %s", restore_error)
            raise
        self._cleanup_remote(backup_path)

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
