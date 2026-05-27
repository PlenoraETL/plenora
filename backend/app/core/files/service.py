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

import app.core.files.db as files_db
from app.core.auth.visibility import is_resource_visible, is_shareable
from app.core.files import load_dispatch as _load_dispatch
from app.core.files import multi_layer_upload as _multi_layer_upload
from app.core.files.preview_temp import preview_file_temp as _preview_file_temp
from app.core.files import raw_upload as _raw_upload
from app.core.files import source_blob as _source_blob
from app.core.files import source_metadata as _source_metadata
from app.core.files import source_read as _source_read
from app.core.files.source_read import FileSourceLayersPayload, FileSourcePreviewPayload
from app.core.files.preview_temp import FilePreviewTempPayload
from app.core.files.storage_dispatch import BlobDispatchResult, dispatch_blob_storage
from typing import Protocol

from app.core.files.service_deps import (  # noqa: F401 (public domain export)
    LoggerProtocol,
    DataLoaderProtocol,
    TimeProviderProtocol,
    NullLogger,
    DefaultTimeProvider,
    DefaultDataLoader,
)
from app.core.files.helpers import (  # noqa: F401 (public domain export)
    FormatDetector,
    TypeInferrer,
    FileIdGenerator,
    ColumnInfoBuilder,
)
from app.core.io.local_cleanup import rmtree_safe
from app.infrastructure.storage import compensation as storage_compensation

# Non-blocking GDAL compatibility warning for newer GeoPackage user_version.
warnings.filterwarnings("ignore", message=".*GeoPackage user_version.*may only be partially supported.*")


class FileSystemProtocol(Protocol):
    """Legacy constructor marker; no runtime filesystem methods are used."""

# Module-level fallback for helpers constructed before FileSourceService exists.
try:
    from app.utils.logger import get_logger as _get_logger
    _logger = _get_logger(__name__)
except ImportError:
    _get_logger = None
    _logger = _logging.getLogger(__name__)

try:
    from app.core.files.models import (
        FileSource as _AppFileSource,
        FileSourceList,
    )
    from app.core.projects.canvas_models import ColumnInfo as _AppColumnInfo
    _USE_APP_MODELS = True
except ImportError:
    _USE_APP_MODELS = False


if _USE_APP_MODELS:
    FileSource = _AppFileSource
    ColumnInfo = _AppColumnInfo
else:
    @dataclass
    class ColumnInfo:
        name: str
        type: str


    @dataclass
    class FileSource:
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
            return cls.from_json(json_str)
        
        @classmethod
        def from_json(cls, json_str: str) -> 'FileSource':
            data = json.loads(json_str)
            data['columns'] = [ColumnInfo(**c) for c in data['columns']]
            data['created_at'] = datetime.fromisoformat(data['created_at'])
            data['updated_at'] = datetime.fromisoformat(data['updated_at'])
            return cls(**data)


def _rmtree_safe(path: Path | str, _retries: int = 3) -> None:
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
    - data_loader: caricamento dati (mock per test senza GeoPandas/Pyogrio)
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
        # `filesystem` kwarg + `self._fs` rimossi (2026-05-26): erano
        # legacy DI passati a `_preview_file_temp(fs=...)` che li
        # ignorava esplicitamente. DefaultFileSystem tombstonato.
        self._fs = filesystem
        self._loader = data_loader or DefaultDataLoader()
        self._logger = logger or NullLogger()
        self._time = time_provider or DefaultTimeProvider()

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

    def _load_by_format(
        self,
        file_path: Path,
        format_type: str,
        layer: str = None,
        sheet: str = None
    ) -> Tuple[Any, Optional[Any]]:
        return _load_dispatch.load_by_format(
            self._loader, file_path, format_type, layer, sheet,
            logger=self._logger,
        )
    
    def _dispatch_blob_storage(
        self, file_id: str, raw_data: bytes, filename: str, **kwargs: Any,
    ) -> BlobDispatchResult:
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
        return _source_blob.get_blob_from_db(
            file_id,
            expected_group_id=expected_group_id,
            assert_visible=self._assert_visible,
            superadmin_bypass=self.SUPERADMIN_BYPASS,
            logger=self._logger,
        )

    def get_file_source(self, file_id: str) -> Optional[FileSource]:
        return _source_metadata.get_file_source(
            file_id,
            db_get_file_source=files_db.get_file_source,
            file_source_model=FileSource,
            use_app_models=_USE_APP_MODELS,
            logger=self._logger,
        )

    def delete_file_source(self, file_id: str) -> bool:
        return _source_metadata.delete_file_source(
            file_id,
            db_get_file_source=files_db.get_file_source,
            db_delete_file_source=files_db.delete_file_source,
            logger=self._logger,
        )

    def list_file_sources(self, group_id: str = None) -> Any:
        file_source_list_model = FileSourceList if _USE_APP_MODELS else None
        return _source_metadata.list_file_sources(
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
        from app.config import settings
        from app.core.files.upload import create_from_upload as _create_from_upload
        return _create_from_upload(
            file_content, filename, name, description,
            layer, sheet, selected_columns, srid, group_id,
            generate_file_id=self._generate_file_id,
            time_now=self._time.now,
            detect_format=self._detect_format,
            column_builder_from_df=self._column_builder.from_dataframe,
            dispatch_blob_storage=self._dispatch_blob_storage,
            db_save_file_source=files_db.save_file_source,
            build_file_source=lambda **kw: FileSource(**kw),
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
        from app.config import settings
        return _raw_upload.create_raw_from_upload(
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
        from app.config import settings

        # Object-storage rollback cleanup is best-effort; BYTEA mode has no
        # storage_key and never calls this callback.
        def _delete_blob(storage_key: str) -> None:
            storage_compensation.compensating_delete(
                storage_key,
                domain="file_source_multi_layer",
                reason="batch_save_rollback",
                logger=self._logger,
            )

        return _multi_layer_upload.create_multi_layer_from_upload(
            file_content, filename, name, description,
            layers_config, srid, group_id,
            generate_file_id=self._generate_file_id,
            time_now=self._time.now,
            detect_format=self._detect_format,
            column_builder_from_df=self._column_builder.from_dataframe,
            dispatch_blob_storage=self._dispatch_blob_storage,
            db_save_file_sources_batch=files_db.save_file_sources_batch,
            delete_blob=_delete_blob,
            build_file_source=lambda **kw: FileSource(**kw),
            default_group=settings.DEFAULT_GROUP,
            logger=self._logger,
        )

    def load_dataframe(
        self, file_id: str, layer: str = None, sheet: str = None,
        expected_group_id: str = None,
    ) -> Tuple[Any, Optional[Any]]:
        """Delegate to `source_read.load_dataframe`.

        N1 resta fail-closed: il modulo estratto riceve `_assert_visible`
        come collaborator esplicito.
        """
        return _source_read.load_dataframe(
            file_id,
            layer=layer,
            sheet=sheet,
            expected_group_id=expected_group_id,
            assert_visible=self._assert_visible,
            get_file_source=self.get_file_source,
            get_blob=self._get_blob_from_db,
            logger=self._logger,
        )

    def update_file_source(self, file_id: str, data: Any) -> Optional[FileSource]:
        from app.config import settings
        return _source_metadata.update_file_source(
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
    ) -> FileSourceLayersPayload:
        """Delegate to `source_read.get_layers_info`.

        N1: `expected_group_id` obbligatorio per fail-closed check.
        """
        return _source_read.get_layers_info(
            file_id,
            expected_group_id=expected_group_id,
            assert_visible=self._assert_visible,
            get_file_source=self.get_file_source,
            get_blob=self._get_blob_from_db,
            logger=self._logger,
        )

    def get_preview(
        self, file_id: str, limit: int = 100, expected_group_id: str = None,
    ) -> FileSourcePreviewPayload:
        """Delegate to `source_read.get_preview`.

        N1: `expected_group_id` obbligatorio per fail-closed check.
        """
        return _source_read.get_preview(
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

    def preview_file_temp(
        self,
        file_content: BinaryIO,
        filename: str,
        layer: str = None,
        sheet: str = None,
    ) -> FilePreviewTempPayload:
        return _preview_file_temp(
            file_content,
            filename,
            loader=self._loader,
            format_detector=self._format_detector,
            column_builder=self._column_builder,
                    logger=getattr(self, "_logger", NullLogger()),
            layer=layer,
            sheet=sheet,
        )




def _create_production_service() -> FileSourceService:
    if _get_logger is not None:
        logger = _get_logger(__name__)
        return FileSourceService(logger=logger)
    return FileSourceService()


def create_file_service() -> FileSourceService:
    return FileSourceService()


file_source_service: FileSourceService = _create_production_service()
