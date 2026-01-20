from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime

# ===== TIPI COLONNA =====
# Tipi supportati: int, real, str, bool, date
# Geometrie con formato: geometry:subtype:srid
# Esempi: geometry:point:4326, geometry:polygon:3003, geometry:multipolygon:4326
# Il subtype può essere: point, line, polygon, multipoint, multiline, multipolygon
# L'SRID è opzionale (es. geometry:polygon è valido)
ColumnType = Literal[
    "int", "real", "str", "bool", "date",
    "geometry", "geometry:point", "geometry:line", "geometry:polygon",
    "geometry:multipolygon", "geometry:multipoint", "geometry:multiline"
]

# ===== CATALOGO TABELLE (condiviso) =====
class ColumnInfo(BaseModel):
    """Informazioni base su una colonna dal database"""
    name: str
    type: str = "str"  # Default a string per retrocompatibilità

class TableCatalog(BaseModel):
    """Catalogo master di tutte le tabelle disponibili dal DB"""
    # AGGIORNATO: ora ogni colonna ha name e type
    tables: dict[str, List[ColumnInfo]]  # {"table_name": [{"name": "col1", "type": "int"}, ...]}

# ===== DATABASE CONNECTIONS =====
class DatabaseConnection(BaseModel):
    """Configurazione connessione database"""
    id: str
    name: str  # "Archibus Production"
    description: str = ""
    type: Literal["postgres", "mssql", "mysql", "oracle", "sqlite"]
    host: str
    port: int
    database: str
    username: str
    password: str  # TODO: encrypt in produzione
    created_at: datetime
    updated_at: datetime

class ConnectionCreate(BaseModel):
    """Dati per creare connessione"""
    name: str
    description: str = ""
    type: Literal["postgres", "mssql", "mysql", "oracle", "sqlite"]
    host: str
    port: int
    database: str
    username: str
    password: str

class ConnectionUpdate(BaseModel):
    """Dati per aggiornare connessione"""
    name: Optional[str] = None
    description: Optional[str] = None
    type: Optional[Literal["postgres", "mssql", "mysql", "oracle", "sqlite"]] = None
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

class ConnectionList(BaseModel):
    """Lista connessioni"""
    connections: List[DatabaseConnection]

class ConnectionTestResult(BaseModel):
    """Risultato test connessione"""
    success: bool
    message: str
    details: Optional[dict] = None


# ===== FILE SOURCE (NUOVO) =====
class FileSource(BaseModel):
    """
    Configurazione file esterno come sorgente dati.
    Usato in MANIPOLA per caricare CSV, Excel, GeoJSON, GDB, etc.
    """
    id: str
    name: str  # Nome descrittivo (es. "Catasto Comuni 2024")
    description: str = ""
    file_path: str  # Path relativo in data/files/{id}/
    original_filename: str  # Nome file originale
    format: Literal["csv", "xlsx", "json", "geojson", "gpkg", "shp", "gdb"]
    layer: Optional[str] = None  # Per GDB/GPKG con più layer
    sheet: Optional[str] = None  # Per XLSX con più fogli
    columns: List[ColumnInfo] = []  # Colonne rilevate
    row_count: int = 0
    file_size: int = 0  # bytes
    crs: Optional[str] = None  # Sistema di riferimento (es. "EPSG:4326")
    created_at: datetime
    updated_at: datetime


class FileSourceCreate(BaseModel):
    """Dati per creare un file source (dopo upload)"""
    name: str
    description: str = ""
    layer: Optional[str] = None  # Per GDB/GPKG
    sheet: Optional[str] = None  # Per XLSX


class FileSourceUpdate(BaseModel):
    """Dati per aggiornare un file source"""
    name: Optional[str] = None
    description: Optional[str] = None
    layer: Optional[str] = None
    sheet: Optional[str] = None


class FileSourceList(BaseModel):
    """Lista file source"""
    files: List[FileSource]


class FileSourceLayersResponse(BaseModel):
    """Risposta con lista layer/sheet disponibili in un file"""
    file_id: str
    format: str
    layers: List[str] = []  # Per GDB/GPKG
    sheets: List[str] = []  # Per XLSX


class FileSourcePreview(BaseModel):
    """Preview dati di un file source"""
    file_id: str
    columns: List[ColumnInfo]
    rows: List[dict]
    total_count: int
    preview_count: int


# ===== METADATA PROGETTO =====
class ProjectMetadata(BaseModel):
    id: str
    name: str
    description: str = ""
    # NUOVO: Specifica se è Estrai, Manipola, etc.
    project_type: Literal["estrai", "manipola", "arricchisci", "salva"] = "estrai"
    default_connection_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    # NUOVO: Default a estrai per compatibilità
    project_type: Literal["estrai", "manipola", "arricchisci", "salva"] = "estrai"
    default_connection_id: Optional[str] = None

class ProjectList(BaseModel):
    """Lista di progetti"""
    projects: List[ProjectMetadata]

# ===== CANVAS STATE =====
class Position(BaseModel):
    """Posizione di un nodo nel canvas"""
    x: float
    y: float

class Column(BaseModel):
    """Colonna di una tabella nel canvas"""
    name: str
    alias: Optional[str] = None
    visible: bool = True
    # NUOVO: tipo colonna per visualizzazione icone
    type: str = "str"  # Default a string per retrocompatibilità

class Filter(BaseModel):
    """Filtro WHERE su una colonna"""
    column: str
    operator: Literal[
        "=", "!=", ">", "<", ">=", "<=", 
        "LIKE", "NOT LIKE", 
        "IN", "NOT IN", 
        "IS NULL", "IS NOT NULL",
        "BETWEEN"
    ]
    value: str = ""

# ===== AGGREGATION CONFIG (per GROUP BY) =====
class AggregationConfig(BaseModel):
    """Configurazione singola aggregazione per GROUP BY"""
    column: str  # Nome colonna o "*" per COUNT(*)
    function: Literal["COUNT", "SUM", "AVG", "MIN", "MAX"] = "COUNT"
    alias: Optional[str] = None  # Alias output (es. "totale_area")

class Node(BaseModel):
    id: str
    type: Literal["source", "result", "project_source", "file_source", "transform", "api_enrich"]
    position: Position
    
    # Campi per nodi tabella (type="source") - Progetti ESTRAI
    table_name: Optional[str] = None
    alias: Optional[str] = None
    columns: List[Column] = []
    filters: List[Filter] = []
    calculated_columns: Optional[List[dict]] = []  # [{name, expression, sql, output_type, is_manual}]
    connection_id: Optional[str] = None
    
    # Campi per nodi progetto (type="project_source") - Progetti MANIPOLA/ARRICCHISCI
    source_project_id: Optional[str] = None
    
    # Campi per nodi file (type="file_source") - Progetti MANIPOLA (NUOVO)
    file_source_id: Optional[str] = None
    
    # Campi per nodi trasformazione (type="transform") - Progetti MANIPOLA
    transform_op: Optional[Literal[
        # Base
        "merge", "join", "concat", "filter", "aggregate", "formula",
        "sort", "distinct",  # ✅ Aggiunti
        # Spaziali
        "sjoin",  # Spatial join con GeoPandas
        # Geometriche
        "geo_centroid", "geo_buffer", "geo_area", "geo_length", 
        "geo_simplify", "geo_convex_hull", "geo_union", "geo_intersection",
        "geo_to_crs", "geo_clip",
        # Query DB
        "query_db",
        # Custom (futuro)
        "custom"
    ]] = None
    
    # Categoria trasformazione
    transform_category: Optional[Literal["base", "geometric", "query", "custom"]] = None
    
    # Campi per nodi API (type="api_enrich") - Progetti ARRICCHISCI
    api_id: Optional[str] = None
    param_mapping: Optional[dict] = None  # { param_name: column_name }
    
    # Dizionario generico per configurazioni specifiche
    data: Optional[dict] = {} 
    
    # ===== Campi per nodo risultato (type="result") - COMUNE =====
    fileName: Optional[str] = None
    format: Optional[Literal["json", "csv", "xlsx", "geojson", "gpkg", "shp", "gdb"]] = None
    distinct: Optional[bool] = False
    limit: Optional[int] = None
    geomColumn: Optional[str] = None  # Colonna geometria per export geospaziale (es: "poi.geometry")
    
    # ✅ NUOVO: Campi GROUP BY per ESTRAI
    group_by_enabled: Optional[bool] = False
    group_by_columns: Optional[List[str]] = []  # Colonne di raggruppamento
    aggregations: Optional[List[AggregationConfig]] = []  # Funzioni aggregate

class Edge(BaseModel):
    """Connessione (join) tra due nodi o verso output"""
    id: str
    source: str
    target: str
    join_type: Optional[Literal["left", "inner", "right", "outer", "output"]] = "left"
    source_column: Optional[str] = ""
    target_column: Optional[str] = ""
    
    # Campi per join spaziale
    is_spatial: Optional[bool] = False
    spatial_predicate: Optional[Literal[
        "ST_Intersects", "ST_Within", "ST_Contains", 
        "ST_DWithin", "ST_Touches", "ST_Overlaps"
    ]] = None
    source_geom_column: Optional[str] = None
    target_geom_column: Optional[str] = None
    spatial_distance: Optional[float] = None  # Per ST_DWithin

class CanvasState(BaseModel):
    """Stato completo del canvas"""
    nodes: List[Node] = []
    edges: List[Edge] = []

# ===== VALIDAZIONE =====
class ValidationError(BaseModel):
    """Errore di validazione"""
    node_id: Optional[str] = None
    edge_id: Optional[str] = None
    message: str
    severity: Literal["error", "warning"]

class ValidationResult(BaseModel):
    """Risultato validazione canvas"""
    valid: bool
    errors: List[ValidationError] = []
    
class ExecuteRequest(BaseModel):
    connection_id: Optional[str] = None
    force_recalculate: bool = False