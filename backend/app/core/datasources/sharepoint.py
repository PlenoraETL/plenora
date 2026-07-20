"""
PLENORA-ETL — Data Source Backend: SharePoint Online

Usa la libreria Office365-REST-Python-Client (stessa usata in sharepoint_uploader.py)
"""

from io import BytesIO
from urllib.parse import quote, urlparse
import uuid

import io
import tempfile
from datetime import datetime
from typing import Iterator, Optional

from app.config import settings
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


def _env_float(name: str, default: float) -> float:
    """Read a positive float setting, falling back on invalid values."""
    try:
        v = float(settings.get(name, default))
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _sanitize_sharepoint_error(e: Exception) -> str:
    """Map SharePoint/client errors to safe user-facing messages.

    Raw exceptions can contain authenticated URLs, tenant IDs, client IDs,
    bearer tokens, AADSTS codes or response bodies with cookies/traces. The
    technical detail stays in server-side logs.

    Priorita': AADSTS/OAuth codes -> HTTP status -> network/TLS -> fallback.
    """
    msg = str(e)
    msg_l = msg.lower()
    # ── Auth failures (Azure AD / OAuth2 client_credentials) ────────────
    if "aadsts" in msg_l:
        return "Autenticazione SharePoint fallita: verifica tenant/client_id/secret"
    if "invalid_client" in msg_l or "unauthorized_client" in msg_l:
        return "Autenticazione SharePoint fallita: client credentials non valide"
    if "invalid_grant" in msg_l or "invalid_scope" in msg_l:
        return "Autorizzazione SharePoint negata: scope o grant non configurati"
    # ── HTTP status codes ───────────────────────────────────────────────
    if " 401" in msg or "unauthorized" in msg_l:
        return "Accesso SharePoint non autorizzato"
    if " 403" in msg or "forbidden" in msg_l:
        return "Accesso SharePoint negato dal server"
    if " 404" in msg or "not found" in msg_l:
        return "Risorsa SharePoint non trovata"
    if " 429" in msg or "throttle" in msg_l or "too many requests" in msg_l:
        return "SharePoint sta limitando le richieste (throttling), riprova piu' tardi"
    if any(code in msg for code in (" 500", " 502", " 503", " 504")):
        return "Errore del server SharePoint (5xx), riprova piu' tardi"
    # ── Network / TLS ───────────────────────────────────────────────────
    if "ssl" in msg_l or "tls" in msg_l or "certificate" in msg_l or "handshake" in msg_l:
        return "Errore protocollo TLS con SharePoint"
    if isinstance(e, TimeoutError) or "timeout" in msg_l:
        return "Timeout connessione SharePoint"
    if isinstance(e, ConnectionRefusedError) or "connection refused" in msg_l:
        return "Connessione SharePoint rifiutata"
    if "name or service not known" in msg_l or "nodename nor servname" in msg_l:
        return "Host SharePoint non risolvibile"
    # ── Fallback ───────────────────────────────────────────────────────
    return "Errore connessione SharePoint"


def _import_office365():
    try:
        from office365.runtime.auth.client_credential import ClientCredential
        from office365.sharepoint.client_context import ClientContext
        return ClientCredential, ClientContext
    except ImportError:
        return None, None


def _close_context_best_effort(ctx) -> None:
    """Close a partially-created Office365 context without leaking a session."""
    if ctx is None:
        return
    try:
        close = getattr(ctx, "close", None)
        if callable(close):
            close()
    except Exception:
        pass
    for attr in ("_session", "session", "_client_context_session"):
        try:
            session = getattr(ctx, attr, None)
            close = getattr(session, "close", None)
            if callable(close):
                close()
        except Exception:
            pass
    try:
        pending = ctx.pending_request()
        session = getattr(pending, "_session", None)
        close = getattr(session, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


class SharePointDataSource(DataSourceInterface):
    """
    Data Source SharePoint Online tramite Office365-REST-Python-Client.

    Autenticazione: App Registration con Client Credentials
    (stesso meccanismo di sharepoint_uploader.py già funzionante).

    root_path = nome della Document Library (es. "GIS", "Documenti condivisi")
    I path relativi passati alle operazioni sono cartelle dentro la library.
    """

    def __init__(self, config: BackendConfig):
        super().__init__(config)
        self._ctx = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def connect(self) -> None:
        ClientCredential, ClientContext = _import_office365()
        if ClientCredential is None:
            raise DataSourceConnectionError(
                "Libreria 'Office365-REST-Python-Client' non installata. "
                "Esegui: pip install Office365-REST-Python-Client"
            )

        extra = self.config.extra
        site_url   = extra.get("site_url", "")
        client_id  = extra.get("client_id", "")
        client_secret = extra.get("client_secret", "")
        tenant_id  = extra.get("tenant_id", "")

        if not all([site_url, client_id, client_secret]):
            raise DataSourceConnectionError(
                "Configurazione SharePoint incompleta: site_url, client_id, client_secret richiesti"
            )

        # Retry transient: SharePoint subisce 502/503 transitori e socket
        # timeout sui datacenter Microsoft (vedi fake-rest /sharepoint/
        # transient_5xx). AADSTS auth errors NON sono transient: vengono
        # mappati come ClientRequestException con AADSTS code stabile, NON
        # retriable; vengono inclusi in `Exception` ma il narrow su
        # (OSError, TimeoutError, ConnectionError) li esclude — propagano
        # immediato e finiscono nell'except Exception fuori dal retry.
        from app.shared.connections.retry import call_with_retry

        def _do_auth():
            ctx = None
            try:
                credentials = ClientCredential(client_id, client_secret)
                ctx = ClientContext(site_url).with_credentials(credentials)
                if tenant_id:
                    ctx.authentication_context.tenant = tenant_id
                web = ctx.web
                ctx.load(web)
                ctx.execute_query()
                return ctx
            except Exception:
                _close_context_best_effort(ctx)
                raise

        try:
            ctx = call_with_retry(
                _do_auth,
                op_name=f"sharepoint.connect({site_url})",
                max_attempts=3,
                backoff_base=0.5,
                retriable_excs=(OSError, TimeoutError, ConnectionError),
            )
            self._ctx = ctx

        except Exception as e:
            # Auth parziale puo' avere gia' aperto la sessione HTTP interna.
            # Lasciamo `_ctx` a None e rilanciamo con messaggio sanitizzato.
            logger.warning(
                f"[GESTITO] SharePoint auth fallita: {type(e).__name__}: {e}",
                exc_info=True,
            )
            # self._ctx resta None: `disconnect()` no-op safe.
            self._ctx = None
            raise DataSourceConnectionError(_sanitize_sharepoint_error(e)) from e

    def disconnect(self) -> None:
        """Chiude la sessione HTTP interna di Office365 prima di
        dereferenziare il `ClientContext`.

        Cleanup best-effort multi-probe: la libreria non espone un'API
        stabile per chiudere la session; usiamo introspection con fallback
        e tentiamo anche `ctx.close()` se disponibile.

        Probe order:
          1. `ctx.close()` — API pubblica (se introdotta in future versioni)
          2. `ctx._session` — internal attr in Office365-REST-Python-Client < 2.5
          3. `ctx.session` — alias in alcune versioni
          4. `ctx._client_context_session` — internal in versioni piu' recenti
          5. `ctx.pending_request()._session` — fallback su pending request

        Se NESSUNA delle probe ha successo, emettiamo un WARNING
        esplicito che indica che la sessione HTTP NON e' stata chiusa
        e suggerisce di aggiornare le probe nella prossima versione
        della libreria.
        """
        ctx = self._ctx
        if ctx is None:
            return

        # 1. Tentativo API pubblica future: ctx.close()
        if hasattr(ctx, "close") and callable(getattr(ctx, "close")):
            try:
                ctx.close()
                self._ctx = None
                return
            except Exception as e:
                logger.debug(
                    "[GESTITO] SharePoint ctx.close() fallito (%s): provo probe internal.",
                    type(e).__name__,
                )

        # 2-5. Probe internal attributes
        session_candidates = [
            ("_session", lambda: getattr(ctx, "_session", None)),
            ("session", lambda: getattr(ctx, "session", None)),
            ("_client_context_session", lambda: getattr(ctx, "_client_context_session", None)),
            ("pending_request._session", lambda: (
                getattr(ctx.pending_request(), "_session", None)
                if hasattr(ctx, "pending_request") and callable(getattr(ctx, "pending_request"))
                else None
            )),
        ]

        closed = False
        for probe_name, probe_fn in session_candidates:
            try:
                session = probe_fn()
            except Exception:
                continue
            if session is None:
                continue
            try:
                session.close()
                logger.debug(
                    "[GESTITO] SharePoint disconnect via probe %r succeeded.",
                    probe_name,
                )
                closed = True
                break
            except Exception as e:
                logger.debug(
                    "[GESTITO] SharePoint disconnect probe %r: close() fallita (%s).",
                    probe_name, type(e).__name__,
                )

        if not closed:
            # Nessuna probe ha chiuso la sessione: warning per upgrade
            # operativo (probabilmente la libreria ha refactored gli internals).
            logger.warning(
                "[GESTITO] SharePoint disconnect: nessuna delle 4 probe "
                "internal e' riuscita a chiudere la requests.Session. "
                "Il riferimento sara' liberato dal GC (non-deterministico). "
                "Verifica versione Office365-REST-Python-Client e aggiorna "
                "le probe in app/core/datasources/sharepoint.py:disconnect."
            )

        self._ctx = None

    def test_connection(self) -> DataSourceConnectionTestResult:
        try:
            self.connect()
            web = self._ctx.web
            self._ctx.load(web)
            self._ctx.execute_query()
            title = web.properties.get("Title", "")
            return {
                "ok": True,
                "message": f"Connessione SharePoint riuscita — Sito: {title}",
                "details": {
                    "site_url": self.config.extra.get("site_url"),
                    "site_title": title,
                    "library": self.config.root_path,
                },
            }
        except DataSourceConnectionError as e:
            # `e` e' il wrapper DataSourceConnectionError con messaggio
            # gia' canonico da `_sanitize_sharepoint_error`.
            logger.warning(
                f"[GESTITO] SharePoint test_connection fallita: {type(e).__name__}: {e} "
                f"(cause: {type(e.__cause__).__name__ if e.__cause__ else 'none'})",
                exc_info=True,
            )
            return {"ok": False, "message": str(e), "details": {}}
        except Exception as e:
            # Eccezione inattesa fuori dal wrap di connect: sanitizza prima
            # di ritornare un messaggio user-facing.
            logger.warning(
                f"[GESTITO] SharePoint test_connection eccezione inattesa: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            return {
                "ok": False,
                "message": _sanitize_sharepoint_error(e),
                "details": {},
            }
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
        path = self._validated_path(path, allow_empty=True)
        try:
            doc_list = self._ctx.web.lists.get_by_title(self._library_name())
            root_folder = doc_list.root_folder
            self._ctx.load(root_folder)
            self._ctx.execute_query()

            effective_path = self._join_path(self._base_path(), path)
            if effective_path.strip("/"):
                folder = self._navigate_to(root_folder, effective_path)
            else:
                folder = root_folder

            files = folder.files
            subfolders = folder.folders
            self._ctx.load(files)
            self._ctx.load(subfolders)
            self._ctx.execute_query()
        except (DataSourceFileNotFoundError, DataSourceConnectionError):
            raise
        except Exception as e:
            logger.warning(
                f"[GESTITO] SharePoint list_files fallita: {type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(
                f"Errore list_files SharePoint: {_sanitize_sharepoint_error(e)}"
            ) from e

        results = []

        # Aggiungi cartelle (type="folder" per distinguerle
        for sf in subfolders:
            name = sf.properties.get("Name", "")
            if not name or name.startswith("_"):
                continue
            modified_str = sf.properties.get("TimeLastModified", "")
            modified_date = self._parse_dt(modified_str)
            folder_path = self._join_path(path, name)
            results.append(FileInfo(
                name=name,
                path=folder_path,
                size=0,
                modified_date=modified_date,
                type="folder",
                hash=None,
            ))
            # Ricorsione nelle sottocartelle
            if recursive:
                try:
                    sub_results = self.list_files(
                        path=folder_path,
                        pattern=pattern,
                        include_hash=include_hash,
                        recursive=True,
                    )
                    # Aggiungi solo i file (non le sottocartelle ricorsive per evitare duplicati
                    results.extend([r for r in sub_results if r.type != "folder"])
                except Exception as e:
                    # Non loggare eccezioni raw: possono contenere URL o
                    # response body autenticati.
                    logger.warning(
                        "Errore ricorsione cartella %s: %s",
                        folder_path, _sanitize_sharepoint_error(e),
                    )

        for f in files:
            name = f.properties.get("Name", "")
            if not self._matches_pattern(name, pattern):
                continue

            size = f.properties.get("Length", 0)
            try:
                size = int(size)
            except (TypeError, ValueError):
                size = 0

            modified_str = f.properties.get("TimeLastModified", "")
            modified_date = self._parse_dt(modified_str)

            file_path = self._join_path(path, name)
            hash_val = None
            if include_hash:
                try:
                    hash_val = self._compute_stream_hash(file_path)
                except Exception as e:
                    logger.debug(f"Hash computation fallita per {name!r}: {type(e).__name__}: {e}")

            results.append(FileInfo(
                name=name,
                path=file_path,
                size=size,
                modified_date=modified_date,
                type=self._extension(name),
                hash=hash_val,
            ))

        results.sort(key=lambda fi: fi.modified_date, reverse=True)
        return results

    def file_info(self, path: str) -> FileInfo:
        self._check()
        path = self._validated_path(path)
        name = path.split("/")[-1]
        folder_path = "/".join(path.split("/")[:-1])
        folder = self._get_folder(folder_path)
        if folder is None:
            raise DataSourceFileNotFoundError(f"File non trovato: {path!r}")

        try:
            files = folder.files
            self._ctx.load(files)
            self._ctx.execute_query()
        except Exception as e:
            logger.warning(
                f"[GESTITO] SharePoint file_info fallita: path={path!r} "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(
                f"Errore file_info SharePoint: {_sanitize_sharepoint_error(e)}"
            ) from e

        for f in files:
            if f.properties.get("Name") == name:
                size = int(f.properties.get("Length", 0) or 0)
                modified_date = self._parse_dt(f.properties.get("TimeLastModified", ""))
                return FileInfo(
                    name=name,
                    path=path,
                    size=size,
                    modified_date=modified_date,
                    type=self._extension(name),
                )

        raise DataSourceFileNotFoundError(f"File non trovato: {path!r}")

    def read_file(self, path: str) -> bytes:
        self._check()
        path = self._validated_path(path)
        server_relative_url = self._server_relative_url(path)
        try:
            f = self._ctx.web.get_file_by_server_relative_url(server_relative_url)
            self._ctx.load(f)
            self._ctx.execute_query()
            return self._download_file(f)
        except Exception as e:
            msg = str(e).lower()
            if "not found" in msg or "404" in msg:
                raise DataSourceFileNotFoundError(f"File non trovato: {path!r}") from e
            if "unauthorized" in msg or "403" in msg or "401" in msg:
                raise DataSourcePermissionError(f"Accesso negato: {path!r}") from e
            logger.warning(
                f"[GESTITO] SharePoint read_file fallita: path={path!r} "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(
                f"Errore read_file SharePoint: {_sanitize_sharepoint_error(e)}"
            ) from e

    # Soglia per upload a chunk (10 MB
    _CHUNK_SIZE = 10 * 1024 * 1024

    def write_file(self, path: str, content: bytes) -> None:
        self._check()
        path = self._validated_path(path)
        # Applica base_path dalla config (stessa logica di list_files
        effective_path = self._join_path(self._base_path(), path)
        parts = effective_path.split("/")
        name = parts[-1]
        folder_path = "/".join(parts[:-1])

        folder = self._get_or_create_folder(folder_path)
        if folder is None:
            raise DataSourceConnectionError(f"Impossibile creare la cartella: {folder_path!r}")

        temp_name = self._temporary_name(name)
        temp_url = self._sibling_url(self._server_relative_url(path), temp_name)
        try:
            if len(content) <= self._CHUNK_SIZE:
                # File piccolo — upload diretto
                folder.upload_file(temp_name, content)
                self._ctx.execute_query()
            else:
                # File grande — upload a chunk
                self._upload_chunked(folder, temp_name, content)
            self._promote_temp_file(temp_url, name)
        except (DataSourcePermissionError, DataSourceConnectionError):
            raise
        except Exception as e:
            msg = str(e).lower()
            if "unauthorized" in msg or "403" in msg or "401" in msg:
                raise DataSourcePermissionError(f"Scrittura negata: {path!r}") from e
            logger.warning(
                f"[GESTITO] SharePoint write_file fallita: path={path!r} "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(
                f"Errore write_file SharePoint: {_sanitize_sharepoint_error(e)}"
            ) from e
        finally:
            self._cleanup_file_url(temp_url)

    def write_chunks(self, path: str, chunks) -> None:
        """Streaming write via SharePoint resumable upload API.

        Usa un buffer di accumulo di `_CHUNK_SIZE` (10MB, soglia
        SharePoint per `start_upload`/`continue_upload`/`finish_upload`)
        e flusha ogni volta che il buffer pieno. Peak ~10MB invece di
        file_size.

        Strategia:
          1. Crea il file vuoto (handle iniziale).
          2. Accumula chunks in buffer fino a `_CHUNK_SIZE`.
          3. Al primo buffer pieno: `start_upload`.
          4. Per ogni successivo: `continue_upload(offset)`.
          5. Al finale (eof + leftover): `finish_upload(offset, leftover)`.

        Trade-off: il protocollo SharePoint richiede che ogni chunk
        flushed sia >= 1 byte e abbia un upload_id session. Per file
        <_CHUNK_SIZE accumuliamo tutto e facciamo save_binary_stream
        diretto (no upload session overhead).
        """
        self._check()
        path = self._validated_path(path)
        effective_path = self._join_path(self._base_path(), path)
        parts = effective_path.split("/")
        name = parts[-1]
        folder_path = "/".join(parts[:-1])
        folder = self._get_or_create_folder(folder_path)
        if folder is None:
            raise DataSourceConnectionError(
                f"Impossibile creare la cartella: {folder_path!r}"
            )

        chunk_size = self._CHUNK_SIZE
        upload_id = str(uuid.uuid4())
        buf = bytearray()
        offset = 0
        upload_started = False
        file_ref = None
        temp_name = self._temporary_name(name)
        temp_url = self._sibling_url(self._server_relative_url(path), temp_name)

        def _ensure_file_ref():
            nonlocal file_ref
            if file_ref is None:
                empty = folder.upload_file(temp_name, b"")
                self._ctx.execute_query()
                file_ref = self._ctx.web.get_file_by_server_relative_url(
                    empty.serverRelativeUrl
                )
                self._ctx.load(file_ref)
                self._ctx.execute_query()

        try:
            for chunk in chunks:
                if not chunk:
                    continue
                buf.extend(chunk)
                # Flusha tutti i full chunks dal buffer.
                while len(buf) >= chunk_size:
                    payload = bytes(buf[:chunk_size])
                    del buf[:chunk_size]
                    _ensure_file_ref()
                    if not upload_started:
                        file_ref.start_upload(upload_id, BytesIO(payload))
                        self._ctx.execute_query()
                        upload_started = True
                    else:
                        file_ref.continue_upload(upload_id, offset, BytesIO(payload))
                        self._ctx.execute_query()
                    offset += chunk_size

            # Leftover handling:
            leftover = bytes(buf)
            if not upload_started:
                # File piccolo: upload diretto, niente upload session.
                if leftover:
                    folder.upload_file(temp_name, leftover)
                    self._ctx.execute_query()
                else:
                    # Caso edge: iterable vuoto -> crea file vuoto.
                    folder.upload_file(temp_name, b"")
                    self._ctx.execute_query()
            else:
                # Upload session attiva: finalizza con leftover (puo'
                # essere vuoto, ma SharePoint richiede comunque la call).
                _ensure_file_ref()
                file_ref.finish_upload(upload_id, offset, BytesIO(leftover))
                self._ctx.execute_query()
            self._promote_temp_file(temp_url, name)
        except (DataSourcePermissionError, DataSourceConnectionError):
            raise
        except Exception as e:
            msg = str(e).lower()
            if "unauthorized" in msg or "403" in msg or "401" in msg:
                raise DataSourcePermissionError(f"Scrittura negata: {path!r}") from e
            # Sanitizza `{e}` per evitare leak di Bearer/AAD token nei log.
            # office365 ClientRequestException include response headers/body
            # nel str() che possono
            # contenere il token Authorization. `_sanitize_sharepoint_error`
            # ripulisce token + URL sensitive.
            logger.warning(
                f"[GESTITO] SharePoint write_chunks fallita: path={path!r} "
                f"{type(e).__name__}: {_sanitize_sharepoint_error(e)}",
            )
            raise DataSourceConnectionError(
                f"Errore write_chunks SharePoint: {_sanitize_sharepoint_error(e)}"
            ) from e
        finally:
            self._cleanup_file_url(temp_url)

    @staticmethod
    def _temporary_name(_name: str) -> str:
        return f"plenora-upload-{uuid.uuid4().hex}.part"

    @staticmethod
    def _sibling_url(file_url: str, sibling_name: str) -> str:
        return f"{file_url.rsplit('/', 1)[0]}/{sibling_name}"

    def _cleanup_file_url(self, file_url: str) -> None:
        try:
            file_ref = self._ctx.web.get_file_by_server_relative_url(file_url)
            file_ref.delete_object()
            self._ctx.execute_query()
        except Exception as e:
            msg = str(e).lower()
            if "404" not in msg and "not found" not in msg:
                logger.warning(
                    "Cleanup file temporaneo SharePoint fallito: %s",
                    _sanitize_sharepoint_error(e),
                )

    def _promote_temp_file(self, temp_url: str, target_name: str) -> None:
        """Promote a complete upload while preserving the previous target."""
        target_url = self._sibling_url(temp_url, target_name)
        backup_name = self._temporary_name(f"{target_name}.backup")
        backup_url = self._sibling_url(temp_url, backup_name)
        existing = None
        try:
            existing = self._ctx.web.get_file_by_server_relative_url(target_url)
            self._ctx.load(existing)
            self._ctx.execute_query()
        except Exception as e:
            msg = str(e).lower()
            if "404" not in msg and "not found" not in msg:
                raise
            existing = None

        if existing is not None:
            existing.rename(backup_name)
            self._ctx.execute_query()

        temp_ref = self._ctx.web.get_file_by_server_relative_url(temp_url)
        try:
            temp_ref.rename(target_name)
            self._ctx.execute_query()
        except Exception:
            if existing is not None:
                try:
                    backup_ref = self._ctx.web.get_file_by_server_relative_url(backup_url)
                    backup_ref.rename(target_name)
                    self._ctx.execute_query()
                except Exception as restore_error:
                    logger.error(
                        "SharePoint rollback replace fallito: %s",
                        _sanitize_sharepoint_error(restore_error),
                    )
            raise

        if existing is not None:
            self._cleanup_file_url(backup_url)

    def _upload_chunked(self, folder, name: str, content: bytes) -> None:
        """Upload a chunk per file grandi (>10 MB). Usa le API SharePoint resumable upload."""

        total = len(content)
        chunk_size = self._CHUNK_SIZE
        upload_id = str(uuid.uuid4())

        try:
            # 1. Crea file vuoto per ottenere il riferimento
            empty_file = folder.upload_file(name, b"")
            self._ctx.execute_query()

            file_ref = self._ctx.web.get_file_by_server_relative_url(
                empty_file.serverRelativeUrl
            )
            self._ctx.load(file_ref)
            self._ctx.execute_query()

            offset = 0
            chunk_index = 0

            while offset < total:
                chunk = content[offset: offset + chunk_size]
                is_first = offset == 0
                is_last = (offset + chunk_size) >= total

                if is_first and is_last:
                    # Un solo chunk — non serve upload resumable
                    file_ref.save_binary_stream(BytesIO(chunk))
                    self._ctx.execute_query()
                elif is_first:
                    file_ref.start_upload(upload_id, BytesIO(chunk))
                    self._ctx.execute_query()
                elif is_last:
                    file_ref.finish_upload(upload_id, offset, BytesIO(chunk))
                    self._ctx.execute_query()
                else:
                    file_ref.continue_upload(upload_id, offset, BytesIO(chunk))
                    self._ctx.execute_query()

                offset += chunk_size
                chunk_index += 1

        except Exception as e:
            # Sanitizza `{e}` per evitare leak di token AAD.
            logger.warning(
                f"[GESTITO] SharePoint upload chunked fallita: name={name!r} "
                f"{type(e).__name__}: {_sanitize_sharepoint_error(e)}",
            )
            raise DataSourceConnectionError(
                f"Errore upload chunked SharePoint: {_sanitize_sharepoint_error(e)}"
            ) from e

    def delete_file(self, path: str) -> None:
        self._check()
        path = self._validated_path(path)
        server_relative_url = self._server_relative_url(path)
        try:
            f = self._ctx.web.get_file_by_server_relative_url(server_relative_url)
            f.delete_object()
            self._ctx.execute_query()
        except Exception as e:
            msg = str(e).lower()
            if "not found" in msg or "404" in msg:
                raise DataSourceFileNotFoundError(f"File non trovato: {path!r}") from e
            if "unauthorized" in msg or "403" in msg or "401" in msg:
                raise DataSourcePermissionError(f"Cancellazione negata: {path!r}") from e
            logger.warning(
                f"[GESTITO] SharePoint delete_file fallita: path={path!r} "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(
                f"Errore delete_file SharePoint: {_sanitize_sharepoint_error(e)}"
            ) from e

    def file_exists(self, path: str) -> bool:
        self._check()
        path = self._validated_path(path)
        server_relative_url = self._server_relative_url(path)
        try:
            f = self._ctx.web.get_file_by_server_relative_url(server_relative_url)
            self._ctx.load(f)
            self._ctx.execute_query()
            return f.properties.get("Exists", True)
        except Exception as e:
            msg = str(e).lower()
            if "404" in msg or "not found" in msg:
                return False
            if "401" in msg or "403" in msg or "unauthorized" in msg or "forbidden" in msg:
                raise DataSourcePermissionError(f"Verifica accesso negata: {path!r}") from e
            raise DataSourceConnectionError(
                f"Errore file_exists SharePoint: {_sanitize_sharepoint_error(e)}"
            ) from e

    # ── Helpers privati ─────────────────────────────────────────────────────

    def _check(self) -> None:
        if self._ctx is None:
            raise DataSourceConnectionError("Non connesso. Usa il context manager 'with'.")

    def _library_name(self) -> str:
        """Primo segmento di root_path = nome della Document Library.
        es. root_path="gisrepo/test" → "gisrepo"
        """
        parts = self.config.root_path.strip("/").split("/")
        return parts[0] or "Documenti condivisi"

    def _base_path(self) -> str:
        """Sottocartella di partenza ricavata dal resto di root_path dopo la library.
        es. root_path="gisrepo/test/dati" → "test/dati"
            root_path="gisrepo" → ""
        """
        parts = self.config.root_path.strip("/").split("/")
        return "/".join(parts[1:])

    def _server_relative_url(self, relative_path: str) -> str:
        """
        Costruisce il ServerRelativeUrl partendo dal path relativo.
        Formato: /sites/NomeSito/NomeLibrary/path/al/file
        """
        extra = self.config.extra
        site_url = extra.get("site_url", "").rstrip("/")
        # Ricava il path relativo del sito dall'URL
        # es. "https://tenant.sharepoint.com/sites/workarea/gis" → "/sites/workarea/gis"
        site_path = urlparse(site_url).path.strip("/")
        library = self._library_name()
        relative_path = self._validated_path(relative_path, allow_empty=True)
        rel = self._join_path(self._base_path(), relative_path)
        parts = [p for p in [site_path, library, rel] if p]
        return "/" + "/".join(parts)

    def _navigate_to(self, root_folder, path: str):
        """Naviga nella struttura cartelle a partire da root_folder. Propaga eccezioni reali."""
        current = root_folder
        for part in path.strip("/").split("/"):
            if not part:
                continue
            subfolders = current.folders
            self._ctx.load(subfolders)
            self._ctx.execute_query()
            found = None
            for sf in subfolders:
                if sf.properties.get("Name") == part:
                    found = sf
                    break
            if found is None:
                raise DataSourceFileNotFoundError(f"Sottocartella non trovata: {part!r} nel path {path!r}")
            current = found
        return current

    def _get_folder(self, relative_path: str):
        """Restituisce l'oggetto Folder di SharePoint per il path dato. None se non esiste."""
        try:
            if not relative_path.strip("/"):
                # Root della library: usare root_folder dalla lista è più affidabile
                # di costruire un server_relative_url che varia per sito
                doc_list = self._ctx.web.lists.get_by_title(self._library_name())
                folder = doc_list.root_folder
            else:
                server_url = self._server_relative_url(relative_path)
                folder = self._ctx.web.get_folder_by_server_relative_url(server_url)
            self._ctx.load(folder)
            self._ctx.execute_query()
            return folder
        except Exception as e:
            msg = str(e).lower()
            if "404" in msg or "not found" in msg:
                return None
            if "401" in msg or "403" in msg or "unauthorized" in msg or "forbidden" in msg:
                raise DataSourcePermissionError(
                    f"Accesso cartella SharePoint negato: {relative_path!r}"
                ) from e
            raise DataSourceConnectionError(
                f"Errore accesso cartella SharePoint: {_sanitize_sharepoint_error(e)}"
            ) from e

    def _get_or_create_folder(self, relative_path: str):
        """
        Naviga/crea la struttura di cartelle.
        Replica _naviga_crea_cartella dal tuo sharepoint_uploader.py.
        """
        if not relative_path:
            # Ritorna la root della library
            return self._get_folder("")

        # Ottieni root della library
        doc_list = self._ctx.web.lists.get_by_title(self._library_name())
        root_folder = doc_list.root_folder
        self._ctx.load(root_folder)
        self._ctx.execute_query()

        current_folder = root_folder
        for part in relative_path.strip("/").split("/"):
            if not part:
                continue
            try:
                subfolders = current_folder.folders
                self._ctx.load(subfolders)
                self._ctx.execute_query()

                found = None
                for sf in subfolders:
                    if sf.properties.get("Name") == part:
                        found = sf
                        break

                if found:
                    current_folder = found
                else:
                    current_folder = current_folder.folders.add(part)
                    self._ctx.execute_query()
            except Exception as e:
                logger.debug(
                    f"Listing subfolders fallito per {part!r}, retry via folders.add: "
                    f"{type(e).__name__}: {e}"
                )
                try:
                    current_folder = current_folder.folders.add(part)
                    self._ctx.execute_query()
                except Exception as e2:
                    logger.warning(
                        f"Creazione cartella {part!r} fallita dopo retry "
                        f"(path {relative_path!r}): {type(e2).__name__}: {e2}"
                    )
                    return None

        return current_folder

    def _download_file(self, file_obj) -> bytes:
        """Scarica il contenuto di un file SharePoint come bytes."""
        buf = io.BytesIO()
        file_obj.download(buf)
        self._ctx.execute_query()
        return buf.getvalue()

    def iter_read_chunks(
        self, path: str, *, chunk_size: int = 64 * 1024
    ) -> Iterator[bytes]:
        """Streaming download via REST API + Range header.

        Bypassa office365 `File.download(buffer)` che e' full-buffer
        e usa la ClientContext authentication via la sua requests.Session
        interna per fare GET diretto a
        `/_api/Web/GetFileByServerRelativeUrl(...)/$value` con
        `stream=True` + `iter_content(chunk_size)`. Per backend
        SharePoint connesso con ClientCredential, la session interna
        di office365 SDK e' gia' autenticata; il request `stream=True`
        eredita il bearer/cookies.

        Peak RAM scende da O(file_size) a O(chunk_size) per file
        grandi.

        Degrade safe: se il request fallisce (HTTP error / auth /
        session inaccessibile), fallback al `_download_file` full
        + slicing locale — stesso peak del default ereditato ma
        compatibile con l'interface uniforme.
        """
        self._check()
        path = self._validated_path(path)
        server_relative_url = self._server_relative_url(path)
        extra = self.config.extra
        site_url = extra.get("site_url", "").rstrip("/")

        # Costruzione URL REST endpoint. `quote` per encoding
        # accentate/spazi nel path SP. `()` non viene encoded perche'
        # parte di sintassi OData.
        encoded_path = quote(server_relative_url, safe="/()")
        url = (
            f"{site_url}/_api/Web/GetFileByServerRelativeUrl('"
            f"{encoded_path}')/$value"
        )

        # Tentativo accesso a session interna office365 per riusare
        # auth. office365-python espone `pending_request()` o
        # `_request_session` a seconda della versione; usiamo
        # try/except per fragilita' API.
        session = None
        yielded_any = False
        try:
            pending = self._ctx.pending_request()
            session = pending._session if hasattr(pending, "_session") else None
        except Exception:
            pass
        if session is None:
            try:
                session = getattr(self._ctx, "_request_session", None)
            except Exception:
                session = None

        if session is None:
            # Auth session non accessibile: fallback office365 full.
            logger.debug(
                f"SharePoint iter_read_chunks: session office365 non "
                f"accessibile per {path!r}, fallback _download_file full"
            )
            yield from self._fallback_download_then_slice(path, chunk_size)
            return

        # Timeout separati: connect breve, read piu' lungo per file grandi.
        # requests accetta tuple (connect, read).
        connect_timeout = _env_float(
            "PLENORA_SHAREPOINT_CONNECT_TIMEOUT_SEC", 15.0
        )
        read_timeout = _env_float(
            "PLENORA_SHAREPOINT_STREAM_TIMEOUT_SEC", 300.0
        )
        timeout_tuple = (connect_timeout, read_timeout)
        try:
            with session.get(url, stream=True, timeout=timeout_tuple) as resp:
                if resp.status_code == 404:
                    raise DataSourceFileNotFoundError(
                        f"File non trovato: {path!r}"
                    )
                if resp.status_code in (401, 403):
                    raise DataSourcePermissionError(
                        f"Accesso negato: {path!r}"
                    )
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if chunk:
                        yielded_any = True
                        yield chunk
        except (DataSourceFileNotFoundError, DataSourcePermissionError):
            raise
        except Exception as exc:
            # Errore network / HTTP generico: fallback office365 full
            # preservando compatibilita' e loggando solo dettaglio safe.
            logger.warning(
                f"[GESTITO] SharePoint REST streaming fallito per "
                f"{path!r}, fallback _download_file full: "
                f"{type(exc).__name__}: {_sanitize_sharepoint_error(exc)}"
            )
            if yielded_any:
                raise DataSourceConnectionError(
                    "Download SharePoint interrotto dopo l'inizio dello stream"
                ) from exc
            yield from self._fallback_download_then_slice(path, chunk_size)

    def _fallback_download_then_slice(self, path: str, chunk_size: int):
        """Helper fallback: full download via office365 client + slice
        in chunks. Stesso peak del default DataSourceInterface ma
        usa il client office365 (auth/folder context). Usato quando
        il path REST diretto non e' disponibile.

        Questo fallback e' **best-effort**, NON garantisce streaming RAM.
        Il client Office365 scarica in uno ``SpooledTemporaryFile``: oltre
        8 MB il contenuto passa su disco, mantenendo il peak RAM limitato.
        Usato quando:
          - office365 session interna non e' accessibile (API SDK
            cambiata fra versioni);
          - REST stream fallisce con HTTP error / network error.

        Il vero streaming RAM resta solo nel path principale
        `iter_read_chunks` (REST + Range + iter_content)."""
        # Re-implementa la logica di read_file per ottenere file_obj
        # senza duplicare il try/except del read_file pubblico.
        server_relative_url = self._server_relative_url(path)
        try:
            file_obj = self._ctx.web.get_file_by_server_relative_url(
                server_relative_url
            )
            self._ctx.load(file_obj)
            self._ctx.execute_query()
            spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
            file_obj.download(spool)
            self._ctx.execute_query()
            spool.seek(0)
        except Exception as e:
            msg = str(e).lower()
            if "not found" in msg or "404" in msg:
                raise DataSourceFileNotFoundError(
                    f"File non trovato: {path!r}"
                ) from e
            if "unauthorized" in msg or "403" in msg or "401" in msg:
                raise DataSourcePermissionError(
                    f"Accesso negato: {path!r}"
                ) from e
            raise DataSourceConnectionError(
                f"Errore read_file SharePoint: {_sanitize_sharepoint_error(e)}"
            ) from e

        try:
            while True:
                chunk = spool.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            spool.close()

    @staticmethod
    def _parse_dt(s) -> datetime:
        """Parsa datetime SharePoint REST. Accetta sia stringa ISO che oggetto datetime."""
        if not s:
            return datetime(1970, 1, 1)
        # office365 a volte restituisce già un datetime
        if isinstance(s, datetime):
            return s
        try:
            clean = str(s).rstrip("Z").split(".")[0]
            return datetime.fromisoformat(clean)
        except (ValueError, AttributeError):
            return datetime(1970, 1, 1)
