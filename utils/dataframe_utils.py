"""
Utility centralizzate per operazioni su DataFrame.

Questo modulo accorpa funzioni duplicate da:
- orchestrator_service.py
- api_connection_service.py
- query_executor.py
- file_source_service.py

USO:
    from utils.dataframe_utils import (
        load_dataframe_from_file,
        save_dataframe,
        filter_visible_columns,
        sanitize_for_json
    )
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
import json

from utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# CARICAMENTO DATAFRAME
# =============================================================================

def load_dataframe_from_file(
    file_path: Union[str, Path],
    sheet: Optional[str] = None,
    layer: Optional[str] = None
) -> pd.DataFrame:
    """
    Carica un DataFrame da file in vari formati.
    
    Supporta: CSV, XLSX, JSON, Parquet, GeoJSON, GPKG, SHP
    
    Args:
        file_path: Path del file da caricare
        sheet: Nome del foglio per file Excel (opzionale)
        layer: Nome del layer per file GDB/GPKG (opzionale)
        
    Returns:
        DataFrame caricato
        
    Raises:
        ValueError: Se il formato non è supportato
        FileNotFoundError: Se il file non esiste
    """
    file_path = Path(file_path)
    
    if not file_path.exists():
        raise FileNotFoundError(f"File non trovato: {file_path}")
    
    file_ext = file_path.suffix.lower()
    
    logger.debug(f"Caricamento file: {file_path.name} (formato: {file_ext})")
    
    try:
        if file_ext == '.csv':
            df = pd.read_csv(file_path)
            
        elif file_ext in ['.xlsx', '.xls']:
            if sheet:
                df = pd.read_excel(file_path, sheet_name=sheet)
            else:
                df = pd.read_excel(file_path)
                
        elif file_ext == '.json':
            df = pd.read_json(file_path, orient='records')
            
        elif file_ext == '.parquet':
            df = pd.read_parquet(file_path)
            
        elif file_ext in ['.geojson', '.gpkg', '.shp']:
            import geopandas as gpd
            if layer and file_ext == '.gpkg':
                df = gpd.read_file(file_path, layer=layer)
            else:
                df = gpd.read_file(file_path)
            
            if file_ext == '.shp':
                logger.warning("⚠️ Shapefile caricato - nomi colonne potrebbero essere troncati a 10 caratteri!")
                
        elif file_ext == '.gdb' or (file_path.is_dir() and file_path.suffix.lower() == '.gdb'):
            import geopandas as gpd
            if layer:
                df = gpd.read_file(file_path, layer=layer)
            else:
                # Prova a leggere il primo layer
                import fiona
                layers = fiona.listlayers(file_path)
                if layers:
                    df = gpd.read_file(file_path, layer=layers[0])
                else:
                    raise ValueError(f"Nessun layer trovato in {file_path}")
        else:
            # Fallback: prova come CSV
            logger.warning(f"Formato {file_ext} non riconosciuto, provo come CSV")
            df = pd.read_csv(file_path)
        
        logger.info(f"Caricato {file_path.name}: {len(df)} righe, {len(df.columns)} colonne")
        return df
        
    except Exception as e:
        logger.error(f"Errore caricamento {file_path}: {e}")
        raise


def find_latest_output_file(output_dir: Union[str, Path]) -> Optional[Path]:
    """
    Trova il file di output più recente in una directory.
    
    Args:
        output_dir: Directory dove cercare
        
    Returns:
        Path del file più recente, o None se non trovato
    """
    output_dir = Path(output_dir)
    
    if not output_dir.exists():
        return None
    
    files = [f for f in output_dir.iterdir() if f.is_file()]
    
    if not files:
        return None
    
    return max(files, key=lambda x: x.stat().st_mtime)


# =============================================================================
# SALVATAGGIO DATAFRAME
# =============================================================================

def save_dataframe(
    df: pd.DataFrame,
    output_path: Union[str, Path],
    format: str = 'csv',
    geom_column: Optional[str] = None,
    crs: str = "EPSG:4326"
) -> Path:
    """
    Salva un DataFrame in vari formati.
    
    Supporta: CSV, XLSX, JSON, Parquet, GeoJSON, GPKG, SHP
    
    Args:
        df: DataFrame da salvare
        output_path: Path di output (senza estensione, verrà aggiunta)
        format: Formato di output
        geom_column: Nome colonna geometria per formati geo (opzionale, auto-detect)
        crs: Sistema di riferimento per output geospaziali
        
    Returns:
        Path del file salvato
        
    Raises:
        ValueError: Se il formato non è supportato o manca geometria per formati geo
    """
    from utils.geo_utils import to_geodataframe, detect_geometry_column
    
    output_path = Path(output_path)
    
    # Assicura che la directory esista
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Aggiungi estensione se non presente
    if not output_path.suffix:
        output_path = output_path.with_suffix(f'.{format}')
    
    format_lower = format.lower()
    geo_formats = ['geojson', 'gpkg', 'shp']
    
    logger.debug(f"Salvataggio: {output_path.name} (formato: {format_lower})")
    
    try:
        if format_lower == 'csv':
            df.to_csv(output_path, index=False)
            
        elif format_lower == 'xlsx':
            df.to_excel(output_path, index=False)
            
        elif format_lower == 'json':
            # Sanitizza prima di salvare
            records = df.to_dict(orient='records')
            records = sanitize_for_json(records)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(records, f, indent=2, default=str, ensure_ascii=False)
                
        elif format_lower == 'parquet':
            df.to_parquet(output_path, index=False)
            
        elif format_lower in geo_formats:
            # Auto-detect colonna geometria se non specificata
            if geom_column is None:
                geom_column = detect_geometry_column(df)
            
            if geom_column is None:
                raise ValueError(
                    f"Nessuna colonna geometria trovata per formato {format_lower}. "
                    f"Colonne disponibili: {list(df.columns)}. "
                    f"Usa CSV o JSON per dati non spaziali."
                )
            
            # Converti a GeoDataFrame
            gdf = to_geodataframe(df, geom_column, crs=crs)
            
            # Salva nel formato richiesto
            if format_lower == 'geojson':
                gdf.to_file(output_path, driver='GeoJSON')
            elif format_lower == 'gpkg':
                gdf.to_file(output_path, driver='GPKG')
            elif format_lower == 'shp':
                gdf.to_file(output_path, driver='ESRI Shapefile')
                # Warning per limiti shapefile
                long_cols = [c for c in df.columns if len(c) > 10]
                if long_cols:
                    logger.warning(
                        f"⚠️ Shapefile: {len(long_cols)} colonne con nomi > 10 caratteri "
                        f"saranno troncate: {long_cols[:5]}..."
                    )
        else:
            raise ValueError(f"Formato '{format_lower}' non supportato")
        
        logger.info(f"Salvato: {output_path} ({len(df)} righe)")
        return output_path
        
    except Exception as e:
        logger.error(f"Errore salvataggio {output_path}: {e}")
        raise


# =============================================================================
# FILTRO COLONNE
# =============================================================================

def filter_visible_columns(
    df: pd.DataFrame,
    node_columns: List[Dict[str, Any]]
) -> pd.DataFrame:
    """
    Filtra le colonne di un DataFrame mantenendo solo quelle visibili.
    
    Args:
        df: DataFrame da filtrare
        node_columns: Lista di dict con 'name' e 'visible' (dal nodo canvas)
                     Es: [{'name': 'col1', 'visible': True}, {'name': 'col2', 'visible': False}]
    
    Returns:
        DataFrame con solo colonne visibili
    """
    if not node_columns:
        return df
    
    # Estrai nomi colonne visibili
    visible_columns = [
        col['name'] 
        for col in node_columns 
        if col.get('visible', True)
    ]
    
    if not visible_columns:
        return df
    
    # Mantieni solo colonne che esistono nel DataFrame
    available_cols = [col for col in visible_columns if col in df.columns]
    
    if not available_cols:
        logger.warning("Nessuna colonna visibile trovata nel DataFrame")
        return df
    
    if len(available_cols) < len(visible_columns):
        missing = set(visible_columns) - set(available_cols)
        logger.debug(f"Colonne visibili non trovate nel DataFrame: {missing}")
    
    logger.debug(f"Filtrate {len(available_cols)}/{len(df.columns)} colonne visibili")
    
    return df[available_cols]


def get_visible_column_names(node_columns: List[Dict[str, Any]]) -> List[str]:
    """
    Estrae i nomi delle colonne visibili dalla configurazione del nodo.
    
    Args:
        node_columns: Lista di dict con 'name' e 'visible'
        
    Returns:
        Lista di nomi colonne visibili
    """
    if not node_columns:
        return []
    
    return [
        col['name'] 
        for col in node_columns 
        if col.get('visible', True)
    ]


# =============================================================================
# SERIALIZZAZIONE JSON
# =============================================================================

def sanitize_for_json(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sanitizza una lista di dict per la serializzazione JSON.
    
    Converte:
    - NaN/None → None
    - numpy int/float → Python int/float
    - numpy bool → Python bool
    - Timestamp/datetime → stringa ISO
    
    Args:
        rows: Lista di dizionari da sanitizzare
        
    Returns:
        Lista di dizionari sanitizzati
    """
    for row in rows:
        for key, value in row.items():
            if pd.isna(value):
                row[key] = None
            elif isinstance(value, (np.integer,)):
                row[key] = int(value)
            elif isinstance(value, (np.floating,)):
                row[key] = float(value)
            elif isinstance(value, np.bool_):
                row[key] = bool(value)
            elif isinstance(value, (pd.Timestamp, np.datetime64)):
                row[key] = str(value)
            elif isinstance(value, bytes):
                # Prova a decodificare, altrimenti usa hex
                try:
                    row[key] = value.decode('utf-8')
                except:
                    row[key] = value.hex()
    
    return rows


def dataframe_to_json_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Converte un DataFrame in lista di dict JSON-safe.
    
    Args:
        df: DataFrame da convertire
        
    Returns:
        Lista di dizionari sanitizzati
    """
    records = df.to_dict(orient='records')
    return sanitize_for_json(records)


# =============================================================================
# UTILITY COLONNE
# =============================================================================

def infer_column_types(df: pd.DataFrame) -> List[Dict[str, str]]:
    """
    Inferisce i tipi delle colonne di un DataFrame.
    
    Args:
        df: DataFrame da analizzare
        
    Returns:
        Lista di dict con 'name' e 'type'
    """
    columns = []
    
    for col in df.columns:
        dtype = str(df[col].dtype).lower()
        
        if 'int' in dtype:
            col_type = 'int'
        elif 'float' in dtype or 'numeric' in dtype or 'decimal' in dtype or 'double' in dtype:
            col_type = 'real'
        elif 'bool' in dtype:
            col_type = 'bool'
        elif 'datetime' in dtype or 'timestamp' in dtype:
            col_type = 'date'
        elif 'date' in dtype:
            col_type = 'date'
        elif 'geometry' in dtype:
            col_type = 'geometry'
        elif 'object' in dtype:
            # Object può essere stringa o tipo misto - controlla i valori
            col_type = 'str'
        else:
            col_type = 'str'
        
        columns.append({'name': col, 'type': col_type})
    
    return columns


def get_column_from_node(node, attr_name: str, default=None):
    """
    Estrae un attributo da un nodo in modo sicuro.
    Gestisce sia oggetti Pydantic che dict.
    
    Args:
        node: Nodo (Pydantic model o dict)
        attr_name: Nome attributo da estrarre
        default: Valore di default se non trovato
        
    Returns:
        Valore dell'attributo o default
    """
    if hasattr(node, attr_name):
        return getattr(node, attr_name)
    elif isinstance(node, dict):
        return node.get(attr_name, default)
    elif hasattr(node, 'data') and node.data:
        return node.data.get(attr_name, default)
    return default