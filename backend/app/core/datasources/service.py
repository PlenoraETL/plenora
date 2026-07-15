"""Data source configuration service and runtime file operations."""

import uuid
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone
from typing import Mapping, Optional, TypeAlias

from app.config import settings
from app.utils.logger import get_logger
from app.infrastructure.security.crypto import (
    encrypt_dict_fields as _encrypt_dict_fields,
    decrypt_dict_fields as _decrypt_dict_fields,
)
from app.core.datasources.models import (
    DataSource,
    DataSourceCreate,
    DataSourceUpdate,
    DataSourceList,
    DataSourceTestResult,
    FileInfoResponse,
    ScanState,
)
from app.core.datasources.factory import create_backend
from app.core.datasources.interface import DataSourceInterface
from app.core.datasources import db as datasource_db
from app.core.files.preview_temp import preview_file_temp
from app.core.files.helpers import ColumnInfoBuilder, FormatDetector

logger = get_logger(__name__)

# Keep redaction and at-rest encryption field lists in sync.
_SENSITIVE_FIELDS = ["password", "private_key", "private_key_passphrase", "client_secret"]
DataSourceRuntimeConfig: TypeAlias = dict[str, object]


class DataSourceService:
    """
    CRUD + operazioni file per i Data Source.
    Configurazioni e scan_state runtime nel DB (PostgreSQL).
    """

    def list_datasources(
        self,
        group_id: Optional[str] = None,
        resource_ids: set[str] | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> DataSourceList:
        rows = datasource_db.list_datasources(
            group_id,
            resource_ids=resource_ids,
            limit=limit,
            offset=offset,
        )
        result = []
        for row in rows:
            config = self._row_to_config(row)
            if config:
                result.append(self._to_model(config, redact=True))
        return DataSourceList(datasources=result)

    def _row_to_config(
        self,
        row: datasource_db.DataSourceRow,
        *,
        strict: bool = False,
    ) -> Optional[DataSourceRuntimeConfig]:
        """Estrae il config dict da una row già fetchata (no re-query).

        Equivalente in-memory di _get_full_config ma senza re-fetch dal DB.
        Usato da list_datasources per evitare N+1 query.
        """
        if not row:
            return None
        config = dict(row.config or {})
        if not isinstance(config, dict):
            return None
        _decrypt_dict_fields(config, _SENSITIVE_FIELDS, strict=strict)
        config["group_id"] = row.group_id
        config["created_by"] = row.created_by
        return config

    def get_datasource(self, ds_id: str) -> Optional[DataSource]:
        config = self._get_full_config(ds_id)
        if not config:
            return None
        return self._to_model(config, redact=True)

    def create_datasource(
        self,
        data: DataSourceCreate,
        group_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> DataSource:
        ds_id = self._generate_id(data.name)
        now = datetime.now(timezone.utc)

        config = {
            "id": ds_id,
            "name": data.name,
            "description": data.description,
            "type": data.type,
            "root_path": data.root_path,
            "host": data.host,
            "port": data.port,
            "username": data.username,
            "password": data.password,
            "private_key": data.private_key,
            "private_key_passphrase": data.private_key_passphrase,
            "host_key_policy": data.host_key_policy,
            "tls": data.tls,
            "passive": data.passive,
            "tenant_id": data.tenant_id,
            "client_id": data.client_id,
            "client_secret": data.client_secret,
            "site_id": data.site_id,
            "site_url": data.site_url,
            "drive_id": data.drive_id,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        config = {k: v for k, v in config.items() if v is not None}

        # Preserve plaintext in-memory for response redaction; only the DB copy is encrypted.
        config_to_save = dict(config)
        _encrypt_dict_fields(config_to_save, _SENSITIVE_FIELDS)

        datasource_db.save_datasource(
            ds_id=ds_id,
            group_id=group_id or settings.DEFAULT_GROUP,
            name=data.name,
            ds_type=data.type,
            config=config_to_save,
            created_by=created_by,
        )

        config["group_id"] = group_id or settings.DEFAULT_GROUP
        config["created_by"] = created_by

        logger.info(f"Data Source creato: {ds_id} (tipo={data.type})")
        return self._to_model(config, redact=True)

    def update_datasource(self, ds_id: str, data: DataSourceUpdate) -> Optional[DataSource]:
        config = self._get_full_config(ds_id)
        if not config:
            return None

        updates = data.model_dump(exclude_none=True)
        # The client sends "***" for unchanged secrets; never persist that mask.
        for sf in _SENSITIVE_FIELDS:
            if sf in updates and updates[sf] == "***":
                del updates[sf]
        config.update(updates)
        config["updated_at"] = datetime.now(timezone.utc).isoformat()

        # DB-owned columns must not be duplicated inside the encrypted JSON config.
        config_to_save = dict(config)
        config_to_save.pop("group_id", None)
        config_to_save.pop("created_by", None)
        _encrypt_dict_fields(config_to_save, _SENSITIVE_FIELDS)
        datasource_db.update_datasource(ds_id, config=config_to_save)
        logger.info(f"Data Source aggiornato: {ds_id}")
        return self._to_model(config, redact=True)

    def delete_datasource(self, ds_id: str) -> bool:
        if not datasource_db.get_datasource(ds_id):
            return False
        datasource_db.delete_datasource(ds_id)
        logger.info(f"Data Source eliminato: {ds_id}")
        return True

    def test_connection(self, ds_id: str) -> DataSourceTestResult:
        config = self._get_full_config(ds_id, strict=True)
        if not config:
            return DataSourceTestResult(
                success=False,
                message=f"Data Source '{ds_id}' non trovato",
            )
        backend = None
        try:
            backend = create_backend(config)
            result = backend.test_connection()
            return DataSourceTestResult(
                success=result.get("ok", False),
                message=result.get("message", ""),
                details=result.get("details"),
            )
        # Provider exceptions can include URLs, tokens, credentials, or
        # internal paths. Logghiamo solo il tipo, con traceback server-side.
        except Exception as e:
            logger.error(
                "[GESTITO] Errore test connessione %s: %s",
                ds_id, type(e).__name__,
                exc_info=True,
            )
            return DataSourceTestResult(
                success=False,
                message="Impossibile testare la connessione: riprova piu' tardi",
            )
        finally:
            # Cleanup best-effort per backend stateful socket
            # (FTP/SFTP/SharePoint).
            if backend is not None and hasattr(backend, "disconnect"):
                try:
                    backend.disconnect()
                except Exception as cleanup_err:
                    logger.warning(
                        "[GESTITO] disconnect post-test fallito per %s: %s",
                        ds_id, type(cleanup_err).__name__,
                    )

    def list_files(
        self,
        ds_id: str,
        path: str = "",
        pattern: Optional[str] = None,
        include_hash: bool = False,
        recursive: bool = False,
    ) -> list[FileInfoResponse]:
        backend = self._get_backend(ds_id)
        with backend as ds:
            files = ds.list_files(path=path, pattern=pattern, include_hash=include_hash, recursive=recursive)
        return [
            FileInfoResponse(
                name=datasource_file.name,
                path=datasource_file.path,
                size=datasource_file.size,
                modified_date=datasource_file.modified_date,
                type=datasource_file.type,
                hash=datasource_file.hash,
            )
            for datasource_file in files
        ]

    def file_info(self, ds_id: str, path: str) -> FileInfoResponse:
        backend = self._get_backend(ds_id)
        with backend as ds:
            datasource_file = ds.file_info(path)
        return FileInfoResponse(
            name=datasource_file.name,
            path=datasource_file.path,
            size=datasource_file.size,
            modified_date=datasource_file.modified_date,
            type=datasource_file.type,
            hash=datasource_file.hash,
        )

    def read_file(self, ds_id: str, path: str) -> bytes:
        backend = self._get_backend(ds_id)
        with backend as ds:
            return ds.read_file(path)

    def get_file_schema(
        self,
        ds_id: str,
        path: str,
        *,
        layer: Optional[str] = None,
        sheet: Optional[str] = None,
    ) -> Mapping[str, object]:
        """Inferisce colonne/formato di un file esposto da un Data Source.

        Il datasource resta la boundary di lettura remota; il parsing riusa
        il parser temporaneo del dominio Files, senza persistere FileSource.

        Bounded read via `iter_read_chunks` con cap
        `PLENORA_DATASOURCE_SCHEMA_MAX_BYTES` (default 50MB). Per
        LocalDataSource/SFTP/FTP il peak scende a max_bytes. Per backend con
        fallback full-read, il cap limita solo i bytes consumati.

        Caveat per format ZIP/binari (XLSX/Parquet/GPKG/SHP-ZIP):
        questi formati richiedono il footer/index del file completo.
        Il cap 50MB e' sufficiente per CSV/JSON (header bytes
        bastano per detection + parse). Per formati binari il cap puo'
        far fallire il parser su file molto grandi — l'utente deve
        sapere che la preview ha un limite di scope.
        """
        # Override env opzionale; valori non validi o <=0 ricadono sul default.
        _default_max = 50 * 1024 * 1024
        try:
            max_bytes = int(
                settings.get("PLENORA_DATASOURCE_SCHEMA_MAX_BYTES", _default_max)
            )
            if max_bytes <= 0:
                max_bytes = _default_max
        except (TypeError, ValueError):
            max_bytes = _default_max

        buf = bytearray()
        for chunk in self.iter_read_chunks(ds_id, path):
            buf.extend(chunk)
            if len(buf) >= max_bytes:
                break
        content = bytes(buf)

        filename = Path(path).name or "datasource_file"
        return preview_file_temp(
            BytesIO(content),
            filename,
            format_detector=FormatDetector,
            column_builder=ColumnInfoBuilder,
            logger=logger,
            layer=layer,
            sheet=sheet,
        )

    def iter_read_chunks(self, ds_id: str, path: str, *, chunk_size: int = 64 * 1024):
        """Streaming read via backend interface.

        Per `LocalDataSource` apre il file e itera `chunk_size` byte
        per volta (peak ~chunk_size). Per gli altri backend usa il
        default fallback (read_file + slicing — peak invariato ma
        compatibile con `StreamingResponse`).

        NB: il backend `__exit__` viene chiamato quando l'iterator
        viene consumato — il caller DEVE consumare lo stream all'interno
        del `with` per evitare leak di connection (SFTP/FTP).
        """
        backend = self._get_backend(ds_id)
        with backend as ds:
            yield from ds.iter_read_chunks(path, chunk_size=chunk_size)

    def write_file(self, ds_id: str, path: str, content: bytes) -> None:
        backend = self._get_backend(ds_id)
        with backend as ds:
            ds.write_file(path, content)

    def write_chunks(self, ds_id: str, path: str, chunks) -> None:
        """Streaming write via backend interface.

        I backend con override nativo (Local, SFTP, FTP) scrivono
        chunk-per-chunk con peak ~chunk_size. SharePoint usa il default
        fallback (accumula in bytearray → write_file).
        """
        backend = self._get_backend(ds_id)
        with backend as ds:
            ds.write_chunks(path, chunks)

    def delete_file(self, ds_id: str, path: str) -> None:
        backend = self._get_backend(ds_id)
        with backend as ds:
            ds.delete_file(path)

    def file_exists(self, ds_id: str, path: str) -> bool:
        backend = self._get_backend(ds_id)
        with backend as ds:
            return ds.file_exists(path)

    def get_scan_state(self, ds_id: str) -> ScanState:
        row = datasource_db.get_scan_state(ds_id)
        if row and row.scan_state is not None:
            try:
                return ScanState(**row.scan_state)
            except Exception as e:
                logger.error(
                    f"[GESTITO] Scan state DB invalido per {ds_id}: {e}. "
                    "Scan riparte da zero.",
                    op="get_scan_state",
                )
                return ScanState()

        return ScanState()

    def save_scan_state(self, ds_id: str, state: ScanState) -> None:
        saved = datasource_db.save_scan_state(
            ds_id,
            state.model_dump(mode="json"),
        )
        if not saved:
            logger.warning(
                "Scan state non salvato: datasource %s non trovato nel DB",
                ds_id,
            )

    def mark_as_processed(self, ds_id: str, filename: str, pipeline_id: str) -> None:
        from app.core.datasources.models import ProcessedEntry, SnapshotEntry
        state = self.get_scan_state(ds_id)
        now = datetime.now(timezone.utc)
        state.last_check = now

        processed_names = {p.name for p in state.processed}
        snapshot_names = {s.name for s in state.snapshot}
        if filename not in processed_names:
            state.processed.append(ProcessedEntry(
                name=filename, processed_at=now, pipeline_id=pipeline_id,
            ))
        # Snapshot metadata is advisory; processing status must still be recorded.
        try:
            snapshot_file_info = self.file_info(ds_id, filename)
            if filename not in snapshot_names:
                state.snapshot.append(SnapshotEntry(
                    name=filename,
                    size=snapshot_file_info.size,
                    modified=snapshot_file_info.modified_date.isoformat(),
                ))
        except Exception as e:
            logger.debug(
                "mark_as_processed: snapshot entry skipped per %s/%s: %s: %s",
                ds_id, filename, type(e).__name__, e,
            )

        self.save_scan_state(ds_id, state)
        logger.info(f"File processato: {filename} (ds={ds_id}, pipeline={pipeline_id})")

    def _get_full_config(
        self,
        ds_id: str,
        *,
        strict: bool = False,
    ) -> Optional[DataSourceRuntimeConfig]:
        """Recupera la config completa (con credenziali) dal DB.

        I campi sensibili (password, private_key, ...) sono cifrati at-rest
        e vengono decifrati qui prima di essere restituiti al caller. Tutti
        gli usi interni (test_connection, get_backend, update_datasource)
        ricevono quindi i valori in chiaro pronti all'uso.

        Su config corrotto in DB, ritorna None (datasource trattata come non
        trovata) loggando warning, invece di sollevare 500.

        Se ``strict=True``, il decrypt di un campo cifrato che torna None
        (chiave crypto mancante/sbagliata o token Fernet corrotto) solleva
        ``CredentialUnavailableError``. Usato dal path test_connection per non
        rispondere 200-OK con secret silent-None.
        """
        row = datasource_db.get_datasource(ds_id)
        if not row:
            return None
        config = self._row_to_config(row, strict=strict)
        if config is None:
            logger.warning(
                f"Config con tipo inatteso per datasource {ds_id}: "
                f"{type(row.config).__name__}. Datasource trattata come non trovata."
            )
        return config

    def _get_backend(self, ds_id: str) -> DataSourceInterface:
        config = self._get_full_config(ds_id)
        if not config:
            from app.core.datasources.interface import DataSourceError
            raise DataSourceError(f"Data Source '{ds_id}' non trovato")
        return create_backend(config)

    def _generate_id(self, name: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_")).strip()
        safe_name = safe_name.replace(" ", "_")[:30]
        return f"ds_{safe_name}_{timestamp}_{short_uuid}"

    def _to_model(self, data: dict[str, object], redact: bool = True) -> DataSource:
        datasource_data = data.copy()
        if redact:
            for field in _SENSITIVE_FIELDS:
                if datasource_data.get(field):
                    datasource_data[field] = "***"
        for ts_field in ("created_at", "updated_at"):
            if isinstance(datasource_data.get(ts_field), str):
                try:
                    datasource_data[ts_field] = datetime.fromisoformat(datasource_data[ts_field])
                except ValueError:
                    datasource_data[ts_field] = datetime.now(timezone.utc)
        return DataSource(**{
            key: value
            for key, value in datasource_data.items()
            if key in DataSource.model_fields
        })


datasource_service = DataSourceService()
