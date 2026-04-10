"""
Servizio CRUD per File Source (sorgenti dati da file caricati).

Architettura
============
Questo servizio gestisce il ciclo di vita dei file caricati dall'utente
come sorgenti dati per i progetti MANIPOLA. Supporta upload, preview,
listing, aggiornamento ed eliminazione.

Dependency Injection per testabilità:
- FileSystemProtocol: operazioni filesystem (mock per test senza I/O reale)
- DataLoaderProtocol: caricamento dati (mock per test senza GeoPandas)
- LoggerProtocol: logging (NullLogger per test silenziosi)
- TimeProvider: datetime/uuid (mock per test deterministici e riproducibili)

Formati supportati:
- Tabulari: CSV, XLSX (Excel), JSON
- Geospaziali: GeoJSON, GeoPackage (.gpkg), Shapefile (.shp/.zip), FileGDB

Struttura filesystem:
    data/file_sources/
    └── <file_id>/
        ├── metadata.json       # Metadati (nome, formato, layers, colonne)
        ├── original.<ext>      # File originale caricato
        └── processed/          # File processati per layer

Operazioni principali:
- upload(): salva file, rileva formato, estrae layers e schema colonne
- preview(): restituisce le prime N righe come JSON per anteprima UI
- get_layers(): lista layers/sheet per file multi-layer (GPKG, XLSX, SHP)
- update(): aggiorna metadati (nome, layer selezionato)
- delete(): rimuove file e metadati dal filesystem
"""

import json
import re
import warnings
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple, BinaryIO, Dict, Any, Protocol, Callable
from dataclasses import dataclass, field
import pandas as pd

# Sopprimi warning GDAL per versioni GeoPackage più recenti della versione GDAL installata
# Non bloccante — i file vengono letti correttamente nonostante il warning
warnings.filterwarnings("ignore", message=".*GeoPackage user_version.*may only be partially supported.*")

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
        file_path: str
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
                "file_path": self.file_path,
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


# ==================== PROTOCOLLI PER DEPENDENCY INJECTION ====================
# Interfacce astratte per disaccoppiare il servizio dall'implementazione concreta.
# In produzione: filesystem reale + GeoPandas. In test: mock in-memory.

class LoggerProtocol(Protocol):
    """Protocol per logger"""
    def info(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...
    def debug(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...


class FileSystemProtocol(Protocol):
    """Protocol per operazioni filesystem - mockabile per test"""
    def mkdir(self, path: Path, parents: bool = True, exist_ok: bool = True) -> None: ...
    def exists(self, path: Path) -> bool: ...
    def is_dir(self, path: Path) -> bool: ...
    def is_file(self, path: Path) -> bool: ...
    def iterdir(self, path: Path) -> List[Path]: ...
    def read_text(self, path: Path, encoding: str = 'utf-8') -> str: ...
    def write_text(self, path: Path, content: str, encoding: str = 'utf-8') -> None: ...
    def write_binary(self, path: Path, content: BinaryIO) -> int: ...
    def stat_size(self, path: Path) -> int: ...
    def rmtree(self, path: Path) -> None: ...
    def unlink(self, path: Path) -> None: ...
    def extract_zip(self, zip_path: Path, dest_dir: Path) -> None: ...
    def rglob(self, path: Path, pattern: str) -> List[Path]: ...


class DataLoaderProtocol(Protocol):
    """Protocol per caricamento dati - mockabile per test"""
    def load_csv(self, path: Path) -> pd.DataFrame: ...
    def load_excel(self, path: Path, sheet: Optional[str] = None) -> pd.DataFrame: ...
    def load_json(self, path: Path) -> pd.DataFrame: ...
    def load_geospatial(self, path: Path, layer: Optional[str] = None) -> Any: ...
    def list_layers(self, path: Path) -> List[str]: ...
    def list_sheets(self, path: Path) -> List[str]: ...
    def save_csv(self, df: pd.DataFrame, path: Path) -> None: ...
    def save_excel(self, df: pd.DataFrame, path: Path, sheet: str) -> None: ...
    def save_json(self, df: pd.DataFrame, path: Path) -> None: ...
    def save_geospatial(self, gdf: Any, path: Path, driver: str, layer: Optional[str] = None) -> None: ...


class TimeProviderProtocol(Protocol):
    """Protocol per datetime/uuid - mockabile per test deterministici"""
    def now(self) -> datetime: ...
    def generate_uuid(self) -> str: ...


# ==================== IMPLEMENTAZIONI DEFAULT / NULL ====================
# Implementazioni concrete dei protocolli per uso in produzione e testing.

class NullLogger:
    """Logger che non fa nulla - per test silenziosi"""
    def info(self, msg: str) -> None: pass
    def error(self, msg: str) -> None: pass
    def debug(self, msg: str) -> None: pass
    def warning(self, msg: str) -> None: pass


class DefaultTimeProvider:
    """Time provider di default"""
    def now(self) -> datetime:
        return datetime.now()
    
    def generate_uuid(self) -> str:
        import uuid
        return str(uuid.uuid4())[:8]


class DefaultFileSystem:
    """Implementazione default filesystem"""
    
    def mkdir(self, path: Path, parents: bool = True, exist_ok: bool = True) -> None:
        path.mkdir(parents=parents, exist_ok=exist_ok)
    
    def exists(self, path: Path) -> bool:
        return path.exists()
    
    def is_dir(self, path: Path) -> bool:
        return path.is_dir()
    
    def is_file(self, path: Path) -> bool:
        return path.is_file()
    
    def iterdir(self, path: Path) -> List[Path]:
        return list(path.iterdir()) if path.exists() else []
    
    def read_text(self, path: Path, encoding: str = 'utf-8') -> str:
        return path.read_text(encoding=encoding)
    
    def write_text(self, path: Path, content: str, encoding: str = 'utf-8') -> None:
        path.write_text(content, encoding=encoding)
    
    def write_binary(self, path: Path, content: BinaryIO) -> int:
        import shutil
        with open(path, 'wb') as f:
            shutil.copyfileobj(content, f)
        return path.stat().st_size
    
    def stat_size(self, path: Path) -> int:
        return path.stat().st_size
    
    def rmtree(self, path: Path) -> None:
        import shutil
        shutil.rmtree(path)
    
    def unlink(self, path: Path) -> None:
        path.unlink()
    
    def extract_zip(self, zip_path: Path, dest_dir: Path) -> None:
        import zipfile
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(dest_dir)
    
    def rglob(self, path: Path, pattern: str) -> List[Path]:
        return list(path.rglob(pattern))


class DefaultDataLoader:
    """Implementazione default data loader"""
    
    def load_csv(self, path: Path) -> pd.DataFrame:
        """
        Carica CSV con rilevamento automatico di encoding e delimitatore.
        Ordine tentativi:
          1. UTF-8 + sep auto (sniff)
          2. UTF-8 + ; esplicito
          3. Latin-1 + sep auto (sniff)
          4. Latin-1 + ; esplicito
        """
        import csv as _csv

        def _detect_sep(path, encoding):
            try:
                with open(path, encoding=encoding, errors='replace') as f:
                    sample = f.read(4096)
                dialect = _csv.Sniffer().sniff(sample, delimiters=',;\t|')
                return dialect.delimiter
            except Exception:
                return ','

        for encoding in ['utf-8', 'latin-1', 'cp1252']:
            try:
                sep = _detect_sep(path, encoding)
                df = pd.read_csv(path, sep=sep, encoding=encoding)
                # Sanity check: se una sola colonna e contiene il delimitatore, riprova con ;
                if len(df.columns) == 1 and (';' in str(df.columns[0]) or ',' in str(df.columns[0])):
                    for alt_sep in [';', ',', '\t']:
                        try:
                            df2 = pd.read_csv(path, sep=alt_sep, encoding=encoding)
                            if len(df2.columns) > 1:
                                return df2
                        except Exception:
                            continue
                return df
            except UnicodeDecodeError:
                continue
            except Exception:
                continue

        # Ultimo fallback
        return pd.read_csv(path, encoding='latin-1', sep=';', on_bad_lines='skip')
    
    def load_excel(self, path: Path, sheet: Optional[str] = None) -> pd.DataFrame:
        if sheet:
            return pd.read_excel(path, sheet_name=sheet)
        return pd.read_excel(path)
    
    def load_json(self, path: Path) -> pd.DataFrame:
        try:
            return pd.read_json(path, orient='records')
        except (ValueError, TypeError):
            # orient='records' fallisce se il JSON è un dict top-level (es.
            # {"key": [...]}) o ha schema non tabulare. Retry senza orient
            # per lasciare a pandas l'inferenza.
            return pd.read_json(path)
    
    def load_geospatial(self, path: Path, layer: Optional[str] = None) -> Any:
        import geopandas as gpd
        if layer:
            return gpd.read_file(path, layer=layer)
        return gpd.read_file(path)
    
    def list_layers(self, path: Path) -> List[str]:
        try:
            import fiona
            return fiona.listlayers(str(path))
        except Exception:
            return []
    
    def list_sheets(self, path: Path) -> List[str]:
        try:
            xlsx = pd.ExcelFile(path)
            return xlsx.sheet_names
        except Exception:
            return []
    
    def save_csv(self, df: pd.DataFrame, path: Path) -> None:
        df.to_csv(path, index=False)
    
    def save_excel(self, df: pd.DataFrame, path: Path, sheet: str) -> None:
        df.to_excel(path, index=False, sheet_name=sheet)
    
    def save_json(self, df: pd.DataFrame, path: Path) -> None:
        df.to_json(path, orient='records', indent=2)
    
    def save_geospatial(self, gdf: Any, path: Path, driver: str, layer: Optional[str] = None) -> None:
        if layer:
            gdf.to_file(path, driver=driver, layer=layer)
        else:
            gdf.to_file(path, driver=driver)


# ==================== FUNZIONI PURE ====================
# Funzioni senza side-effect: inferenza colonne, rilevamento formato, parsing layer.

class FormatDetector:
    """Rileva formato file - PURE FUNCTIONS"""
    
    FORMAT_MAP = {
        '.csv': 'csv',
        '.xlsx': 'xlsx',
        '.xls': 'xlsx',
        '.json': 'json',
        '.geojson': 'geojson',
        '.gpkg': 'gpkg',
        '.shp': 'shp',
        '.gdb': 'gdb',
        '.zip': 'zip'
    }
    
    @classmethod
    def detect(cls, filename: str) -> str:
        """Rileva formato dall'estensione"""
        ext = Path(filename).suffix.lower()
        return cls.FORMAT_MAP.get(ext, 'unknown')
    
    @classmethod
    def is_geospatial(cls, format_type: str) -> bool:
        """Verifica se formato è geospaziale"""
        return format_type in ['geojson', 'gpkg', 'shp', 'gdb']
    
    @classmethod
    def is_tabular(cls, format_type: str) -> bool:
        """Verifica se formato è tabellare"""
        return format_type in ['csv', 'xlsx', 'json']


class TypeInferrer:
    """Inferisce tipi colonne - PURE FUNCTIONS"""
    
    WKT_PREFIXES = ('POINT', 'LINESTRING', 'POLYGON', 'MULTIPOINT', 
                   'MULTILINESTRING', 'MULTIPOLYGON', 'GEOMETRYCOLLECTION')
    
    GEOMETRY_TYPE_MAP = {
        'point': 'geometry:point',
        'multipoint': 'geometry:multipoint',
        'linestring': 'geometry:line',
        'multilinestring': 'geometry:multiline',
        'polygon': 'geometry:polygon',
        'multipolygon': 'geometry:multipolygon'
    }
    
    @classmethod
    def infer_column_type(cls, series: pd.Series) -> str:
        """Inferisce tipo colonna da pandas Series"""
        dtype = str(series.dtype)
        
        if 'int' in dtype:
            return 'int'
        elif 'float' in dtype:
            return 'real'
        elif 'bool' in dtype:
            return 'bool'
        elif 'datetime' in dtype:
            return 'date'
        elif 'geometry' in dtype.lower():
            return 'geometry'
        else:
            return cls._infer_from_values(series)
    
    @classmethod
    def _infer_from_values(cls, series: pd.Series) -> str:
        """Inferisce tipo da valori"""
        if series.dropna().empty:
            return 'str'
        
        sample = str(series.dropna().iloc[0])
        
        # Check WKT geometry
        sample_upper = sample.strip().upper()
        if any(sample_upper.startswith(prefix) for prefix in cls.WKT_PREFIXES):
            return 'geometry'
        
        # Check date
        if len(sample) >= 8 and any(c in sample for c in ['-', '/']):
            try:
                pd.to_datetime(series.dropna().head(10))
                return 'date'
            except Exception:
                pass  # pd.to_datetime fallisce → non è data
        
        return 'str'
    
    @classmethod
    def detect_geometry_type(cls, geom_types: List[str]) -> str:
        """Mappa tipo geometria"""
        if not geom_types:
            return 'geometry'
        
        geom_type = geom_types[0].lower()
        return cls.GEOMETRY_TYPE_MAP.get(geom_type, 'geometry')


class FileIdGenerator:
    """Genera ID per file source - PURE (con time provider iniettato)"""
    
    @staticmethod
    def generate(name: str, timestamp: str, uuid_part: str) -> str:
        """Genera ID univoco"""
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '_')).strip()
        safe_name = safe_name.replace(' ', '_')[:30]
        return f"file_{safe_name}_{timestamp}_{uuid_part}"
    
    @staticmethod
    def sanitize_name(name: str, max_length: int = 30) -> str:
        """Sanitizza nome per uso in path"""
        safe = "".join(c for c in name if c.isalnum() or c in (' ', '_')).strip()
        return safe.replace(' ', '_')[:max_length]


# ==================== COSTRUTTORE INFO COLONNE ====================
# Analizza un DataFrame e costruisce la lista di ColumnInfo con tipo inferito.

class ColumnInfoBuilder:
    """Costruisce info colonne - separato per testabilità"""
    
    @staticmethod
    def from_dataframe(
        df: pd.DataFrame, 
        gdf: Any = None, 
        user_srid: Optional[int] = None
    ) -> List[ColumnInfo]:
        """Estrae info colonne da DataFrame"""
        columns = []
        geom_columns = {}
        
        # Mappa colonne geometriche dal GeoDataFrame
        if gdf is not None:
            geom_columns = ColumnInfoBuilder._extract_geometry_columns(gdf, user_srid)
        
        for col in df.columns:
            if col in geom_columns:
                col_type = geom_columns[col]
            else:
                col_type = TypeInferrer.infer_column_type(df[col])
                if col_type == 'geometry' and user_srid:
                    col_type = f"geometry:{user_srid}"
            
            columns.append(ColumnInfo(name=col, type=col_type))
        
        return columns
    
    @staticmethod
    def _extract_geometry_columns(gdf: Any, user_srid: Optional[int]) -> Dict[str, str]:
        """Estrae colonne geometriche da GeoDataFrame"""
        geom_columns = {}
        
        for col in gdf.columns:
            try:
                col_dtype = str(gdf[col].dtype)
                
                if col_dtype == 'geometry' or hasattr(gdf[col], 'geom_type'):
                    # Rileva tipo geometria
                    geom_series = gdf[col]
                    if not geom_series.empty:
                        geom_types = geom_series.dropna().apply(
                            lambda g: g.geom_type if g else None
                        ).dropna().unique().tolist()
                        geom_type = TypeInferrer.detect_geometry_type(geom_types)
                    else:
                        geom_type = 'geometry'
                    
                    # Determina SRID
                    if gdf.crs:
                        srid = gdf.crs.to_epsg() or user_srid or 4326
                        geom_columns[col] = f"{geom_type}:{srid}"
                    elif user_srid:
                        geom_columns[col] = f"{geom_type}:{user_srid}"
                    else:
                        geom_columns[col] = geom_type
            except Exception as _e:
                pass  # Rilevamento geometria fallito (non critico)
        
        return geom_columns


# ==================== SERVIZIO PRINCIPALE ====================
# Classe FileService: CRUD completo per file source con DI.
# Gestisce il ciclo di vita dei file caricati dall'utente come sorgenti dati.
# Usa Dependency Injection per testabilità: ogni dipendenza esterna (filesystem,
# data loader, logger, time) è iniettabile con un mock nei test.


def _rmtree_safe(path) -> None:
    """shutil.rmtree con fallback Windows per file/cartelle locked (es. .gdb usato da fiona)."""
    import stat
    def _on_error(func, fpath, exc_info):
        try:
            os.chmod(fpath, stat.S_IWRITE)
            func(fpath)
        except Exception:
            pass  # non critico
    try:
        shutil.rmtree(path, onerror=_on_error)
    except Exception:
        pass  # non critico

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
        files_dir: Path = None
    ):
        self._fs = filesystem or DefaultFileSystem()
        self._loader = data_loader or DefaultDataLoader()
        self._logger = logger or NullLogger()
        self._time = time_provider or DefaultTimeProvider()
        self._files_dir = files_dir or Path("/tmp/files")
        
        # Pure function helpers
        self._format_detector = FormatDetector
        self._type_inferrer = TypeInferrer
        self._id_generator = FileIdGenerator
        self._column_builder = ColumnInfoBuilder
    
    @property
    def FILES_DIR(self) -> Path:
        """Backward compatibility"""
        return self._files_dir
    
    # ==================== HELPER PERCORSI ====================
    
    def _get_file_dir(self, file_id: str) -> Path:
        return self._files_dir / file_id
    
    def _get_metadata_path(self, file_id: str) -> Path:
        return self._get_file_dir(file_id) / "metadata.json"
    
    def _get_data_dir(self, file_id: str) -> Path:
        path = self._get_file_dir(file_id) / "data"
        self._fs.mkdir(path, parents=True, exist_ok=True)
        return path
    
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
    
    # ==================== CARICAMENTO DATI ====================
    # Carica DataFrame da file con gestione automatica formato e layer.
    
    def load_dataframe(
        self,
        file_id: str,
        layer: str = None,
        sheet: str = None
    ) -> Tuple[pd.DataFrame, Optional[Any]]:
        """Carica dati da file source come DataFrame — legge blob dal DB."""
        import tempfile, os
        import app.db.db_service as db

        file_source = self.get_file_source(file_id)
        if not file_source:
            raise ValueError(f"File source '{file_id}' non trovato")

        # Leggi blob dal DB
        row = db.get_file_source(file_id, include_data=True)
        if not row or not row.get('data'):
            raise ValueError(f"Nessun dato nel DB per file source '{file_id}'")

        raw_data: bytes = row['data']
        fmt = file_source.format
        suffix = Path(file_source.file_path).suffix or f".{fmt}"

        # Scrivi in temp e carica
        from app.config import settings
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=settings.TEMP_DIR)
        tmp.write(raw_data)
        tmp.close()
        tmp_path = Path(tmp.name)

        use_layer = layer or file_source.layer
        use_sheet = sheet or file_source.sheet

        try:
            return self._load_by_format(tmp_path, fmt, use_layer, use_sheet)
        finally:
            try:
                os.unlink(str(tmp_path))
            except Exception:
                pass
    
    def _find_data_file(self, data_files: List[Path], format_type: str) -> Optional[Path]:
        """Trova il file dati corretto"""
        if format_type == 'gdb':
            for f in data_files:
                if self._fs.is_dir(f) and f.name.endswith('.gdb'):
                    return f
        elif format_type == 'shp':
            for f in data_files:
                if f.suffix.lower() == '.shp':
                    return f
        elif format_type == 'gpkg':
            for f in data_files:
                if f.suffix.lower() == '.gpkg':
                    return f
        else:
            return data_files[0] if data_files else None
        return None
    
    def _load_by_format(
        self, 
        file_path: Path, 
        format_type: str, 
        layer: str = None, 
        sheet: str = None
    ) -> Tuple[pd.DataFrame, Optional[Any]]:
        """Carica dati in base al formato"""
        gdf = None
        
        if format_type == 'csv':
            df = self._loader.load_csv(file_path)
        elif format_type == 'xlsx':
            df = self._loader.load_excel(file_path, sheet)
        elif format_type == 'json':
            df = self._loader.load_json(file_path)
        elif format_type in ['geojson', 'gpkg', 'shp', 'gdb']:
            gdf = self._loader.load_geospatial(file_path, layer)
            df = pd.DataFrame(gdf)
            if 'geometry' in df.columns:
                df['geometry'] = gdf.geometry.apply(lambda g: g.wkt if g else None)
        else:
            raise ValueError(f"Formato '{format_type}' non supportato")
        
        self._logger.info(f"Caricato: {len(df)} righe, {len(df.columns)} colonne")
        return df, gdf
    
    # ==================== OPERAZIONI CRUD ====================
    # Upload, lista, preview, aggiornamento, eliminazione file source.
    # Lettura, creazione, aggiornamento, eliminazione file source.
    # Tutti i metodi usano il Protocol filesystem per I/O,
    # permettendo mock completi nei test unitari.
    
    # ==================== DB STORAGE HELPERS ====================

    def _get_blob_from_db(self, file_id: str) -> Optional[bytes]:
        """Recupera blob file dal DB SQLite."""
        try:
            import app.db.db_service as db
            row = db.get_file_source(file_id, include_data=True)
            return row["data"] if row else None
        except Exception as e:
            self._logger.error(f"[GESTITO] Errore lettura blob {file_id}: {e}")
            return None

    def _temp_file_for(self, file_id: str) -> Optional[Path]:
        """
        Estrae il blob dal DB in un file temporaneo.
        Per SHP: estrae lo ZIP in una temp dir e ritorna il path del .shp.
        Il chiamante deve eliminare il file (o la dir intera per SHP) dopo l'uso.
        """
        import tempfile
        try:
            import app.db.db_service as db
            row_meta = db.get_file_source(file_id, include_data=False)
            blob = self._get_blob_from_db(file_id)
        except Exception:
            return None
        if not blob or not row_meta:
            return None

        fmt = row_meta.get("format", "")
        original_filename = row_meta.get("original_filename", f"{file_id}.bin")

        # Per SHP e GDB: il blob è uno ZIP — estrai tutto in temp dir
        if fmt in ("shp", "gdb"):
            import zipfile, io
            from app.config import settings
            tmp_dir = Path(tempfile.mkdtemp(dir=settings.TEMP_DIR))
            try:
                with zipfile.ZipFile(io.BytesIO(blob), 'r') as z:
                    z.extractall(tmp_dir)
                if fmt == "shp":
                    shp_files = list(tmp_dir.rglob("*.shp")) + list(tmp_dir.rglob("*.SHP"))
                    if shp_files:
                        return shp_files[0]
                else:
                    gdb_dirs = [f for f in tmp_dir.rglob("*") if f.is_dir() and f.suffix.lower() == ".gdb"]
                    if gdb_dirs:
                        return gdb_dirs[0]
            except Exception as e:
                self._logger.error(f"[GESTITO] Errore estrazione {fmt.upper()} zip {file_id}: {e}")
            return None

        suffix = Path(original_filename).suffix or ".bin"
        from app.config import settings
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=settings.TEMP_DIR)
        tmp.write(blob)
        tmp.close()
        return Path(tmp.name)

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
        """Crea file source da upload multipart. Salva blob nel DB SQLite."""
        import tempfile, os, zipfile as _zipfile
        import app.db.db_service as db
        from app.config import settings

        file_id = self._generate_file_id(name)
        format_type = self._detect_format(filename)

        suffix = Path(filename).suffix or ".bin"
        from app.config import settings
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=settings.TEMP_DIR)
        file_content.seek(0)
        raw_data = file_content.read()
        tmp.write(raw_data)
        tmp.close()
        tmp_path = Path(tmp.name)

        zip_raw_data = raw_data  # conserva ZIP originale per SHP
        zip_tmp_dir = None

        try:
            if format_type == 'zip':
                from app.config import settings
                zip_tmp_dir = Path(tempfile.mkdtemp(dir=settings.TEMP_DIR))
                try:
                    with _zipfile.ZipFile(tmp_path, 'r') as z:
                        z.extractall(zip_tmp_dir)
                except _zipfile.BadZipFile as e:
                    raise ValueError(f"File ZIP non valido o corrotto: {e}") from e
                extracted_files = [f for f in zip_tmp_dir.rglob("*") if f.is_file() or (f.is_dir() and f.suffix.lower() == ".gdb")]
                for ext in ['.shp', '.gpkg', '.gdb', '.geojson', '.csv', '.xlsx']:
                    match = next((f for f in extracted_files if f.suffix.lower() == ext), None)
                    if match:
                        tmp_path = match
                        format_type = self._detect_format(match.name)
                        filename = match.name
                        # Per SHP teniamo il ZIP (ha bisogno di .shx, .dbf, ecc.)
                        if format_type in ('shp', 'gdb'):
                            raw_data = zip_raw_data  # salva ZIP nel blob (GDB è cartella, non leggibile come bytes)
                        else:
                            raw_data = tmp_path.read_bytes()
                        break

            if format_type in ['gdb', 'gpkg']:
                layers = self.list_layers(tmp_path)
                if not layer and len(layers) == 1:
                    layer = layers[0]
            elif format_type == 'xlsx':
                sheets = self.list_sheets(tmp_path)
                if not sheet and len(sheets) == 1:
                    sheet = sheets[0]

            columns = []
            row_count = 0
            crs = None
            try:
                df, gdf = self._load_by_format(tmp_path, format_type, layer, sheet)
                if selected_columns:
                    df = self._filter_columns(df, selected_columns)
                columns = self._column_builder.from_dataframe(df, gdf, srid)
                row_count = len(df)
                if gdf is not None and hasattr(gdf, 'crs') and gdf.crs:
                    # Preferisce il codice EPSG numerico al WKT grezzo
                    epsg = gdf.crs.to_epsg()
                    if epsg:
                        crs = f"EPSG:{epsg}"
                    else:
                        # Fallback: prova to_authority (es. ESRI:xxxxx)
                        auth = gdf.crs.to_authority()
                        crs = f"{auth[0]}:{auth[1]}" if auth else str(gdf.crs)
                elif srid:
                    crs = f"EPSG:{srid}"
            except Exception as e:
                self._logger.warning(f"Errore inferenza colonne: {e}")

            now = self._time.now()
            # Converti ColumnInfo locali in dict per compatibilità con app.models.FileSource
            columns_dicts = [
                {"name": c.name, "type": c.type} if not isinstance(c, dict) else c
                for c in columns
            ]
            file_source = FileSource(
                id=file_id,
                name=name,
                description=description,
                file_path=filename,
                original_filename=filename,
                format=format_type,
                layer=layer,
                sheet=sheet,
                columns=columns_dicts,
                row_count=row_count,
                file_size=len(raw_data),
                crs=crs,
                group_id=group_id or settings.DEFAULT_GROUP,
                created_at=now,
                updated_at=now
            )

            metadata = file_source.model_dump(mode='json') if _USE_APP_MODELS else {}
            db.save_file_source(
                file_id=file_id,
                group_id=group_id or settings.DEFAULT_GROUP,
                name=name,
                original_filename=filename,
                fmt=format_type,
                data=raw_data,
                metadata=metadata,
            )

            self._logger.info(f"FileSource creato: {file_id} ({len(raw_data)} bytes)")
            return file_source

        finally:
            try:
                _rmtree_safe(tmp.name)
            except Exception:
                pass  # non critico

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
        """
        Crea N FileSource da un file multi-layer (GPKG, GDB).

        layers_config: lista di dict con:
            - layer: nome layer
            - selected_columns: lista colonne da importare (opzionale)

        Il nome di ogni FileSource sara: name_layername
        Tutti i FileSource condividono lo stesso blob binario.

        Returns: lista di FileSource creati (uno per layer)
        """
        import tempfile, os, zipfile as _zipfile
        import app.db.db_service as db
        from app.config import settings

        if not layers_config:
            raise ValueError("layers_config non puo essere vuota")

        file_content.seek(0)
        raw_data = file_content.read()

        suffix = Path(filename).suffix or ".bin"
        from app.config import settings
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=settings.TEMP_DIR)
        tmp.write(raw_data)
        tmp.close()
        tmp_path = Path(tmp.name)

        zip_tmp_dir = None
        format_type = self._detect_format(filename)

        try:
            if format_type == 'zip':
                from app.config import settings
                zip_tmp_dir = Path(tempfile.mkdtemp(dir=settings.TEMP_DIR))
                try:
                    with _zipfile.ZipFile(tmp_path, 'r') as z:
                        z.extractall(zip_tmp_dir)
                except _zipfile.BadZipFile as e:
                    raise ValueError(f"File ZIP non valido o corrotto: {e}") from e
                extracted_files = [f for f in zip_tmp_dir.rglob("*") if f.is_file() or (f.is_dir() and f.suffix.lower() == ".gdb")]
                for ext in ['.gpkg', '.gdb']:
                    match = next((f for f in extracted_files if f.suffix.lower() == ext), None)
                    if match:
                        tmp_path = match
                        format_type = self._detect_format(match.name)
                        filename = match.name
                        raw_data = tmp_path.read_bytes()
                        break

            # Determina se stiamo lavorando con layer (gpkg/gdb) o sheet (xlsx)
            is_sheet_based = format_type in ['xlsx', 'xls']

            created = []
            now = self._time.now()

            for layer_cfg in layers_config:
                # Supporta sia {"layer": "nome"} che {"sheet": "nome"}
                layer = layer_cfg.get("layer") or layer_cfg.get("sheet")
                selected_columns = layer_cfg.get("selected_columns")

                safe_layer = layer.replace(" ", "_").replace("-", "_") if layer else "default"
                file_source_name = f"{name}_{safe_layer}"
                file_id = self._generate_file_id(file_source_name)

                columns = []
                row_count = 0
                crs = None
                try:
                    if is_sheet_based:
                        df, gdf = self._load_by_format(tmp_path, format_type, None, layer)
                    else:
                        df, gdf = self._load_by_format(tmp_path, format_type, layer, None)
                    if selected_columns:
                        df = self._filter_columns(df, selected_columns)
                    columns = self._column_builder.from_dataframe(df, gdf, srid)
                    row_count = len(df)
                    if gdf is not None and hasattr(gdf, 'crs') and gdf.crs:
                        epsg = gdf.crs.to_epsg()
                        if epsg:
                            crs = f"EPSG:{epsg}"
                        else:
                            auth = gdf.crs.to_authority()
                            crs = f"{auth[0]}:{auth[1]}" if auth else str(gdf.crs)
                    elif srid:
                        crs = f"EPSG:{srid}"
                except Exception as e:
                    self._logger.warning(f"Errore inferenza colonne layer '{layer}': {e}")

                columns_dicts = [
                    {"name": c.name, "type": c.type} if not isinstance(c, dict) else c
                    for c in columns
                ]

                file_source = FileSource(
                    id=file_id,
                    name=file_source_name,
                    description=description,
                    file_path=filename,
                    original_filename=filename,
                    format=format_type,
                    layer=None if is_sheet_based else layer,
                    sheet=layer if is_sheet_based else None,
                    columns=columns_dicts,
                    row_count=row_count,
                    file_size=len(raw_data),
                    crs=crs,
                    group_id=group_id or settings.DEFAULT_GROUP,
                    created_at=now,
                    updated_at=now
                )

                metadata = file_source.model_dump(mode='json') if _USE_APP_MODELS else {}
                db.save_file_source(
                    file_id=file_id,
                    group_id=group_id or settings.DEFAULT_GROUP,
                    name=file_source_name,
                    original_filename=filename,
                    fmt=format_type,
                    data=raw_data,
                    metadata=metadata,
                )

                self._logger.info(f"FileSource multi-layer creato: {file_id} layer='{layer}'")
                created.append(file_source)

            return created

        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
            if zip_tmp_dir:
                import shutil
                try:
                    _rmtree_safe(zip_tmp_dir)
                except Exception:
                    pass

    def _handle_zip(self, zip_path: Path, data_dir: Path) -> Tuple[str, Path]:
        """Backward compat."""
        import zipfile
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(data_dir)
        extracted = [f for f in data_dir.iterdir() if f.is_file() and f != zip_path]
        if not extracted:
            raise ValueError("ZIP vuoto")
        target = extracted[0]
        return self._detect_format(target.name), target

    def load_dataframe(self, file_id: str, layer: str = None, sheet: str = None) -> Tuple[pd.DataFrame, Optional[Any]]:
        """Carica dati da file source — estrae blob in temp file."""
        file_source = self.get_file_source(file_id)
        if not file_source:
            raise ValueError(f"File source '{file_id}' non trovato")
        tmp_path = self._temp_file_for(file_id)
        if not tmp_path:
            raise ValueError(f"Nessun dato trovato per '{file_id}'")
        try:
            use_layer = layer or file_source.layer
            use_sheet = sheet or file_source.sheet
            return self._load_by_format(tmp_path, file_source.format, use_layer, use_sheet)
        finally:
            try:
                _rmtree_safe(tmp_path)
            except Exception:
                pass  # non critico

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
                df, gdf = self.load_dataframe(file_id)
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
                data=self._get_blob_from_db(file_id) or b"",
                metadata=metadata,
            )
        return file_source

    def get_layers_info(self, file_id: str) -> Dict[str, Any]:
        """Ottieni info su layers/sheets — estrae blob in temp."""
        file_source = self.get_file_source(file_id)
        if not file_source:
            raise ValueError(f"File source '{file_id}' non trovato")
        layers = []
        sheets = []
        if file_source.format in ['gdb', 'gpkg', 'xlsx']:
            tmp_path = self._temp_file_for(file_id)
            if tmp_path:
                try:
                    if file_source.format in ['gdb', 'gpkg']:
                        layers = self.list_layers(tmp_path)
                    elif file_source.format == 'xlsx':
                        sheets = self.list_sheets(tmp_path)
                finally:
                    try:
                        import os
                        os.unlink(tmp_path)
                    except Exception:
                        pass
        if _USE_APP_MODELS:
            try:
                from app.models import FileSourceLayersResponse
                return FileSourceLayersResponse(file_id=file_id, format=file_source.format, layers=layers, sheets=sheets)
            except ImportError:
                pass
        return {"file_id": file_id, "format": file_source.format, "layers": layers, "sheets": sheets}

    def get_preview(self, file_id: str, limit: int = 100) -> Dict[str, Any]:
        """Restituisce anteprima dati (prime N righe)."""
        file_source = self.get_file_source(file_id)
        if not file_source:
            raise ValueError(f"File source '{file_id}' non trovato")
        df, gdf = self.load_dataframe(file_id)
        total_count = len(df)
        preview_df = df.head(limit)
        rows = preview_df.to_dict(orient='records')
        import numpy as np
        for row in rows:
            for key, value in row.items():
                if pd.isna(value):
                    row[key] = None
                elif isinstance(value, (np.integer, np.floating)):
                    row[key] = value.item()
                elif isinstance(value, np.bool_):
                    row[key] = bool(value)
        if _USE_APP_MODELS:
            try:
                from app.models import FileSourcePreview
                return FileSourcePreview(file_id=file_id, columns=file_source.columns, rows=rows, total_count=total_count, preview_count=len(rows))
            except ImportError:
                pass
        return {"file_id": file_id, "columns": [{"name": c.name, "type": c.type} for c in file_source.columns], "rows": rows, "total_count": total_count, "preview_count": len(rows)}

    def preview_file_temp(self, file_content: BinaryIO, filename: str, layer: str = None, sheet: str = None) -> dict:
        """
        Preview temporanea di un file prima dell'upload definitivo.
        
        - layer: carica un layer specifico (GPKG/GDB)
        - sheet: carica un foglio specifico (XLSX)
        - Se nessuno specificato su file multi-layer/sheet, restituisce la lista disponibile.
        """
        import tempfile
        import os
        
        format_type = self._detect_format(filename)
        
        if format_type == 'unknown':
            raise ValueError(f"Formato file non supportato: {filename}")
        
        suffix = Path(filename).suffix
        
        from app.config import settings
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=settings.TEMP_DIR) as tmp:
            import shutil
            shutil.copyfileobj(file_content, tmp)
            tmp_path = Path(tmp.name)
        
        tmp_dir = None
        
        try:
            columns = []
            row_count = 0
            
            if format_type == 'zip':
                from app.config import settings
                tmp_dir = Path(tempfile.mkdtemp(dir=settings.TEMP_DIR))
                self._fs.extract_zip(tmp_path, tmp_dir)
                
                for item in self._fs.iterdir(tmp_dir):
                    if self._fs.is_dir(item) and item.suffix.lower() == '.gdb':
                        format_type = 'gdb'
                        tmp_path = item
                        break
                    elif item.suffix.lower() == '.shp':
                        format_type = 'shp'
                        tmp_path = item
                        break
                    elif item.suffix.lower() == '.gpkg':
                        format_type = 'gpkg'
                        tmp_path = item
                        break
            
            # File multi-layer/sheet senza selezione: restituisce lista layers/sheets
            if format_type in ['gpkg', 'gdb'] and not layer:
                available_layers = self._loader.list_layers(tmp_path)
                if len(available_layers) > 1:
                    return {
                        "columns": [],
                        "row_count": 0,
                        "format": format_type,
                        "layers": available_layers,
                        "sheets": []
                    }
                if available_layers:
                    layer = available_layers[0]

            if format_type in ['xlsx', 'xls'] and not sheet:
                available_sheets = self._loader.list_sheets(tmp_path)
                if len(available_sheets) > 1:
                    return {
                        "columns": [],
                        "row_count": 0,
                        "format": format_type,
                        "layers": [],
                        "sheets": available_sheets
                    }
                if available_sheets:
                    sheet = available_sheets[0]

            # Carica con layer o sheet
            is_sheet = format_type in ['xlsx', 'xls']
            df, gdf = self._load_by_format(tmp_path, format_type, None if is_sheet else layer, sheet if is_sheet else None)
            columns = self._column_builder.from_dataframe(df, gdf)
            row_count = len(df)
            
            return {
                "columns": [{"name": c.name, "type": c.type} for c in columns],
                "row_count": row_count,
                "format": format_type,
                "layers": [],
                "sheets": []
            }
        
        finally:
            try:
                if tmp_path.exists():
                    _rmtree_safe(tmp_path)
            except Exception:
                pass  # non critico
            
            if tmp_dir and tmp_dir.exists():
                import shutil
                _rmtree_safe(tmp_dir)


# ==================== FACTORY ====================
# Costruisce FileService con tutte le dipendenze di produzione.
# Per creare istanze con dependency injection personalizzata (test, ambienti custom)

def create_file_service(
    filesystem: FileSystemProtocol = None,
    data_loader: DataLoaderProtocol = None,
    files_dir: Path = None
) -> FileSourceService:
    """Factory function per creare service con dipendenze iniettate."""
    return FileSourceService(
        filesystem=filesystem,
        data_loader=data_loader,
        files_dir=files_dir
    )


# ==================== SINGLETON PRODUZIONE ====================
# Istanza globale per uso da router FastAPI (evita ri-creazione ad ogni richiesta).
# Singleton per uso in produzione — mantiene compatibilità con codice esistente.
# Per test, usare create_file_service() con mock iniettati.
def _create_production_service() -> FileSourceService:
    """Crea service con configurazione di produzione"""
    try:
        from app.config import settings
        from app.utils.logger import get_logger
        logger = get_logger(__name__)
        return FileSourceService(
            files_dir=settings.FILES_DIR,
            logger=logger
        )
    except ImportError:
        # Fallback per test standalone
        return FileSourceService()


# Singleton globale per uso da router FastAPI
file_source_service: FileSourceService = _create_production_service()
