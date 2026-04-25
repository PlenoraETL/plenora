"""
PLENORA-ETL — Data Source Backend: SharePoint Online
Usa la libreria Office365-REST-Python-Client (stessa usata in sharepoint_uploader.py)
che gestisce correttamente l'autenticazione e i certificati SSL su Windows/Anaconda.

Dipendenza: pip install Office365-REST-Python-Client

Configurazione extra attesa in config.extra:
    site_url (str)      es. "https://tenant.sharepoint.com/sites/NomeSito"
    client_id (str)     Application (client) ID
    client_secret (str) Client secret
    root_path (str)     Nome della Document Library, es. "Documenti" o "GIS"
                        (corrisponde al list_name in sharepoint_uploader.py)
"""

import io
from datetime import datetime
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceInterface,
    DataSourceConnectionError,
    DataSourceFileNotFoundError,
    DataSourcePermissionError,
    FileInfo,
)


def _sanitize_sharepoint_error(e: Exception) -> str:
    """F5-B4 (2026-04-24): mappa eccezioni Office365 / msal / requests /
    socket a messaggi canonici user-facing, SENZA esporre str(e) che puo'
    contenere site_url autenticati, tenant_id, client_id, Bearer token,
    AADSTS error codes, HTTP response body con cookie/trace. Il dettaglio
    tecnico resta nei log server-side (logger.warning con exc_info=True).

    Priorita': AADSTS/OAuth codes -> HTTP status -> network/TLS -> fallback.
    Messaggi canonici italiani coerenti con FTP/SFTP/MSSQL (F5-B2/B3).
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

        try:
            credentials = ClientCredential(client_id, client_secret)
            ctx = ClientContext(site_url).with_credentials(credentials)
            if tenant_id:
                ctx.authentication_context.tenant = tenant_id

            # Test connessione (stesso pattern del tuo uploader)
            web = ctx.web
            ctx.load(web)
            ctx.execute_query()

            self._ctx = ctx

        except Exception as e:
            logger.warning(
                f"[GESTITO] SharePoint auth fallita: {type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(_sanitize_sharepoint_error(e)) from e

    def disconnect(self) -> None:
        self._ctx = None

    def test_connection(self) -> dict:
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
            # F5-B4: `e` e' il nostro wrapper con messaggio gia' canonico
            # (vedi connect() + _sanitize_sharepoint_error), quindi str(e)
            # e' safe.
            logger.warning(
                f"[GESTITO] SharePoint test_connection fallita: {type(e).__name__}: {e} "
                f"(cause: {type(e.__cause__).__name__ if e.__cause__ else 'none'})",
                exc_info=True,
            )
            return {"ok": False, "message": str(e), "details": {}}
        except Exception as e:
            # F5-B4: eccezione inattesa fuori dal wrap di connect() —
            # sanitize via helper invece di f"Errore inatteso: {e}".
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

        # Aggiungi cartelle (type="folder" per distinguerle)
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
                    # Aggiungi solo i file (non le sottocartelle ricorsive per evitare duplicati)
                    results.extend([r for r in sub_results if r.type != "folder"])
                except Exception as e:
                    logger.warning(f"Errore ricorsione cartella {folder_path}: {e}")

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
                    content = self._download_file(f)
                    hash_val = self._compute_hash(content)
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

    # Soglia per upload a chunk (10 MB)
    _CHUNK_SIZE = 10 * 1024 * 1024

    def write_file(self, path: str, content: bytes) -> None:
        self._check()
        # Applica base_path dalla config (stessa logica di list_files)
        effective_path = self._join_path(self._base_path(), path)
        parts = effective_path.split("/")
        name = parts[-1]
        folder_path = "/".join(parts[:-1])

        folder = self._get_or_create_folder(folder_path)
        if folder is None:
            raise DataSourceConnectionError(f"Impossibile creare la cartella: {folder_path!r}")

        try:
            if len(content) <= self._CHUNK_SIZE:
                # File piccolo — upload diretto
                folder.upload_file(name, content)
                self._ctx.execute_query()
            else:
                # File grande — upload a chunk
                self._upload_chunked(folder, name, content)
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

    def _upload_chunked(self, folder, name: str, content: bytes) -> None:
        """Upload a chunk per file grandi (>10 MB). Usa le API SharePoint resumable upload."""
        import uuid
        from io import BytesIO

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
            logger.warning(
                f"[GESTITO] SharePoint upload chunked fallita: name={name!r} "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise DataSourceConnectionError(
                f"Errore upload chunked SharePoint: {_sanitize_sharepoint_error(e)}"
            ) from e

    def delete_file(self, path: str) -> None:
        self._check()
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
        server_relative_url = self._server_relative_url(path)
        try:
            f = self._ctx.web.get_file_by_server_relative_url(server_relative_url)
            self._ctx.load(f)
            self._ctx.execute_query()
            return f.properties.get("Exists", True)
        except Exception as e:
            logger.debug(f"file_exists probe fallito per {path!r}: {type(e).__name__}: {e}")
            return False

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
            root_path="gisrepo"           → ""
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
        from urllib.parse import urlparse
        site_path = urlparse(site_url).path.rstrip("/")
        library = self._library_name()
        rel = relative_path.strip("/")
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
            logger.debug(f"_get_folder fallito per {relative_path!r}: {type(e).__name__}: {e}")
            return None

    def _get_or_create_folder(self, relative_path: str):
        """
        Naviga/crea la struttura di cartelle.
        Replica _naviga_crea_cartella() dal tuo sharepoint_uploader.py.
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