"""
Modelli condivisi del canvas URBY-ETL.

Contiene i modelli Pydantic "core" usati trasversalmente da tutti i moduli:
- Tipi colonna e info base (ColumnType, ColumnInfo)
- Canvas state (Position, Column, Filter, AggregationConfig, Node, Edge, CanvasState)
- Validazione e richieste (ValidationError, ValidationResult, ExecuteRequest)

I modelli specifici di dominio (Connection, FileSource, Project, etc.)
risiedono nei rispettivi moduli sotto app/core/<modulo>/models.py.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Any, Union, Dict, List, Optional, Literal
from datetime import datetime


# =============================================================================
# TIPI COLONNA
# =============================================================================

ColumnType = Literal[
    "int", "real", "str", "bool", "date",
    "geometry", "geometry:point", "geometry:line", "geometry:polygon",
    "geometry:multipolygon", "geometry:multipoint", "geometry:multiline"
]


# =============================================================================
# INFO COLONNA
# =============================================================================

class ColumnInfo(BaseModel):
    """Informazioni di base su una colonna (nome e tipo)."""
    name: str
    type: str = "str"


# =============================================================================
# CANVAS — NODI E ARCHI
# =============================================================================

class Position(BaseModel):
    """Posizione (x, y) di un nodo nel canvas visuale."""
    x: float
    y: float


class Column(BaseModel):
    """Colonna di una tabella/dataset nel canvas."""
    name: str
    alias: Optional[str] = None
    visible: bool = True
    type: str = "str"

    def __getitem__(self, key: str):
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)


class Filter(BaseModel):
    """Filtro WHERE applicato a una colonna nel canvas."""
    column: str
    operator: Literal[
        "=", "!=", ">", "<", ">=", "<=",
        "LIKE", "NOT LIKE",
        "IN", "NOT IN",
        "IS NULL", "IS NOT NULL",
        "BETWEEN"
    ]
    value: str = ""


class AggregationConfig(BaseModel):
    """Configurazione di una funzione di aggregazione per GROUP BY."""
    column: str
    function: Literal["COUNT", "SUM", "AVG", "MIN", "MAX"] = "COUNT"
    alias: Optional[str] = None


class OrderByConfig(BaseModel):
    """Configurazione di una colonna per ORDER BY."""
    column: str
    direction: Literal["ASC", "DESC"] = "ASC"


class HavingCondition(BaseModel):
    """Condizione HAVING per filtrare risultati aggregati."""
    function: Literal["COUNT", "SUM", "AVG", "MIN", "MAX"] = "COUNT"
    column: str = "*"
    operator: Literal["=", "!=", ">", "<", ">=", "<="] = ">"
    value: str = "0"


class Node(BaseModel):
    """
    Nodo nel canvas visuale del progetto.

    Tipologie supportate:
        - source: tabella database (progetti ESTRAI)
        - feature_source: layer ArcGIS Feature Service (progetti ESTRAI)
        - result: nodo di output con formato e opzioni export
        - project_source: riferimento a un altro progetto (MANIPOLA/ARRICCHISCI)
        - file_source: file esterno caricato (MANIPOLA)
        - transform: operazione di trasformazione (MANIPOLA)
        - api_enrich: arricchimento via API esterna (ARRICCHISCI)
        - staticValues: valori statici con supporto variabili template
        - comment: annotazione/nota visuale nel canvas (non influenza il flusso dati)
    """
    id: str
    type: Literal[
        "source", "result", "project_source", "file_source", "feature_source",
        "transform", "api_enrich", "staticValues", "carica", "carica_arcgis",
        "multi_join", "comment", "datasource_node"
    ]
    position: Position

    @field_validator('type', mode='before')
    @classmethod
    def normalize_type(cls, v):
        """Normalizza nomi tipo camelCase dal frontend a snake_case."""
        _MAP = {'caricaArcgis': 'carica_arcgis', 'resultNode': 'result'}
        return _MAP.get(v, v)

    # --- Nodo tabella (type="source") — Progetti ESTRAI ---
    table_name: Optional[str] = None
    alias: Optional[str] = None
    columns: List[Column] = []
    filters: List[Filter] = []
    calculated_columns: Optional[List[dict]] = []
    connection_id: Optional[str] = None

    # --- Nodo Feature Service (type="feature_source") — Progetti ESTRAI ---
    layer_name: Optional[str] = None
    layer_url: Optional[str] = None
    service_name: Optional[str] = None
    connection_name: Optional[str] = None
    geometry_type: Optional[str] = None
    feature_count: Optional[int] = None
    where: Optional[str] = None

    # --- Nodo progetto sorgente (type="project_source") — MANIPOLA/ARRICCHISCI ---
    source_project_id: Optional[str] = None

    # --- Nodo file sorgente (type="file_source") — MANIPOLA ---
    file_source_id: Optional[str] = None

    # --- Nodo valori statici (type="staticValues") — Tutti i progetti ---
    label: Optional[str] = None
    fields: Optional[List[dict]] = []

    # --- Nodo trasformazione (type="transform") — MANIPOLA ---
    collapsed: Optional[bool] = False
    transform_op: Optional[Literal[
        # Base
        "merge", "join", "concat", "filter", "aggregate", "formula",
        "sort", "distinct", "rename", "drop_columns", "reorder_columns",
        "fill_na", "replace", "split_column", "concat_columns",
        "pivot", "conditional", "type_cast", "text_normalize",
        "md5_hash", "mask_data",
        # Spatial
        "sjoin",
        "geo_centroid", "geo_buffer", "geo_area", "geo_length",
        "geo_simplify", "geo_convex_hull", "geo_union", "geo_intersection",
        "geo_to_crs", "geo_clip", "geo_dissolve", "geo_difference",
        "geo_reproject", "geo_envelope", "geo_from_coords",
        # Other
        "table_diff", "query_db", "custom"
    ]] = None
    transform_category: Optional[Literal["base", "geometric", "query", "custom"]] = None

    # --- Nodo API (type="api_enrich") — ARRICCHISCI ---
    api_id: Optional[str] = None
    api_name: Optional[str] = None
    param_mapping: Optional[Any] = None
    output_mapping: Optional[Any] = None
    static_parameters: Optional[Any] = None
    enrich_type: Optional[str] = None
    api_type: Optional[str] = None
    connection_type: Optional[str] = None
    executable: Optional[Any] = None  # B-BE-33: oggetto config {type, path, input_mode, ...}
    exec_params: Optional[Any] = None
    endpoint: Optional[Any] = None
    parameters: Optional[Any] = None
    from_catalog: Optional[bool] = False
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    provider_id: Optional[str] = None
    manifest: Optional[Any] = None
    batch: Optional[Any] = None
    continue_on_error: Optional[bool] = None
    skip_null_params: Optional[bool] = None
    max_errors: Optional[int] = None

    # --- Dizionario generico per configurazioni specifiche del nodo ---
    data: Optional[dict] = {}
    config: Optional[dict] = {}

    # --- Nodo risultato (type="result") — COMUNE A TUTTI I MODULI ---
    fileName: Optional[str] = None
    format: Optional[Literal["json", "csv", "xlsx", "geojson", "gpkg", "shp", "gdb", ""]] = None
    distinct: Optional[bool] = False
    limit: Optional[int] = None
    geomColumn: Optional[str] = None

    # --- GROUP BY (per ESTRAI) ---
    group_by_enabled: Optional[bool] = False
    group_by_columns: Optional[List[str]] = []
    aggregations: Optional[List[AggregationConfig]] = []

    # --- ORDER BY (per ESTRAI) ---
    order_by_enabled: Optional[bool] = False
    order_by_columns: Optional[List[OrderByConfig]] = []

    # --- HAVING (per ESTRAI, con GROUP BY) ---
    having_enabled: Optional[bool] = False
    having_conditions: Optional[List[HavingCondition]] = []

    # --- Nodo CARICA (type="carica") ---
    dest_type: Optional[Literal["db", "arcgis"]] = None
    schema_name: Optional[str] = None
    write_mode: Optional[Literal["create", "replace", "append", "upsert", "update", "truncate_insert"]] = "create"
    column_mappings: Optional[List[dict]] = []
    match_columns: Optional[List[str]] = []
    batch_size: Optional[int] = 1000
    transaction_mode: Optional[Literal["all_or_nothing", "per_batch"]] = "all_or_nothing"
    create_spatial_index: Optional[bool] = True
    conflict_policy: Optional[Literal["abort", "skip", "update"]] = "abort"
    create_unique_index: Optional[bool] = False
    unique_key_column: Optional[str] = None

    # --- Nodo CARICA ArcGIS (type="carica_arcgis") ---
    arcgis_connection_id: Optional[str] = None
    arcgis_service_url: Optional[str] = None
    arcgis_layer_url: Optional[str] = None
    arcgis_operation: Optional[Literal["add", "update", "upsert", "delete", "create_service"]] = None
    arcgis_folder_id: Optional[str] = None
    arcgis_match_column: Optional[str] = None
    arcgis_new_service_name: Optional[str] = None
    arcgis_new_layer_name: Optional[str] = None

    # --- Nodo MULTI-JOIN (type="multi_join") ---
    join_keys: Optional[List[str]] = []
    multi_join_type: Optional[Literal["inner", "left", "outer"]] = "inner"

    # --- Nodo DATA SOURCE (type="datasource_node") — ESTRAI/MANIPOLA ---
    mode: Optional[Literal["source", "carica"]] = None
    datasource_id: Optional[str] = None
    datasource_name: Optional[str] = None
    file_mode: Optional[Literal["fixed", "dynamic"]] = None
    file_path: Optional[str] = None
    folder_path: Optional[str] = None
    dynamic_rule: Optional[Literal["latest", "latest_pattern", "latest_unprocessed"]] = None
    file_offset: Optional[int] = 0
    pattern: Optional[str] = None
    parse_options: Optional[dict] = {}
    dest_folder: Optional[str] = None
    dest_filename: Optional[str] = None
    dest_format: Optional[Literal["csv", "xlsx", "geojson", "gpkg"]] = None

    # --- Nodo annotazione (type="comment") ---
    text: Optional[str] = None
    title: Optional[str] = None
    color: Optional[str] = None
    width: Optional[float] = None
    height: Optional[float] = None
    level: Optional[str] = None
    arrowStyle: Optional[dict] = None


class Edge(BaseModel):
    """
    Arco (connessione) tra due nodi nel canvas.

    Rappresenta una relazione JOIN tra tabelle o il collegamento verso
    il nodo di output.
    """
    id: str
    source: str
    target: str
    join_type: Optional[Literal["left", "inner", "right", "outer", "output", "carica", "carica_arcgis"]] = "left"
    source_column: Optional[str] = ""
    target_column: Optional[str] = ""
    
    join_conditions: Optional[List[Dict[str, str]]] = None
    on_filters: Optional[List[Dict[str, str]]] = None

    # Campi per JOIN spaziale
    is_spatial: Optional[bool] = False
    spatial_predicate: Optional[Literal[
        "ST_Intersects", "ST_Within", "ST_Contains",
        "ST_DWithin", "ST_Touches", "ST_Overlaps"
    ]] = None
    source_geom_column: Optional[str] = None
    target_geom_column: Optional[str] = None
    spatial_distance: Optional[float] = None


class CanvasState(BaseModel):
    """Stato completo del canvas di un progetto."""
    nodes: List[Node] = []
    edges: List[Edge] = []


# =============================================================================
# VALIDAZIONE E RICHIESTE
# =============================================================================

class ValidationError(BaseModel):
    """Singolo errore o warning rilevato durante la validazione del canvas."""
    node_id: Optional[str] = None
    edge_id: Optional[str] = None
    message: str
    severity: Literal["error", "warning"]


class ValidationResult(BaseModel):
    """Risultato della validazione di un canvas."""
    valid: bool
    errors: List[ValidationError] = []


class ExecuteRequest(BaseModel):
    """Richiesta di esecuzione di una pipeline."""
    connection_id: Optional[str] = None
    force_recalculate: bool = False