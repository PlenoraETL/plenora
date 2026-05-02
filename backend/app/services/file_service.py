"""
Servizio CRUD per File Source (sorgenti dati da file caricati).

Architettura
============
Gestisce il ciclo di vita dei file sources caricati dall'utente.
Tutto lo storage (blob binario + metadata JSON) vive nel DB
(`plenora.file_sources` table), niente persistenza su filesystem.
Il filesystem viene usato solo per file **temporanei** durante il
processing (fiona/GDAL lavorano con path fisici) e vengono eliminati
al termine dell'operazione.

Dependency Injection per testabilità:
- FileSystemProtocol: operazioni temp file (mock per test senza I/O reale)
- DataLoaderProtocol: caricamento dati (mock per test senza GeoPandas)
- LoggerProtocol: logging (NullLogger per test silenziosi)
- TimeProvider: datetime/uuid (mock per test deterministici e riproducibili)

Formati supportati:
- Tabulari: CSV, XLSX (Excel), JSON
- Geospaziali: GeoJSON, GeoPackage (.gpkg), Shapefile (.shp/.zip), FileGDB
- Raw binari: DXF, altri formati non parsabili (salvati solo come blob)

Operazioni principali:
- create_from_upload(): parsing + infer colonne/layers, blob DB
- create_raw_from_upload(): salva blob DB senza parsing (fallback per
  formati non supportati dal parser)
- load_dataframe(): estrae blob da DB, scrive in temp, legge via parser
- get_preview(): prime N righe per anteprima UI
- get_layers_info(): layers/sheets di file multi-layer
- update_file_source(): aggiorna metadati
- delete_file_source(): rimuove record DB
"""

import json
import os
import re
import warnings
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple, BinaryIO, Dict, Any, Callable
from dataclasses import dataclass, field
import pandas as pd

# Sopprimi warning GDAL per versioni GeoPackage più recenti della versione GDAL installata
# Non bloccante — i file vengono letti correttamente nonostante il warning
warnings.filterwarnings("ignore", message=".*GeoPackage user_version.*may only be partially supported.*")

# Logger module-level per helper senza accesso a self._logger (es. _rmtree_safe,
# DefaultDataLoader): gli altri percorsi del service usano self._logger iniettato
# via LoggerProtocol. Fallback stdlib per esecuzione standalone di test.
try:
    from app.utils.logger import get_logger as _get_logger
    _logger = _get_logger(__name__)
except ImportError:
    import logging as _logging
    _logger = _logging.getLogger(__name__)

# ==================== IMPORT CONDIZIONALI ====================
# Usa modelli da app.models in produzione, fallback a dataclass per test standalone
# Usa modelli da app.models in produzione, fallback a dataclass per test standalone

try:
    from app.models import (
        FileSource,
        FileSourceCreate,
        FileSourceUpdate,
        FileSourceList,
        FileSourceLayersResponse,
        FileSourcePreview,
        ColumnInfo
    )
    _USE_APP_MODELS = True
except ImportError:
    _USE_APP_MODELS = False


# ==================== DEFINIZIONI TIPI (FALLBACK PER TEST) ====================
# Usate solo quando app.models non è disponibile (test standalone)

if not _USE_APP_MODELS:
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


# ==================== PROTOCOLLI + DEFAULT IMPLS ====================
#
# STRUCT-001 Seam B: i 4 Protocols (Logger/FileSystem/DataLoader/TimeProvider)
# e le 4 Default impls (NullLogger/DefaultTimeProvider/DefaultFileSystem/
# DefaultDataLoader) vivono in `app.services.file_service_deps`.
# Re-export per backward-compat coi test che importano direttamente
# (test_file_service_filesystem_contract usa DefaultFileSystem,
# test_n1_file_service_cross_tenant_contract usa DefaultTimeProvider) e
# per `FileSourceService.__init__` che continua a istanziare i Default
# come fallback (`self._fs = filesystem or DefaultFileSystem()`).

from app.services.file_service_deps import (  # noqa: F401  (re-export pubblico)
    LoggerProtocol,
    FileSystemProtocol,
    DataLoaderProtocol,
    TimeProviderProtocol,
    NullLogger,
    DefaultTimeProvider,
    DefaultFileSystem,
    DefaultDataLoader,
)


# ==================== FUNZIONI PURE ====================
#
# STRUCT-001 Seam A: i 4 helper pure (FormatDetector, TypeInferrer,
# FileIdGenerator, ColumnInfoBuilder) vivono in `app.shared.file_helpers`.
# Re-export per backward-compat coi test/consumer che importano da
# `app.services.file_service` (es. test_n1_file_service_cross_tenant_contract
# che usa FileIdGenerator). Identita' preservata: Module attribute is the same
# object as in app.shared.file_helpers (pattern Seam A R-01 / STRUCT-005).

from app.shared.file_helpers import (  # noqa: F401  (re-export pubblico)
    FormatDetector,
    TypeInferrer,
    FileIdGenerator,
    ColumnInfoBuilder,
)


# ==================== SERVIZIO PRINCIPALE ====================
# Classe FileService: CRUD completo per file source con DI.
# Gestisce il ciclo di vita dei file caricati dall'utente come sorgenti dati.
# Usa Dependency Injection per testabilità: ogni dipendenza esterna (filesystem,
# data loader, logger, time) è iniettabile con un mock nei test.


# STRUCT-001 Seam D-C: la logica vive in `app.shared.file_cleanup.rmtree_safe`.
# Wrapper compat per i call site interni che usano `_rmtree_safe(path)` senza
# passare logger esplicito. La funzione condivisa accetta `logger=None` e usa
# il logger del modulo come fallback (best-effort silenzioso); qui passiamo
# `_logger` del modulo file_service per preservare il naming dei log.

def _rmtree_safe(path, _retries: int = 3) -> None:
    """Wrapper compat: delega a `app.shared.file_cleanup.rmtree_safe`."""
    from app.shared.file_cleanup import rmtree_safe
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
        filesystem: FileSystemProtocol = None,
        data_loader: DataLoaderProtocol = None,
        logger: LoggerProtocol = None,
        time_provider: TimeProviderProtocol = None,
    ):
        self._fs = filesystem or DefaultFileSystem()
        self._loader = data_loader or DefaultDataLoader()
        self._logger = logger or NullLogger()
        self._time = time_provider or DefaultTimeProvider()

        # Pure function helpers
        self._format_detector = FormatDetector
        self._type_inferrer = TypeInferrer
        self._id_generator = FileIdGenerator
        self._column_builder = ColumnInfoBuilder

    # ==================== GENERAZIONE ID ====================
    
    def _generate_file_id(self, name: str) -> str:
        timestamp = self._time.now().strftime("%Y%m%d_%H%M%S")
        uuid_part = self._time.generate_uuid()
        return self._id_generator.generate(name, timestamp, uuid_part)
    
    # ==================== RILEVAMENTO FORMATO ====================
    
    def _detect_format(self, filename: str) -> str:
        return self._format_detector.detect(filename)
    
    # ==================== LISTA LAYERS / FOGLI ====================
    # Per file multi-layer (GPKG, XLSX, SHP in directory, FileGDB).
    
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
    
    # ==================== N1 CROSS-TENANT FAIL-CLOSED CHECK ====================

    # Sentinel per bypass esplicito superadmin. L'uso di questo valore
    # scrive un log INFO (audit trail); pass-through di un group_id
    # valido e' lo scenario normale.
    SUPERADMIN_BYPASS: str = "*"

    def _assert_visible(self, file_id: str, expected_group_id: str) -> None:
        """Fail-closed: verifica che `expected_group_id` abbia visibilita'
        sul file_id (owned o shared), altrimenti solleva PermissionError.

        Visibilita' calcolata via `app.shared.sharing.is_resource_visible`
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
            from app.shared.sharing import is_resource_visible, is_shareable
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
            # non esistente o DB glitch), FAIL-CLOSED: nega accesso.
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

    # ==================== CARICAMENTO DATI ====================
    # Carica DataFrame da file con gestione automatica formato e layer.
    # La definizione canonica di `load_dataframe` e' piu' in basso
    # (~riga 1200): estrae blob dal DB in temp file, carica via parser,
    # cleanup garantito con cleanup robusto (SHP/GDB necessitano rmtree
    # della temp dir parent, non solo unlink del file).

    def _load_by_format(
        self,
        file_path: Path,
        format_type: str,
        layer: str = None,
        sheet: str = None
    ) -> Tuple[pd.DataFrame, Optional[Any]]:
        """Wrapper compat: delega a `file_load_dispatch.load_by_format`.

        STRUCT-001 Seam D-B: la logica vive in
        `app.services.file_load_dispatch.load_by_format` come funzione
        standalone con collaborator espliciti. I 3 call site interni
        (`self._load_by_format(...)` in upload methods + load_dataframe)
        restano invariati. Behavior byte-per-byte preservato.
        """
        from app.services.file_load_dispatch import load_by_format
        return load_by_format(
            self._loader, file_path, format_type, layer, sheet,
            logger=self._logger,
        )
    
    # ==================== OPERAZIONI CRUD ====================
    # Upload, lista, preview, aggiornamento, eliminazione file source.
    # Lettura, creazione, aggiornamento, eliminazione file source.
    # Tutti i metodi usano il Protocol filesystem per I/O,
    # permettendo mock completi nei test unitari.
    
    # ==================== DB STORAGE HELPERS ====================
    #
    # STRUCT-001 Seam C: la logica policy-aware (db/object_storage/hybrid +
    # threshold) vive in `app.services.file_storage_dispatch.dispatch_blob_storage`.
    # Qui resta un thin wrapper che inietta `self._logger`. I 3 call site
    # interni (`self._dispatch_blob_storage(...)` in upload methods) restano
    # invariati. Behavior byte-per-byte preservato.

    def _dispatch_blob_storage(
        self, file_id: str, raw_data: bytes, filename: str,
    ) -> Dict[str, Any]:
        """Wrapper compat: delega a `file_storage_dispatch.dispatch_blob_storage`.

        Vedi `app.services.file_storage_dispatch.dispatch_blob_storage`
        per la documentazione completa del policy dispatch.
        """
        from app.services.file_storage_dispatch import dispatch_blob_storage
        return dispatch_blob_storage(
            file_id, raw_data, filename, logger=self._logger,
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
        """Wrapper compat: delega a `file_blob_materialization.get_blob_from_db`.

        STRUCT-001 Seam D-D: la logica vive in
        `app.services.file_blob_materialization.get_blob_from_db` con
        collaborator espliciti. Fail-closed N1 PRESERVATO: il check
        `_assert_visible` resta nel service e viene passato come callback.
        """
        from app.services.file_blob_materialization import get_blob_from_db
        from app.config import settings
        from app.shared.cloud_backends import get_runtime_object_storage
        import app.db.db_service as db
        return get_blob_from_db(
            file_id, expected_group_id,
            assert_visible=self._assert_visible,
            db_get_file_source=db.get_file_source,
            get_object_storage=get_runtime_object_storage,
            temp_dir=settings.TEMP_DIR,
            logger=self._logger,
        )

    def _temp_file_for(
        self, file_id: str, expected_group_id: str = None,
    ) -> Optional[Path]:
        """Wrapper compat: delega a `file_blob_materialization.temp_file_for`.

        Per SHP/GDB: estrae lo ZIP in temp dir e ritorna il path del
        .shp/.gdb. Il chiamante deve eliminare la dir parent dopo l'uso.

        N1: `expected_group_id` obbligatorio (fail-closed via assert_visible).
        """
        from app.services.file_blob_materialization import temp_file_for
        from app.config import settings
        from app.shared.cloud_backends import get_runtime_object_storage
        from app.shared.file_cleanup import rmtree_safe
        from app.utils.zip_safe import safe_extractall
        import app.db.db_service as db
        return temp_file_for(
            file_id, expected_group_id,
            assert_visible=self._assert_visible,
            db_get_file_source=db.get_file_source,
            get_object_storage=get_runtime_object_storage,
            temp_dir=settings.TEMP_DIR,
            safe_extractall=safe_extractall,
            cleanup=rmtree_safe,
            logger=self._logger,
        )

    # ==================== CRUD (SQLite) ====================

    def get_file_source(self, file_id: str) -> Optional[FileSource]:
        """Ottieni file source specifico dal DB."""
        try:
            import app.db.db_service as db
            import json
            row = db.get_file_source(file_id, include_data=False)
            if not row:
                return None
            metadata = row.get("metadata") or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            # Inietta group_id e created_by dal record DB (colonne separate)
            metadata["group_id"] = row.get("group_id", "")
            metadata["created_by"] = row.get("created_by")
            if _USE_APP_MODELS:
                return FileSource.model_validate(metadata)
            else:
                return FileSource.from_json(json.dumps(metadata))
        except Exception as e:
            self._logger.error(f"[GESTITO] Errore get_file_source {file_id}: {e}")
            return None

    def delete_file_source(self, file_id: str) -> bool:
        """Elimina file source dal DB."""
        try:
            import app.db.db_service as db
            if not db.get_file_source(file_id):
                return False
            db.delete_file_source(file_id)
            self._logger.info(f"FileSource eliminato: {file_id}")
            return True
        except Exception as e:
            self._logger.error(f"[GESTITO] Errore delete_file_source {file_id}: {e}")
            return False

    def list_file_sources(self, group_id: str = None) -> Any:
        """Lista file source dal DB.

        - group_id=None → no filter (usato dal superadmin per vedere tutti i gruppi)
        - group_id="X"  → solo file del gruppo X
        """
        try:
            import app.db.db_service as db
            # NB: NON applicare DEFAULT_GROUP come fallback. Se group_id è None,
            # significa "no filter" (superadmin); altrimenti il superadmin vedrebbe
            # solo i file del gruppo default invece di tutti.
            rows = db.list_file_sources(group_id)
            files = []
            for row in rows:
                fs = self.get_file_source(row["id"])
                if fs:
                    files.append(fs)
            if _USE_APP_MODELS:
                try:
                    from app.models import FileSourceList
                    return FileSourceList(files=files)
                except ImportError:
                    pass
            return files
        except Exception as e:
            self._logger.error(f"[GESTITO] Errore list_file_sources: {e}")
            if _USE_APP_MODELS:
                try:
                    from app.models import FileSourceList
                    return FileSourceList(files=[])
                except ImportError:
                    pass
            return []

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
        """Wrapper compat: delega a `file_upload.create_from_upload`.

        STRUCT-001 Seam D-F: la logica vive in
        `app.services.file_upload.create_from_upload` con collaborator
        espliciti. Behavior byte-per-byte preservato (parse best-effort,
        orphan guard su dispatch, cleanup garantito in finally).
        """
        from app.services.file_upload import create_from_upload as _impl
        from app.config import settings
        from app.shared.file_cleanup import rmtree_safe
        from app.utils.zip_safe import safe_extractall, ZipSlipError
        import app.db.db_service as db
        return _impl(
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
            db_save_file_source=db.save_file_source,
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
        """Wrapper compat: delega a `file_raw_upload.create_raw_from_upload`.

        STRUCT-001 Seam D-E: la logica vive in
        `app.services.file_raw_upload.create_raw_from_upload` con
        collaborator espliciti. Behavior byte-per-byte preservato:
        MIME validation defense-in-depth + DTO build + dispatch policy +
        DB save + logger info. Orphan guard rispettato.
        """
        from app.services.file_raw_upload import create_raw_from_upload as _impl
        from app.config import settings
        import app.db.db_service as db
        return _impl(
            file_content, filename, name, description, group_id,
            generate_file_id=self._generate_file_id,
            time_now=self._time.now,
            dispatch_blob_storage=self._dispatch_blob_storage,
            db_save_file_source=db.save_file_source,
            default_group=settings.DEFAULT_GROUP,
            build_file_source=lambda **kw: FileSource(**kw),
            logger=self._logger,
        )

    def _filter_columns(self, df: pd.DataFrame, selected_columns: List[str]) -> pd.DataFrame:
        valid_cols = [c for c in selected_columns if c in df.columns]
        return df[valid_cols] if valid_cols else df

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
        """Wrapper compat: delega a `file_multi_layer_upload.create_multi_layer_from_upload`.

        STRUCT-001 Seam D-G: la logica vive in
        `app.services.file_multi_layer_upload.create_multi_layer_from_upload`
        con collaborator espliciti. Behavior byte-per-byte preservato:
        MIME defense-in-depth sempre attiva, ZIP unwrap solo `.gpkg`/`.gdb`,
        loop per-layer best-effort, atomicita' batch NON garantita,
        cleanup asimmetrico (os.unlink per file + rmtree_safe per dir).
        """
        from app.services.file_multi_layer_upload import (
            create_multi_layer_from_upload as _impl,
        )
        from app.config import settings
        from app.shared.file_cleanup import rmtree_safe
        from app.utils.zip_safe import safe_extractall, ZipSlipError
        import os as _os
        import app.db.db_service as db
        return _impl(
            file_content, filename, name, description,
            layers_config, srid, group_id,
            generate_file_id=self._generate_file_id,
            time_now=self._time.now,
            detect_format=self._detect_format,
            load_by_format=self._load_by_format,
            column_builder_from_df=self._column_builder.from_dataframe,
            dispatch_blob_storage=self._dispatch_blob_storage,
            db_save_file_source=db.save_file_source,
            build_file_source=lambda **kw: FileSource(**kw),
            cleanup_dir=rmtree_safe,
            cleanup_file=_os.unlink,    # NON rmtree_safe - preserva asimmetria
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
        """Wrapper compat: delega a `file_source_read.load_dataframe`.

        N1 resta fail-closed: il modulo estratto riceve `_assert_visible`
        come collaborator esplicito.
        """
        from app.services.file_source_read import load_dataframe
        return load_dataframe(
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
        """Aggiorna file source nel DB."""
        import app.db.db_service as db
        file_source = self.get_file_source(file_id)
        if not file_source:
            return None
        update_data = data.model_dump(exclude_unset=True) if hasattr(data, 'model_dump') else data
        for field_name, value in update_data.items():
            setattr(file_source, field_name, value)
        if 'layer' in update_data or 'sheet' in update_data:
            try:
                # N1: update interno — il file e' appena stato recuperato
                # e il caller (router) ha gia' validato l'ownership.
                # Passiamo il group_id reale del file come "expected" per
                # il fail-closed del helper.
                df, gdf = self.load_dataframe(
                    file_id, expected_group_id=file_source.group_id,
                )
                raw_cols = self._column_builder.from_dataframe(df, gdf)
                file_source.columns = [
                    {"name": c.name, "type": c.type} if not isinstance(c, dict) else c
                    for c in raw_cols
                ]
                file_source.row_count = len(df)
            except Exception as e:
                self._logger.warning(f"Errore aggiornamento colonne: {e}")
        file_source.updated_at = self._time.now()
        metadata = file_source.model_dump(mode='json') if _USE_APP_MODELS else {}
        try:
            db.update_file_source_metadata(file_id, metadata)
        except Exception:
            # Fallback: riscrivi il record completo.
            # Recupera il group_id originale dal DB invece di usare "default".
            existing_row = db.get_file_source(file_id, include_data=False) or {}
            db.save_file_source(
                file_id=file_id,
                group_id=existing_row.get("group_id") or settings.DEFAULT_GROUP,
                name=file_source.name,
                original_filename=file_source.original_filename,
                fmt=file_source.format,
                data=self._get_blob_from_db(
                    file_id, expected_group_id=file_source.group_id,
                ) or b"",
                metadata=metadata,
            )
        return file_source

    def get_layers_info(
        self, file_id: str, expected_group_id: str = None,
    ) -> Dict[str, Any]:
        """Wrapper compat: delega a `file_source_read.get_layers_info`.

        N1: `expected_group_id` obbligatorio per fail-closed check.
        """
        from app.services.file_source_read import get_layers_info
        return get_layers_info(
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
        """Wrapper compat: delega a `file_source_read.get_preview`.

        N1: `expected_group_id` obbligatorio per fail-closed check.
        """
        from app.services.file_source_read import get_preview
        return get_preview(
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
        """Wrapper compat: delega a `file_preview_temp.preview_file_temp`.

        STRUCT-001 Seam D-A: la logica vive in
        `app.services.file_preview_temp.preview_file_temp` come
        funzione standalone go-ready (collaborator espliciti via kwargs,
        no DB, no auth). Qui passiamo i collaborator del service.
        """
        from app.services.file_preview_temp import preview_file_temp as _preview
        return _preview(
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


# ==================== FACTORY ====================
# Costruisce FileService con tutte le dipendenze di produzione.
# Per creare istanze con dependency injection personalizzata (test, ambienti custom)

def create_file_service(
    filesystem: FileSystemProtocol = None,
    data_loader: DataLoaderProtocol = None,
) -> FileSourceService:
    """Factory function per creare service con dipendenze iniettate."""
    return FileSourceService(
        filesystem=filesystem,
        data_loader=data_loader,
    )


# ==================== SINGLETON PRODUZIONE ====================
# Istanza globale per uso da router FastAPI (evita ri-creazione ad ogni richiesta).
# Per test, usare create_file_service() con mock iniettati.
def _create_production_service() -> FileSourceService:
    """Crea service con configurazione di produzione"""
    try:
        from app.utils.logger import get_logger
        logger = get_logger(__name__)
        return FileSourceService(logger=logger)
    except ImportError:
        # Fallback per test standalone
        return FileSourceService()


# Singleton globale per uso da router FastAPI
file_source_service: FileSourceService = _create_production_service()
