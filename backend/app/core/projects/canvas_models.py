"""Pydantic models for project canvas state."""

from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, List, Optional, Literal


# TIPI COLONNA

ColumnType = Literal[
    "int", "real", "str", "bool", "date",
    "geometry", "geometry:point", "geometry:line", "geometry:polygon",
    "geometry:multipolygon", "geometry:multipoint", "geometry:multiline"
]


# INFO COLONNA

class ColumnInfo(BaseModel):
    """Informazioni di base su una colonna (nome e tipo)."""
    name: str
    type: str = "str"


# CANVAS — NODI E ARCHI

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
        "multi_join", "comment", "datasource_node", "imdf_publisher"
    ]
    position: Position

    @field_validator('type', mode='before')
    @classmethod
    def normalize_type(cls, v):
        """Normalizza nomi tipo camelCase dal frontend a snake_case."""
        _MAP = {'caricaArcgis': 'carica_arcgis', 'resultNode': 'result'}
        return _MAP.get(v, v)

    table_name: Optional[str] = None
    alias: Optional[str] = None
    columns: List[Column] = []
    filters: List[Filter] = []
    calculated_columns: Optional[List[dict]] = []
    connection_id: Optional[str] = None

    layer_name: Optional[str] = None
    layer_url: Optional[str] = None
    service_name: Optional[str] = None
    connection_name: Optional[str] = None
    geometry_type: Optional[str] = None
    feature_count: Optional[int] = None
    where: Optional[str] = None

    source_project_id: Optional[str] = None

    file_source_id: Optional[str] = None

    label: Optional[str] = None
    fields: Optional[List[dict]] = []

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

    api_id: Optional[str] = None
    api_name: Optional[str] = None
    param_mapping: Optional[Any] = None
    output_mapping: Optional[Any] = None
    static_parameters: Optional[Any] = None
    enrich_type: Optional[str] = None
    api_type: Optional[str] = None
    connection_type: Optional[str] = None
    exec_params: Optional[Any] = None
    endpoint: Optional[Any] = None
    parameters: Optional[Any] = None
    # rest_file (Processore File): config multipart in/binary out propagato
    # dalla connection al nodo del canvas (cosi' il runner conosce file_fields
    # static_fields, output_extension senza dover rifetcharli al runtime
    request: Optional[Any] = None
    response: Optional[Any] = None
    # File aggiuntivi caricati inline sul nodo (per connector rest_file con
    # file_fields source='extra_input'). Struttura
    # {field_name: {file_source_id, original_filename, size} | file_source_id}
    extra_files: Optional[Dict[str, Any]] = None
    # output_kind: tag esplicito sul nodo api_enrich (canvas-side) per indicare
    # che lo zip prodotto dall'upstream Processor File ha una struttura
    # riconosciuta. Il nodo `result` downstream legge questo tag e si specializza
    # es. modalita' "Pubblicazione IMDF"). La connection resta generica
    # Valori: 'generic' (default), 'imdf', ... (estendibile in futuro
    output_kind: Optional[str] = None
    from_catalog: Optional[bool] = False
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    provider_id: Optional[str] = None
    manifest: Optional[Any] = None
    batch: Optional[Any] = None
    continue_on_error: Optional[bool] = None
    skip_null_params: Optional[bool] = None
    max_errors: Optional[int] = None

    data: Optional[dict] = {}
    config: Optional[dict] = {}

    fileName: Optional[str] = None
    format: Optional[Literal["json", "csv", "xlsx", "geojson", "gpkg", "shp", "gdb", ""]] = None
    distinct: Optional[bool] = False
    limit: Optional[int] = None
    geomColumn: Optional[str] = None
    # Prende in ingresso un api_enrich con output_kind='imdf', legge il bundle
    # IMDF prodotto dal Processor File e chiama l'uploader (estrazione GDB +
    # creazione FeatureService ArcGIS + apply renderers). I campi sono sul Node
    # base perche' Pydantic non supporta variante di schema per type literal
    # La pubblicazione NON e' piu' automatica: l'utente la triggera dal nodo
    # dopo aver verificato il risultato dell'arricchimento
    imdf_arcgis_connection_id: Optional[str] = None
    imdf_arcgis_folder: Optional[str] = None
    imdf_fs_name_template: Optional[str] = None
    imdf_apply_renderers: Optional[bool] = False
    # Modalita' pubblicazione: 'replace' (default) | 'append' | 'upsert'
    imdf_publish_mode: Optional[Literal["replace", "append", "upsert"]] = "replace"

    group_by_enabled: Optional[bool] = False
    group_by_columns: Optional[List[str]] = []
    aggregations: Optional[List[AggregationConfig]] = []

    order_by_enabled: Optional[bool] = False
    order_by_columns: Optional[List[OrderByConfig]] = []

    having_enabled: Optional[bool] = False
    having_conditions: Optional[List[HavingCondition]] = []

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

    arcgis_connection_id: Optional[str] = None
    arcgis_service_url: Optional[str] = None
    arcgis_layer_url: Optional[str] = None
    arcgis_operation: Optional[Literal["add", "update", "upsert", "delete", "create_service"]] = None
    arcgis_folder_id: Optional[str] = None
    arcgis_match_column: Optional[str] = None
    arcgis_new_service_name: Optional[str] = None
    arcgis_new_layer_name: Optional[str] = None

    join_keys: Optional[List[str]] = []
    multi_join_type: Optional[Literal["inner", "left", "outer"]] = "inner"

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

    id/source/target sono obbligatori e stringhe non-vuote.
    sourceHandle/targetHandle sono metadata ReactFlow opzionali
    tollerati (il frontend li invia a volte con l'edge grezzo).

    Manteniamo deliberatamente il default
    Pydantic `extra="ignore"` invece di `extra="forbid"` raccomandato
    dal finding. Motivo: il serializer frontend al
    `frontend/src/utils/canvasSerializer.js:227` ritorna il raw edge
    ReactFlow quando `data.join_type` manca (es. edge verso result /
    carica). Quel raw edge porta con se' campi ReactFlow variabili
    (`type`, `animated`, `label`, `style`, `markerStart`, ...) che
    cambiano tra versioni di ReactFlow. Forbid spezzerebbe canvas
    legittimi ad ogni upgrade library. Trade-off: accettiamo il rischio
    minore di tollerare typo/field ignorati; la validazione strutturale
    (id/source/target/join_type tipizzati) copre il 90% della superficie.
    """
    id: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    target: str = Field(..., min_length=1)
    join_type: Optional[Literal[
        "left", "inner", "right", "outer", "cross",
        "output", "carica", "carica_arcgis", "imdf_publisher",
    ]] = "left"
    source_column: Optional[str] = ""
    target_column: Optional[str] = ""

    sourceHandle: Optional[str] = None
    targetHandle: Optional[str] = None

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


# VALIDAZIONE E RICHIESTE

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
