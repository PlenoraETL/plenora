"""
Service per gestione File Source (sorgenti dati da file).

Supporta: CSV, XLSX, JSON, GeoJSON, GeoPackage, Shapefile, FileGDB

USO:
    from services.file_source_service import FileSourceService
    
    service = FileSourceService()
    
    # Upload e crea file source
    file_source = service.create_from_upload(file, name="Catasto", layer="particelle")
    
    # Lista layers in un file GDB/GPKG
    layers = service.list_layers(file_id)
    
    # Carica dati come DataFrame
    df = service.load_dataframe(file_id)
"""

import json
import uuid
import shutil
import zipfile
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple, BinaryIO
import pandas as pd
import geopandas as gpd

from config import settings
from utils.logger import get_logger
from models import (
    FileSource,
    FileSourceCreate,
    FileSourceUpdate,
    FileSourceList,
    FileSourceLayersResponse,
    FileSourcePreview,
    ColumnInfo
)

logger = get_logger(__name__)


class FileSourceService:
    """Servizio per gestione file esterni come sorgenti dati."""
    
    def __init__(self):
        self.FILES_DIR = settings.FILES_DIR
    
    # ==================== ID E PATH ====================
    
    def _generate_file_id(self, name: str) -> str:
        """Genera ID univoco per file source."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '_')).strip()
        safe_name = safe_name.replace(' ', '_')[:30]
        return f"file_{safe_name}_{timestamp}_{short_uuid}"
    
    def _get_file_dir(self, file_id: str) -> Path:
        """Directory del file source."""
        return self.FILES_DIR / file_id
    
    def _get_metadata_path(self, file_id: str) -> Path:
        """Path del file metadata.json."""
        return self._get_file_dir(file_id) / "metadata.json"
    
    def _get_data_dir(self, file_id: str) -> Path:
        """Directory dove salvare i file dati."""
        path = self._get_file_dir(file_id) / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    # ==================== DETECT FORMAT ====================
    
    def _detect_format(self, filename: str) -> str:
        """Rileva formato file dall'estensione."""
        ext = Path(filename).suffix.lower()
        
        format_map = {
            '.csv': 'csv',
            '.xlsx': 'xlsx',
            '.xls': 'xlsx',
            '.json': 'json',
            '.geojson': 'geojson',
            '.gpkg': 'gpkg',
            '.shp': 'shp',
            '.gdb': 'gdb',
            '.zip': 'zip'  # Potrebbe essere SHP o GDB zippato
        }
        
        return format_map.get(ext, 'unknown')
    
    def _detect_geometry_type(self, gdf: gpd.GeoDataFrame) -> str:
        """Rileva tipo geometria da GeoDataFrame."""
        if gdf.empty or 'geometry' not in gdf.columns:
            return 'geometry'
        
        # Prendi il primo tipo non nullo
        geom_types = gdf.geometry.geom_type.dropna().unique()
        
        if len(geom_types) == 0:
            return 'geometry'
        
        geom_type = geom_types[0].lower()
        
        type_map = {
            'point': 'geometry:point',
            'multipoint': 'geometry:multipoint',
            'linestring': 'geometry:line',
            'multilinestring': 'geometry:multiline',
            'polygon': 'geometry:polygon',
            'multipolygon': 'geometry:multipolygon'
        }
        
        return type_map.get(geom_type, 'geometry')
    
    # ==================== INFER COLUMNS ====================
    
    def _infer_column_type(self, series: pd.Series) -> str:
        """Inferisce tipo colonna da pandas Series."""
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
            # Prova a capire se è una data da stringa
            if series.dropna().empty:
                return 'str'
            
            sample = str(series.dropna().iloc[0])
            
            # Check se sembra una data
            if len(sample) >= 8 and any(c in sample for c in ['-', '/']):
                try:
                    pd.to_datetime(series.dropna().head(10))
                    return 'date'
                except:
                    pass
            
            return 'str'
    
    def _get_columns_info(self, df: pd.DataFrame, gdf: gpd.GeoDataFrame = None) -> List[ColumnInfo]:
        """Estrae info colonne da DataFrame."""
        columns = []
        
        for col in df.columns:
            col_type = self._infer_column_type(df[col])
            
            # Se è un GeoDataFrame e la colonna è geometry, aggiungi info CRS
            if gdf is not None and col == 'geometry':
                geom_type = self._detect_geometry_type(gdf)
                if gdf.crs:
                    srid = gdf.crs.to_epsg() or 4326
                    col_type = f"{geom_type}:{srid}"
                else:
                    col_type = geom_type
            
            columns.append(ColumnInfo(name=col, type=col_type))
        
        return columns
    
    # ==================== LIST LAYERS/SHEETS ====================
    
    def list_layers(self, file_path: Path) -> List[str]:
        """Lista layer in un file GDB o GPKG."""
        try:
            import fiona
            layers = fiona.listlayers(str(file_path))
            return layers
        except Exception as e:
            logger.error(f"Errore listing layers: {e}")
            return []
    
    def list_sheets(self, file_path: Path) -> List[str]:
        """Lista fogli in un file Excel."""
        try:
            xlsx = pd.ExcelFile(file_path)
            return xlsx.sheet_names
        except Exception as e:
            logger.error(f"Errore listing sheets: {e}")
            return []
    
    # ==================== LOAD DATA ====================
    
    def _load_csv(self, file_path: Path) -> pd.DataFrame:
        """Carica CSV con auto-detect separatore."""
        # Prova prima con virgola, poi punto e virgola
        try:
            df = pd.read_csv(file_path, encoding='utf-8')
            if len(df.columns) == 1 and ';' in df.columns[0]:
                df = pd.read_csv(file_path, sep=';', encoding='utf-8')
            return df
        except UnicodeDecodeError:
            # Prova con latin-1
            return pd.read_csv(file_path, encoding='latin-1')
    
    def _load_xlsx(self, file_path: Path, sheet: str = None) -> pd.DataFrame:
        """Carica Excel."""
        if sheet:
            return pd.read_excel(file_path, sheet_name=sheet)
        return pd.read_excel(file_path)
    
    def _load_json(self, file_path: Path) -> pd.DataFrame:
        """Carica JSON."""
        # Prova come array di oggetti
        try:
            return pd.read_json(file_path, orient='records')
        except:
            # Prova come oggetto con chiavi
            return pd.read_json(file_path)
    
    def _load_geospatial(self, file_path: Path, layer: str = None) -> gpd.GeoDataFrame:
        """Carica file geospaziale (GeoJSON, GPKG, SHP, GDB)."""
        if layer:
            return gpd.read_file(file_path, layer=layer)
        return gpd.read_file(file_path)
    
    def load_dataframe(
        self, 
        file_id: str, 
        layer: str = None, 
        sheet: str = None
    ) -> Tuple[pd.DataFrame, Optional[gpd.GeoDataFrame]]:
        """
        Carica dati da file source come DataFrame.
        
        Returns:
            Tuple (DataFrame, GeoDataFrame o None se non geospaziale)
        """
        # Carica metadata
        file_source = self.get_file_source(file_id)
        if not file_source:
            raise ValueError(f"File source '{file_id}' non trovato")
        
        # Costruisci path completo
        data_dir = self._get_data_dir(file_id)
        
        # Trova il file dati
        data_files = list(data_dir.iterdir())
        if not data_files:
            raise ValueError(f"Nessun file dati trovato per '{file_id}'")
        
        # Per GDB è una cartella
        file_path = None
        for f in data_files:
            if f.suffix.lower() == '.gdb' or f.is_dir():
                if f.name.endswith('.gdb'):
                    file_path = f
                    break
            else:
                file_path = f
                break
        
        if not file_path:
            file_path = data_files[0]
        
        # Usa layer/sheet dal parametro o dal metadata
        use_layer = layer or file_source.layer
        use_sheet = sheet or file_source.sheet
        
        format_type = file_source.format
        
        logger.info(f"Caricamento file {file_id}: format={format_type}, layer={use_layer}, sheet={use_sheet}")
        
        # Carica in base al formato
        gdf = None
        
        if format_type == 'csv':
            df = self._load_csv(file_path)
        
        elif format_type == 'xlsx':
            df = self._load_xlsx(file_path, use_sheet)
        
        elif format_type == 'json':
            df = self._load_json(file_path)
        
        elif format_type in ['geojson', 'gpkg', 'shp', 'gdb']:
            gdf = self._load_geospatial(file_path, use_layer)
            df = pd.DataFrame(gdf)
            # Converti geometry in WKT per compatibilità
            if 'geometry' in df.columns:
                df['geometry'] = gdf.geometry.apply(lambda g: g.wkt if g else None)
        
        else:
            raise ValueError(f"Formato '{format_type}' non supportato")
        
        logger.info(f"Caricato: {len(df)} righe, {len(df.columns)} colonne")
        
        return df, gdf
    
    # ==================== CRUD ====================
    
    def create_from_upload(
        self,
        file_content: BinaryIO,
        filename: str,
        name: str,
        description: str = "",
        layer: str = None,
        sheet: str = None
    ) -> FileSource:
        """
        Crea file source da upload.
        
        Args:
            file_content: Contenuto file (binary)
            filename: Nome file originale
            name: Nome descrittivo
            description: Descrizione
            layer: Layer da usare (per GDB/GPKG)
            sheet: Sheet da usare (per XLSX)
        
        Returns:
            FileSource creato
        """
        # Genera ID
        file_id = self._generate_file_id(name)
        
        # Crea directory
        file_dir = self._get_file_dir(file_id)
        file_dir.mkdir(parents=True, exist_ok=True)
        data_dir = self._get_data_dir(file_id)
        
        # Rileva formato
        format_type = self._detect_format(filename)
        
        # Salva file
        saved_path = data_dir / filename
        
        with open(saved_path, 'wb') as f:
            shutil.copyfileobj(file_content, f)
        
        file_size = saved_path.stat().st_size
        
        logger.info(f"File salvato: {saved_path} ({file_size} bytes)")
        
        # Se è uno ZIP, estrai
        if format_type == 'zip':
            format_type, saved_path = self._handle_zip(saved_path, data_dir)
        
        # Lista layers/sheets se necessario
        available_layers = []
        available_sheets = []
        
        if format_type in ['gdb', 'gpkg']:
            available_layers = self.list_layers(saved_path)
            logger.info(f"Layers disponibili: {available_layers}")
            
            # Se non specificato layer e ce n'è solo uno, usalo
            if not layer and len(available_layers) == 1:
                layer = available_layers[0]
            elif not layer and len(available_layers) > 1:
                # Serve selezione utente
                pass
        
        elif format_type == 'xlsx':
            available_sheets = self.list_sheets(saved_path)
            logger.info(f"Sheets disponibili: {available_sheets}")
            
            if not sheet and len(available_sheets) == 1:
                sheet = available_sheets[0]
        
        # Carica dati per inferire colonne
        columns = []
        row_count = 0
        crs = None
        
        try:
            if format_type == 'csv':
                df = self._load_csv(saved_path)
                columns = self._get_columns_info(df)
                row_count = len(df)
            
            elif format_type == 'xlsx':
                df = self._load_xlsx(saved_path, sheet)
                columns = self._get_columns_info(df)
                row_count = len(df)
            
            elif format_type == 'json':
                df = self._load_json(saved_path)
                columns = self._get_columns_info(df)
                row_count = len(df)
            
            elif format_type in ['geojson', 'gpkg', 'shp', 'gdb']:
                # Per geo, serve il layer
                if layer or format_type in ['geojson', 'shp']:
                    gdf = self._load_geospatial(saved_path, layer)
                    columns = self._get_columns_info(pd.DataFrame(gdf), gdf)
                    row_count = len(gdf)
                    if gdf.crs:
                        crs = str(gdf.crs)
        
        except Exception as e:
            logger.warning(f"Errore inferenza colonne: {e}")
        
        # Crea metadata
        now = datetime.now()
        
        file_source = FileSource(
            id=file_id,
            name=name,
            description=description,
            file_path=str(saved_path.relative_to(self.FILES_DIR)),
            original_filename=filename,
            format=format_type,
            layer=layer,
            sheet=sheet,
            columns=columns,
            row_count=row_count,
            file_size=file_size,
            crs=crs,
            created_at=now,
            updated_at=now
        )
        
        # Salva metadata
        with open(self._get_metadata_path(file_id), 'w', encoding='utf-8') as f:
            f.write(file_source.model_dump_json(indent=2))
        
        logger.info(f"FileSource creato: {file_id}")
        
        return file_source
    
    def _handle_zip(self, zip_path: Path, data_dir: Path) -> Tuple[str, Path]:
        """
        Gestisce file ZIP (potrebbe contenere SHP o GDB).
        
        Returns:
            Tuple (formato rilevato, path file estratto)
        """
        logger.info(f"Estrazione ZIP: {zip_path}")
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(data_dir)
        
        # Rimuovi lo zip originale
        zip_path.unlink()
        
        # Cerca cosa c'è dentro
        extracted = list(data_dir.iterdir())
        
        # Cerca .gdb (cartella)
        for item in extracted:
            if item.is_dir() and item.suffix.lower() == '.gdb':
                return 'gdb', item
        
        # Cerca .shp
        for item in extracted:
            if item.suffix.lower() == '.shp':
                return 'shp', item
        
        # Cerca .gpkg
        for item in extracted:
            if item.suffix.lower() == '.gpkg':
                return 'gpkg', item
        
        # Cerca ricorsivamente in sottocartelle
        for item in extracted:
            if item.is_dir():
                for subitem in item.rglob('*.gdb'):
                    if subitem.is_dir():
                        return 'gdb', subitem
                for subitem in item.rglob('*.shp'):
                    return 'shp', subitem
        
        raise ValueError("ZIP non contiene file supportati (SHP, GDB, GPKG)")
    
    def list_file_sources(self) -> FileSourceList:
        """Lista tutti i file source."""
        files = []
        
        for file_dir in self.FILES_DIR.iterdir():
            if not file_dir.is_dir():
                continue
            
            metadata_path = file_dir / "metadata.json"
            if not metadata_path.exists():
                continue
            
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    file_source = FileSource.model_validate_json(f.read())
                    files.append(file_source)
            except Exception as e:
                logger.error(f"Errore caricamento file source {file_dir.name}: {e}")
        
        # Ordina per data (più recenti prima)
        files.sort(key=lambda f: f.updated_at, reverse=True)
        
        return FileSourceList(files=files)
    
    def get_file_source(self, file_id: str) -> Optional[FileSource]:
        """Ottieni file source specifico."""
        metadata_path = self._get_metadata_path(file_id)
        
        if not metadata_path.exists():
            return None
        
        with open(metadata_path, 'r', encoding='utf-8') as f:
            return FileSource.model_validate_json(f.read())
    
    def update_file_source(self, file_id: str, data: FileSourceUpdate) -> Optional[FileSource]:
        """Aggiorna file source."""
        file_source = self.get_file_source(file_id)
        
        if not file_source:
            return None
        
        # Aggiorna campi
        update_data = data.model_dump(exclude_unset=True)
        
        for field, value in update_data.items():
            setattr(file_source, field, value)
        
        # Se cambia layer/sheet, ricarica info colonne
        if 'layer' in update_data or 'sheet' in update_data:
            try:
                df, gdf = self.load_dataframe(file_id)
                file_source.columns = self._get_columns_info(df, gdf)
                file_source.row_count = len(df)
            except Exception as e:
                logger.warning(f"Errore aggiornamento colonne: {e}")
        
        file_source.updated_at = datetime.now()
        
        # Salva
        with open(self._get_metadata_path(file_id), 'w', encoding='utf-8') as f:
            f.write(file_source.model_dump_json(indent=2))
        
        return file_source
    
    def delete_file_source(self, file_id: str) -> bool:
        """Elimina file source e tutti i suoi file."""
        file_dir = self._get_file_dir(file_id)
        
        if not file_dir.exists():
            return False
        
        # Elimina ricorsivamente
        shutil.rmtree(file_dir)
        
        logger.info(f"FileSource eliminato: {file_id}")
        
        return True
    
    def get_layers_info(self, file_id: str) -> FileSourceLayersResponse:
        """Ottieni info su layers/sheets disponibili."""
        file_source = self.get_file_source(file_id)
        
        if not file_source:
            raise ValueError(f"File source '{file_id}' non trovato")
        
        data_dir = self._get_data_dir(file_id)
        
        # Trova il file
        data_files = list(data_dir.iterdir())
        if not data_files:
            return FileSourceLayersResponse(
                file_id=file_id,
                format=file_source.format,
                layers=[],
                sheets=[]
            )
        
        file_path = data_files[0]
        
        # Per GDB cerca la cartella .gdb
        for f in data_files:
            if f.suffix.lower() == '.gdb' or (f.is_dir() and f.name.endswith('.gdb')):
                file_path = f
                break
        
        layers = []
        sheets = []
        
        if file_source.format in ['gdb', 'gpkg']:
            layers = self.list_layers(file_path)
        elif file_source.format == 'xlsx':
            sheets = self.list_sheets(file_path)
        
        return FileSourceLayersResponse(
            file_id=file_id,
            format=file_source.format,
            layers=layers,
            sheets=sheets
        )
    
    def get_preview(self, file_id: str, limit: int = 100) -> FileSourcePreview:
        """Ottieni preview dati."""
        file_source = self.get_file_source(file_id)
        
        if not file_source:
            raise ValueError(f"File source '{file_id}' non trovato")
        
        df, gdf = self.load_dataframe(file_id)
        
        total_count = len(df)
        preview_df = df.head(limit)
        
        # Converti a lista di dict
        rows = preview_df.to_dict(orient='records')
        
        # Gestisci valori non serializzabili
        import numpy as np
        for row in rows:
            for key, value in row.items():
                if pd.isna(value):
                    row[key] = None
                elif isinstance(value, (np.integer, np.floating)):
                    row[key] = value.item()
                elif isinstance(value, np.bool_):
                    row[key] = bool(value)
        
        return FileSourcePreview(
            file_id=file_id,
            columns=file_source.columns,
            rows=rows,
            total_count=total_count,
            preview_count=len(rows)
        )


# Singleton
file_source_service = FileSourceService()
