"""Canonical file-source service boundary.

Implementation for file-source CRUD, upload orchestration, blob reads, and
preview delegation.
"""

import logging as _logging

import json
import os
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

import pandas as pd

import app.core.files.db as files_db
from app.core.auth.visibility import is_resource_visible, is_shareable
from app.core.files.load_dispatch import load_by_format
from app.core.files.multi_layer_upload import (
    create_multi_layer_from_upload as _create_multi_layer_from_upload,
)
from app.core.files.preview_temp import preview_file_temp as _preview_file_temp
from app.core.files.raw_upload import create_raw_from_upload as _create_raw_from_upload
from app.core.files.source_blob import get_blob_from_db as _read_blob_from_db
from app.core.files.source_blob import temp_file_for
from app.core.files.source_metadata import delete_file_source as _delete_file_source
from app.core.files.source_metadata import get_file_source as _read_file_source
from app.core.files.source_metadata import list_file_sources as _list_file_sources
from app.core.files.source_metadata import update_file_source as _update_file_source
from app.core.files.source_read import get_layers_info as _read_layers_info
from app.core.files.source_read import get_preview as _read_preview
from app.core.files.source_read import load_dataframe as _read_dataframe
from app.core.files.storage_dispatch import BlobDispatchResult, dispatch_blob_storage
from app.core.files.upload import mkdtemp_writable
from app.core.files.service_deps import (  # noqa: F401 (public domain export)
    LoggerProtocol,
    FileSystemProtocol,
    DataLoaderProtocol,
    TimeProviderProtocol,
    NullLogger,
    DefaultTimeProvider,
    DefaultFileSystem,
    DefaultDataLoader,
)
from app.shared.file_helpers import (  # noqa: F401 (public domain export)
    FormatDetector,
    TypeInferrer,
    FileIdGenerator,
    ColumnInfoBuilder,
)
from app.shared.resources.file_cleanup import rmtree_safe
from app.shared.storage_compensation import compensating_delete
from app.utils.zip_safe import ZipSlipError, safe_extractall

# Sopprimi warning GDAL per versioni GeoPackage più recenti della versione GDAL installata
# Non bloccante — i file vengono letti correttamente nonostante il warning
warnings.filterwarnings("ignore", message=".*GeoPackage user_version.*may only be partially supported.*")

# Logger module-level per helper senza accesso a self._logger (es. _rmtree_safe
# DefaultDataLoader): gli altri percorsi del service usano self._logger iniettato
# via LoggerProtocol. Fallback stdlib per esecuzione standalone di test
try:
    from app.utils.logger import get_logger as _get_logger
    _logger = _get_logger(__name__)
except ImportError:
    _get_logger = None
    _logger = _logging.getLogger(__name__)

# Usa i modelli canonici del dominio Files in produzione, con fallback a dataclass per test standalone

try:
    from app.core.files.models import (
        FileSource as _AppFileSource,
        FileSourceList,
    )
    from app.core.projects.canvas_models import ColumnInfo as _AppColumnInfo
    _USE_APP_MODELS = True
except ImportError:
    _USE_APP_MODELS = False


# Usate solo quando i modelli canonici non sono disponibili (test standalone)

if _USE_APP_MODELS:
    FileSource = _AppFileSource
    ColumnInfo = _AppColumnInfo
else:
    @dataclass
    class ColumnInfo:
        """Informazioni su una colonna"""
        name: str
        type: str


    @dataclass
    class FileSource:
        """Metadata di un file source"""
        id: str
        name: str
        description: str
        original_filename: str
        format: str
        layer: Optional[str]
        sheet: Optional[str]
        columns: List[ColumnInfo]
        row_count: int
        file_size: int
        crs: Optional[str]
        created_at: datetime
        updated_at: datetime

        def model_dump_json(self, indent: int = 2) -> str:
            """Serializza a JSON"""
            return json.dumps({
                "id": self.id,
                "name": self.name,
                "description": self.description,
                "original_filename": self.original_filename,
                "format": self.format,
                "layer": self.layer,
                "sheet": self.sheet,
                "columns": [{"name": c.name, "type": c.type} for c in self.columns],
                "row_count": self.row_count,
                "file_size": self.file_size,
                "crs": self.crs,
                "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat(),
            }, indent=indent)
        
        @classmethod
        def model_validate_json(cls, json_str: str) -> 'FileSource':
            """Deserializza da JSON (compatibilità con Pydantic)"""
            return cls.from_json(json_str)
        
        @classmethod
        def from_json(cls, json_str: str) -> 'FileSource':
            """Deserializza da JSON"""
            data = json.loads(json_str)
            data['columns'] = [ColumnInfo(**c) for c in data['columns']]
            data['created_at'] = datetime.fromisoformat(data['created_at'])
            data['updated_at'] = datetime.fromisoformat(data['updated_at'])
            return cls(**data)


# STRUCT-001 Seam B: i 4 Protocols (Logger/FileSystem/DataLoader/TimeProvider
# e le 4 Default impls (NullLogger/DefaultTimeProvider/DefaultFileSystem/
# DefaultDataLoader) vivono in `app.core.files.service_deps`
# Export pubblico del dominio files: test e caller istanziano il service con
# queste dipendenze esplicite, e `FileSourceService.__init__` le usa come
# fallback (`self._fs = filesystem or DefaultFileSystem`).

# Classe FileService: CRUD completo per file source con DI
# Gestisce il ciclo di vita dei file caricati dall'utente come sorgenti dati
# Usa Dependency Injection per testabilità: ogni dipendenza esterna (filesystem
# data loader, logger, time) è iniettabile con un mock nei test


# STRUCT-001 Seam D-C: la logica vive in `app.shared.resources.file_cleanup.rmtree_safe`.
# Questo adapter mantiene i call site interni semplici (`_rmtree_safe(path)`)
# iniettando il logger del modulo files.

def _rmtree_safe(path: Path | str, _retries: int = 3) -> None:
    """Delegate to `app.shared.resources.file_cleanup.rmtree_safe`."""
    rmtree_safe(path, retries=_retries, logger=_logger)

class FileSourceService:
    """
    Servizio CRUD per file source con Dependency Injection.
    
    Ogni file source è una directory con:
    - metadata.json: metadati (nome, formato, colonne, CRS, date)
    - data/<file>: file dati originale
    
    Flusso upload:
    1. Genera ID univoco (nome_timestamp_uuid)
    2. Salva file nella directory data/
    3. Se ZIP: estrai e rileva formato interno (SHP, GDB, GPKG)
    4. Rileva layer/sheet se multi-layer
    5. Carica campione dati per inferire colonne e tipi
    6. Salva metadata.json con info complete
    
    Dependency Injection per testabilità:
    - filesystem: operazioni I/O (mock per test senza filesystem reale)
    - data_loader: caricamento dati (mock per test senza GeoPandas/Fiona)
    - logger: logging (NullLogger per test silenziosi)
    - time_provider: datetime/uuid (mock per test deterministici)
    """
    
    def __init__(
        self,
        filesystem: Optional[FileSystemProtocol] = None,
        data_loader: Optional[DataLoaderProtocol] = None,
        logger: Optional[LoggerProtocol] = None,
        time_provider: Optional[TimeProviderProtocol] = None,
    ) -> None:
        self._fs = filesystem or DefaultFileSystem()
        self._loader = data_loader or DefaultDataLoader()
        self._logger = logger or NullLogger()
        self._time = time_provider or DefaultTimeProvider()

        # Pure function helpers
        self._format_detector = FormatDetector
        self._type_inferrer = TypeInferrer
        self._id_generator = FileIdGenerator
        self._column_builder = ColumnInfoBuilder

    
    def _generate_file_id(self, name: str) -> str:
        timestamp = self._time.now().strftime("%Y%m%d_%H%M%S")
        uuid_part = self._time.generate_uuid()
        return self._id_generator.generate(name, timestamp, uuid_part)
    
    
    def _detect_format(self, filename: str) -> str:
        return self._format_detector.detect(filename)
    
    # Per file multi-layer (GPKG, XLSX, SHP in directory, FileGDB
    
    def list_layers(self, file_path: Path) -> List[str]:
        """Lista layer in un file GDB o GPKG"""
        try:
            return self._loader.list_layers(file_path)
        except Exception as e:
            self._logger.error(f"[CRASH] Errore listing layers: {e}", exc_info=True)
            return []
    
    def list_sheets(self, file_path: Path) -> List[str]:
        """Lista fogli in un file Excel"""
        try:
            return self._loader.list_sheets(file_path)
        except Exception as e:
            self._logger.error(f"[CRASH] Errore listing sheets: {e}", exc_info=True)
            return []
    

    # Sentinel per bypass esplicito superadmin. L'uso di questo valore
    # scrive un log INFO (audit trail); pass-through di un group_id
    # valido e' lo scenario normale
    SUPERADMIN_BYPASS: str = "*"

    def _assert_visible(self, file_id: str, expected_group_id: str) -> None:
        """Fail-closed: verifica che `expected_group_id` abbia visibilita'
        sul file_id (owned o shared), altrimenti solleva PermissionError.

        Visibilita' calcolata via `app.core.auth.visibility.is_resource_visible`
        — stesso predicato usato da `assert_resource_visible` del layer
        auth (coerente col router).

        N1 (defense-in-depth): pre-fix, `load_dataframe` e metodi sensibili
        affidavano 100% della validazione al router. Un endpoint che
        dimentica `assert_resource_visible(...)` diventa immediatamente
        un cross-tenant data leak. Con questo helper, anche il service
        rifiuta la request se il caller non ha visibilita' dichiarata,
        rendendo la leak richiedere due bug (router + service) invece
        che uno.

        Args:
            file_id: identifier del file source.
            expected_group_id: group_id del caller. Passare
                `FileSourceService.SUPERADMIN_BYPASS` ("*") per saltare
                il check (caso superadmin), che verra' loggato.

        Raises:
            ValueError: file_id non trovato (passthrough alla semantica
                esistente che distingue 404 da 403).
            PermissionError: expected_group_id vuoto/None, o nessun
                match ownership/share.
        """
        if not expected_group_id:
            raise PermissionError(
                f"[N1 FAIL-CLOSED] expected_group_id mancante per load del "
                f"file_source {file_id}. Il service richiede il group_id "
                f"del caller per la verifica cross-tenant."
            )

        file_source = self.get_file_source(file_id)
        if not file_source:
            raise ValueError(f"File source '{file_id}' non trovato")

        if expected_group_id == self.SUPERADMIN_BYPASS:
            self._logger.info(
                f"[N1] superadmin bypass visibility su file_source {file_id}"
            )
            return

        # Ownership diretta: path hot, niente DB extra
        if file_source.group_id == expected_group_id:
            return

        # Shared via *_shares?
        try:
            if is_shareable("file_sources") and is_resource_visible(
                "file_sources",
                file_id,
                expected_group_id,
                is_superadmin=False,
                resource_owner_group_id=file_source.group_id,
            ):
                return
        except Exception as e:
            # In caso di fallimento del check shared (es. tabella share
            # non esistente o DB glitch), FAIL-CLOSED: nega accesso
            self._logger.warning(
                f"[N1] check sharing fallito per {file_id}, nego accesso: {e}"
            )

        self._logger.error(
            f"[N1 SECURITY] Cross-tenant access blocked: group "
            f"'{expected_group_id}' tenta load su file_source {file_id} "
            f"(owner: '{file_source.group_id}')"
        )
        raise PermissionError(
            f"File source '{file_id}' non accessibile al gruppo "
            f"'{expected_group_id}'"
        )

    # Carica DataFrame da file con gestione automatica formato e layer
    # La definizione canonica di `load_dataframe` e' piu' in basso
    # ~riga 1200): estrae blob dal DB in temp file, carica via parser
    # cleanup garantito con cleanup robusto (SHP/GDB necessitano rmtree
    # della temp dir parent, non solo unlink del file

    def _load_by_format(
        self,
        file_path: Path,
        format_type: str,
        layer: str = None,
        sheet: str = None
    ) -> Tuple[pd.DataFrame, Optional[Any]]:
        """Delegate to `load_dispatch.load_by_format`.

        STRUCT-001 Seam D-B: la logica vive in
        `app.core.files.load_dispatch.load_by_format` come funzione
        standalone con collaborator espliciti. I 3 call site interni
        (`self._load_by_format(...)` in upload methods + load_dataframe)
        restano invariati. Behavior byte-per-byte preservato.
        """
        return load_by_format(
            self._loader, file_path, format_type, layer, sheet,
            logger=self._logger,
        )
    
    # Upload, lista, preview, aggiornamento, eliminazione file source
    # Lettura, creazione, aggiornamento, eliminazione file source
    # Tutti i metodi usano il Protocol filesystem per I/O
    # permettendo mock completi nei test unitari
    
    # STRUCT-001 Seam C: la logica policy-aware (db/object_storage/hybrid +
    # threshold) vive in `app.core.files.storage_dispatch.dispatch_blob_storage`
    # Qui resta un thin wrapper che inietta `self._logger`. I 3 call site
    # interni (`self._dispatch_blob_storage(...)` in upload methods) restano
    # invariati. Behavior byte-per-byte preservato

    def _dispatch_blob_storage(
        self, file_id: str, raw_data: bytes, filename: str, **kwargs: Any,
    ) -> BlobDispatchResult:
        """Delegate to `storage_dispatch.dispatch_blob_storage`.

        BE-HARDEN-001C E1: `**kwargs` pass-through propaga eventuali
        parametri opzionali (es. `storage_key_id` per shared blob in
        multi-layer upload). Single-file caller chiamano con 3 positional
        args -> backward-compat 100%.

        BE-SHAPE-002: ritorna `BlobDispatchResult` TypedDict (data,
        storage_key, size_bytes). Compat 100% con i caller che usano
        `**blob_kwargs`: a runtime e' un dict.

        Vedi `app.core.files.storage_dispatch.dispatch_blob_storage`
        per la documentazione completa del policy dispatch.
        """
        return dispatch_blob_storage(
            file_id, raw_data, filename, logger=self._logger, **kwargs,
        )

    def get_blob_bytes(
        self, file_id: str, expected_group_id: str = None,
    ) -> Optional[bytes]:
        """API pubblica per ottenere il blob in-memory di un file_source.

        Wrapper documentato di `_get_blob_from_db` per i caller esterni
        (ticket attachments download, eseguibili workflow, ecc.) che
        non devono dipendere da private API.
        """
        return self._get_blob_from_db(file_id, expected_group_id)

    def _get_blob_from_db(
        self, file_id: str, expected_group_id: str = None,
    ) -> Optional[bytes]:
        """Delegate to `source_blob.get_blob_from_db`."""
        return _read_blob_from_db(
            file_id,
            expected_group_id=expected_group_id,
            assert_visible=self._assert_visible,
            superadmin_bypass=self.SUPERADMIN_BYPASS,
            logger=self._logger,
        )

    def _temp_file_for(
        self, file_id: str, expected_group_id: str = None,
    ) -> Optional[Path]:
        """Delegate to `source_blob.temp_file_for`."""
        from app.config import settings

        return temp_file_for(
            file_id,
            expected_group_id=expected_group_id,
            db_get_file_source=files_db.get_file_source,
            get_blob_from_db=self._get_blob_from_db,
            temp_dir=settings.TEMP_DIR,
            mkdtemp_writable=mkdtemp_writable,
            safe_extractall=safe_extractall,
            rmtree_safe=rmtree_safe,
            logger=self._logger,
        )

    def get_file_source(self, file_id: str) -> Optional[FileSource]:
        """Delegate to `source_metadata.get_file_source`."""
        return _read_file_source(
            file_id,
            db_get_file_source=files_db.get_file_source,
            file_source_model=FileSource,
            use_app_models=_USE_APP_MODELS,
            logger=self._logger,
        )

    def delete_file_source(self, file_id: str) -> bool:
        """Delegate to `source_metadata.delete_file_source`."""
        return _delete_file_source(
            file_id,
            db_get_file_source=files_db.get_file_source,
            db_delete_file_source=files_db.delete_file_source,
            logger=self._logger,
        )

    def list_file_sources(self, group_id: str = None) -> Any:
        """Delegate to `source_metadata.list_file_sources`."""
        file_source_list_model = FileSourceList if _USE_APP_MODELS else None
        return _list_file_sources(
            group_id,
            db_list_file_sources=files_db.list_file_sources,
            get_file_source_by_id=self.get_file_source,
            file_source_list_model=file_source_list_model,
            use_app_models=_USE_APP_MODELS,
            logger=self._logger,
        )

    def create_from_upload(
        self,
        file_content: BinaryIO,
        filename: str,
        name: str,
        description: str = "",
        layer: str = None,
        sheet: str = None,
        selected_columns: List[str] = None,
        srid: int = None,
        group_id: str = None,
    ) -> FileSource:
        """Delegate to `upload.create_from_upload`.

        STRUCT-001 Seam D-F: la logica vive in
        `app.core.files.upload.create_from_upload` con collaborator
        espliciti. Behavior byte-per-byte preservato (parse best-effort,
        orphan guard su dispatch, cleanup garantito in finally).
        """
        from app.config import settings
        from app.core.files.upload import create_from_upload as _create_from_upload
        return _create_from_upload(
            file_content, filename, name, description,
            layer, sheet, selected_columns, srid, group_id,
            generate_file_id=self._generate_file_id,
            time_now=self._time.now,
            detect_format=self._detect_format,
            list_layers=self.list_layers,
            list_sheets=self.list_sheets,
            load_by_format=self._load_by_format,
            column_builder_from_df=self._column_builder.from_dataframe,
            dispatch_blob_storage=self._dispatch_blob_storage,
            db_save_file_source=files_db.save_file_source,
            build_file_source=lambda **kw: FileSource(**kw),
            cleanup=rmtree_safe,
            safe_extractall=safe_extractall,
            zip_slip_error=ZipSlipError,
            temp_dir=settings.TEMP_DIR,
            default_group=settings.DEFAULT_GROUP,
            logger=self._logger,
        )

    def create_raw_from_upload(
        self,
        file_content: BinaryIO,
        filename: str,
        name: str,
        description: str = "",
        group_id: str = None,
    ) -> FileSource:
        """Delegate to `raw_upload.create_raw_from_upload`.

        STRUCT-001 Seam D-E: the logic lives in
        `app.core.files.raw_upload.create_raw_from_upload` with explicit
        collaborators. Behavior preserved:
        MIME validation defense-in-depth + DTO build + dispatch policy +
        DB save + logger info. Orphan guard rispettato.
        """
        from app.config import settings
        return _create_raw_from_upload(
            file_content, filename, name, description, group_id,
            generate_file_id=self._generate_file_id,
            time_now=self._time.now,
            dispatch_blob_storage=self._dispatch_blob_storage,
            db_save_file_source=files_db.save_file_source,
            default_group=settings.DEFAULT_GROUP,
            build_file_source=lambda **kw: FileSource(**kw),
            logger=self._logger,
        )

    def create_multi_layer_from_upload(
        self,
        file_content: BinaryIO,
        filename: str,
        name: str,
        description: str = "",
        layers_config: List[Dict[str, Any]] = None,
        srid: int = None,
        group_id: str = None,
    ) -> List["FileSource"]:
        """Delegate to `multi_layer_upload.create_multi_layer_from_upload`.

        STRUCT-001 Seam D-G: la logica vive in
        `app.core.files.multi_layer_upload.create_multi_layer_from_upload`
        con collaborator espliciti. Behavior byte-per-byte preservato:
        MIME defense-in-depth sempre attiva, ZIP unwrap solo `.gpkg`/`.gdb`,
        loop per-layer best-effort, atomico (E3 transactional
        all-or-nothing) con cleanup compensativo del shared storage_key
        su rollback, cleanup asimmetrico (os.unlink per file + rmtree_safe
        per dir).
        """
        from app.config import settings

        # BE-HARDEN-001C E3: callback `delete_blob` per cleanup compensativo
        # del shared storage_key se la transazione Lazy
        # binding sul cloud backend: in BYTEA mode il caller non chiamera'
        # mai questa fn (storage_key is None
        # delegato all'helper unificato
        # `app.shared.storage_compensation.compensating_delete`. L'helper
        # gestisce capability-disabled (skip silente) e delete failure
        # log strutturato, mai solleva). Il wrapper-side
        # `event=orphan_blob_cleanup_failed` log in
        # `file_multi_layer_upload` non scatta piu' (delete_blob non
        # solleva); l'evento ora canonico e'
        # `event=storage_compensating_delete_failed` con
        # `domain=file_source_multi_layer`
        def _delete_blob(storage_key: str) -> None:
            compensating_delete(
                storage_key,
                domain="file_source_multi_layer",
                reason="batch_save_rollback",
                logger=self._logger,
            )

        return _create_multi_layer_from_upload(
            file_content, filename, name, description,
            layers_config, srid, group_id,
            generate_file_id=self._generate_file_id,
            time_now=self._time.now,
            detect_format=self._detect_format,
            load_by_format=self._load_by_format,
            column_builder_from_df=self._column_builder.from_dataframe,
            dispatch_blob_storage=self._dispatch_blob_storage,
            db_save_file_sources_batch=files_db.save_file_sources_batch,
            delete_blob=_delete_blob,
            build_file_source=lambda **kw: FileSource(**kw),
            cleanup_dir=rmtree_safe,
            cleanup_file=os.unlink,    # NON rmtree_safe - preserva asimmetria
            safe_extractall=safe_extractall,
            zip_slip_error=ZipSlipError,
            temp_dir=settings.TEMP_DIR,
            default_group=settings.DEFAULT_GROUP,
            logger=self._logger,
        )

    def load_dataframe(
        self, file_id: str, layer: str = None, sheet: str = None,
        expected_group_id: str = None,
    ) -> Tuple[pd.DataFrame, Optional[Any]]:
        """Delegate to `source_read.load_dataframe`.

        N1 resta fail-closed: il modulo estratto riceve `_assert_visible`
        come collaborator esplicito.
        """
        return _read_dataframe(
            file_id,
            layer=layer,
            sheet=sheet,
            expected_group_id=expected_group_id,
            assert_visible=self._assert_visible,
            get_file_source=self.get_file_source,
            temp_file_for=self._temp_file_for,
            load_by_format=self._load_by_format,
            logger=self._logger,
        )

    def update_file_source(self, file_id: str, data: Any) -> Optional[FileSource]:
        """Delegate to `source_metadata.update_file_source`."""
        from app.config import settings
        return _update_file_source(
            file_id,
            data,
            get_file_source_by_id=self.get_file_source,
            load_dataframe=self.load_dataframe,
            column_builder_from_dataframe=self._column_builder.from_dataframe,
            get_blob_from_db=self._get_blob_from_db,
            time_now=self._time.now,
            db_update_file_source_metadata=files_db.update_file_source_metadata,
            db_get_file_source=files_db.get_file_source,
            db_save_file_source=files_db.save_file_source,
            default_group=settings.DEFAULT_GROUP,
            use_app_models=_USE_APP_MODELS,
            logger=self._logger,
        )

    def get_layers_info(
        self, file_id: str, expected_group_id: str = None,
    ) -> Dict[str, Any]:
        """Delegate to `source_read.get_layers_info`.

        N1: `expected_group_id` obbligatorio per fail-closed check.
        """
        return _read_layers_info(
            file_id,
            expected_group_id=expected_group_id,
            assert_visible=self._assert_visible,
            get_file_source=self.get_file_source,
            temp_file_for=self._temp_file_for,
            list_layers=self.list_layers,
            list_sheets=self.list_sheets,
            logger=self._logger,
        )

    def get_preview(
        self, file_id: str, limit: int = 100, expected_group_id: str = None,
    ) -> Dict[str, Any]:
        """Delegate to `source_read.get_preview`.

        N1: `expected_group_id` obbligatorio per fail-closed check.
        """
        return _read_preview(
            file_id,
            limit=limit,
            expected_group_id=expected_group_id,
            assert_visible=self._assert_visible,
            get_file_source=self.get_file_source,
            load_dataframe_for_preview=(
                lambda fid, group_id: self.load_dataframe(
                    fid, expected_group_id=group_id,
                )
            ),
        )

    def preview_file_temp(self, file_content: BinaryIO, filename: str, layer: str = None, sheet: str = None) -> dict:
        """Delegate to `preview_temp.preview_file_temp`.

        STRUCT-001 Seam D-A: la logica vive in
        `app.core.files.preview_temp.preview_file_temp` come
        funzione standalone portable (collaborator espliciti via kwargs,
        no DB, no auth). Qui passiamo i collaborator del service.
        """
        return _preview_file_temp(
            file_content,
            filename,
            fs=self._fs,
            loader=self._loader,
            format_detector=self._format_detector,
            column_builder=self._column_builder,
            logger=self._logger,
            layer=layer,
            sheet=sheet,
        )




# Istanza globale per uso da router FastAPI (evita ri-creazione ad ogni richiesta
# Per test, usare create_file_service con mock iniettati
def _create_production_service() -> FileSourceService:
    """Crea service con configurazione di produzione"""
    if _get_logger is not None:
        logger = _get_logger(__name__)
        return FileSourceService(logger=logger)
    # Fallback per test standalone
    return FileSourceService()


# Singleton globale per uso da router FastAPI
file_source_service: FileSourceService = _create_production_service()
