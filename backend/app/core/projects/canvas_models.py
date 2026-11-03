"""Pydantic models for project canvas state."""

from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.shared.execution.canvas_types import (
    ConflictPolicyLiteral,
    DestTypeLiteral,
    MultiJoinTypeLiteral,
    NodeTypeLiteral,
    RestBatchModeLiteral,
    RestMethodLiteral,
    TransactionModeLiteral,
    WriteModeLiteral,
)
from app.shared.execution.canvas_column import Column as _Column
from app.utils.logger import get_logger

_canvas_logger = get_logger(__name__)


# Set di field "noti ma extra" che NON devono triggerare il warning di
# divergenza FE/BE: il FE ReactFlow li serializza come decorazioni
# visuali (selezione, dragging, posizione assoluta) o come metadata che
# il backend volontariamente ignora. Mantenere stretto: ogni nuovo
# campo qui dovrebbe avere un commento sul perche' non sta sul modello.
_NODE_KNOWN_VISUAL_EXTRAS: frozenset[str] = frozenset({
    # ReactFlow-runtime fields (auto-populated dal FE, niente semantica BE)
    "selected", "dragging", "measured", "positionAbsolute", "zIndex",
    "sourcePosition", "targetPosition",
    # Camp tipicamente echoed-back che il FE riceve in lettura e rinvia
    # come parte dei dati node ma che non hanno effetto runtime sul BE
    "data",  # bag generico FE-only (la chiave "config" gia' c'e' sul modello)
})


# TIPI COLONNA

ColumnType = Literal[
    "int", "real", "str", "bool", "date",
    "geometry", "geometry:point", "geometry:line", "geometry:polygon",
    "geometry:multipolygon", "geometry:multipoint", "geometry:multiline"
]


# CANVAS — NODI E ARCHI

class Position(BaseModel):
    """Posizione (x, y) di un nodo nel canvas visuale."""
    x: float
    y: float


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
        - project_input: input file/scalare valorizzato a runtime da un eseguibile
        - comment: annotazione/nota visuale nel canvas (non influenza il flusso dati)
    """

    # Preserva campi FE non ancora dichiarati sul modello e li rende visibili
    # al validator sotto, evitando drop silenziosi durante il runtime.
    # `extra="forbid"` resta troppo rigido per campi FE nuovi non ancora
    # tipizzati sul backend.
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def _warn_on_unknown_extras(self) -> "Node":
        extra = self.__pydantic_extra__ or {}
        if extra:
            unknown = sorted(set(extra) - _NODE_KNOWN_VISUAL_EXTRAS)
            if unknown:
                _canvas_logger.warning(
                    "[canvas-contract] Node id=%r type=%r riceve campi non "
                    "dichiarati sul modello: %s. Aggiungerli al Pydantic Node "
                    "(con il tipo corretto) per evitare drop silenziosi al "
                    "boundary FE->BE.",
                    self.id, self.type, unknown,
                )
        return self
    # (Definizione campi sotto.)
    id: str
    type: NodeTypeLiteral
    position: Position

    @field_validator('type', mode='before')
    @classmethod
    def normalize_type(cls, v):
        """Normalizza nomi tipo camelCase dal frontend a snake_case."""
        _MAP = {'caricaArcgis': 'carica_arcgis', 'resultNode': 'result'}
        return _MAP.get(v, v)

    table_name: Optional[str] = None
    alias: Optional[str] = None
    columns: List[_Column] = []
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
    # ArcGIS feature_source: lista relazioni dichiarate sul layer
    # (cardinalita', child layer id, ecc.). Il FE le serializza dal
    # nodo, il BE le accettava silenziosamente droppate via
    # extra='ignore' (catturato dal contract test post-`extra=allow`).
    relationships: Optional[List[dict]] = []

    source_project_id: Optional[str] = None
    source_freshness: Optional[dict] = None

    file_source_id: Optional[str] = None

    # Input pubblico di progetto. Viene risolto prima del dispatch sia nella
    # normale esecuzione sia nel publisher/runtime degli eseguibili.
    input_key: Optional[str] = None
    input_kind: Optional[Literal[
        "file", "text", "integer", "number", "boolean", "date",
        "datetime", "select", "connection"
    ]] = None
    connection_set_id: Optional[str] = None
    required: Optional[bool] = True
    default_value: Optional[Any] = None
    multiple: Optional[bool] = False
    expected_columns: Optional[List[dict]] = []
    validation: Optional[dict] = {}
    bindings: Optional[List[dict]] = []
    order: Optional[int] = 0

    label: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[List[dict]] = []

    collapsed: Optional[bool] = False
    # Allineato al `transforms_registry.json` (base 35 + spatial 33 +
    # privacy 2 = 70 op). Pre-allineamento il Literal accettava solo
    # ~30 nomi e la UI permetteva di selezionare op del registry
    # (es. `cross_join`, `lookup`, `geo_distance`) che facevano fallire
    # il PUT canvas con 422 "Dati richiesta non validi", anche se il
    # `plugin_executor` runtime le sa eseguire. La lista qui sotto
    # tiene anche `merge`/`geo_to_crs`/`query_db`/`custom` (orfani
    # rispetto al registry) per backward-compat con canvas legacy.
    transform_op: Optional[Literal[
        # Base (registry: 35)
        "merge", "join", "concat", "filter", "aggregate", "formula",
        "sort", "distinct", "rename", "drop_columns", "reorder_columns", "select_columns",
        "fill_na", "replace", "split_column", "concat_columns",
        "pivot", "conditional", "type_cast", "text_normalize",
        "md5_hash", "mask_data",
        "cross_join", "sample", "melt", "add_row_number", "date_extract",
        "lookup", "bin", "flatten_json", "statistics", "string_extract",
        "transpose", "string_pad", "string_length", "dedup_advanced",
        "window_function", "uuid_generator",
        # Spatial (registry: 33)
        "sjoin",
        "geo_centroid", "geo_buffer", "geo_area", "geo_length",
        "geo_simplify", "geo_convex_hull", "geo_union", "geo_intersection",
        "geo_to_crs", "geo_clip", "geo_dissolve", "geo_difference",
        "geo_reproject", "geo_envelope", "geo_from_coords",
        "geo_boundary", "geo_bounds_extractor", "geo_clean_topology",
        "geo_count_points_in_polygons", "geo_distance", "geo_explode",
        "geo_line_builder", "geo_make_valid", "geo_nearest", "geo_overlay",
        "geo_perimeter", "geo_point_on_surface", "geo_polygon_builder",
        "geo_symmetric_difference", "geo_to_wkt", "geo_vertex_count",
        "geo_voronoi", "geo_within",
        # Other / orfani backward-compat (non presenti nel registry corrente
        # ma referenziati da canvas legacy salvati prima del refactor)
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
    # api_enrich: condizione opzionale di esecuzione del nodo. Quando
    # valorizzata (es. un'espressione boolean valutata sui dati a
    # monte), il runtime salta il nodo se la condizione e' falsa.
    # Catturato dal contract test post-`extra=allow`.
    execute_if: Optional[Any] = None

    data: Optional[dict] = {}
    config: Optional[dict] = {}

    fileName: Optional[str] = None
    format: Optional[Literal["json", "csv", "xlsx", "geojson", "gpkg", "shp", "gdb", ""]] = None
    distinct: Optional[bool] = False
    limit: Optional[int] = None
    geomColumn: Optional[str] = None

    output_columns: Optional[List[dict]] = None

    group_by_enabled: Optional[bool] = False
    group_by_columns: Optional[List[str]] = []
    aggregations: Optional[List[AggregationConfig]] = []

    order_by_enabled: Optional[bool] = False
    order_by_columns: Optional[List[OrderByConfig]] = []

    having_enabled: Optional[bool] = False
    having_conditions: Optional[List[HavingCondition]] = []

    dest_type: Optional[DestTypeLiteral] = None
    schema_name: Optional[str] = None
    write_mode: Optional[WriteModeLiteral] = "create"
    column_mappings: Optional[List[dict]] = []
    match_columns: Optional[List[str]] = []
    update_columns: Optional[List[str]] = []
    batch_size: Optional[int] = 1000
    transaction_mode: Optional[TransactionModeLiteral] = "all_or_nothing"
    create_spatial_index: Optional[bool] = True
    conflict_policy: Optional[ConflictPolicyLiteral] = "abort"
    if_table_exists: Optional[Literal["error", "replace"]] = "error"
    create_unique_index: Optional[bool] = False
    unique_key_column: Optional[str] = None
    allow_unsafe_append: Optional[bool] = False

    arcgis_connection_id: Optional[str] = None
    arcgis_service_url: Optional[str] = None
    arcgis_layer_url: Optional[str] = None
    arcgis_operation: Optional[Literal["add", "update", "upsert", "delete", "create_service"]] = None
    arcgis_folder_id: Optional[str] = None
    arcgis_match_column: Optional[str] = None
    arcgis_new_service_name: Optional[str] = None
    arcgis_new_layer_name: Optional[str] = None
    # SRID sorgente dei dati in ingresso (EPSG). Letto da carica_task per la
    # scrittura ArcGIS (mono e multi-layer); None -> il writer assume 4326.
    source_srid: Optional[int] = None

    # CARICA REST sink. Il FE (canvasSerializer.js) li serializza ai
    # top-level del nodo carica con `dest_type=rest`; senza il typing
    # qui Pydantic li droppava silenziosamente al PUT canvas, lasciando
    # il task carica con "CARICA REST url is required".
    rest_url: Optional[str] = None
    rest_method: Optional[RestMethodLiteral] = None
    rest_headers: Optional[List[dict]] = []
    rest_body_mapping: Optional[List[dict]] = []
    rest_batch_mode: Optional[RestBatchModeLiteral] = None
    rest_batch_size: Optional[int] = None
    rest_timeout_seconds: Optional[int] = None
    rest_success_statuses: Optional[List[int]] = None

    join_keys: Optional[List[str]] = []
    join_key_ids: Optional[List[str]] = []
    join_pairs: Optional[List[Dict[str, str]]] = []
    left_keys: Optional[List[str]] = []
    right_keys: Optional[List[str]] = []
    multi_join_type: Optional[MultiJoinTypeLiteral] = "inner"

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
    dest_format: Optional[Literal["csv", "xlsx", "json", "geojson", "gpkg"]] = None

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

    Manteniamo deliberatamente il default Pydantic `extra="ignore"`
    invece di `extra="forbid"`. Motivo: il serializer frontend al
    `frontend/src/utils/canvasSerializer.js:227` ritorna il raw edge
    ReactFlow quando `data.join_type` manca (es. edge verso result /
    carica). Quel raw edge porta con se' campi ReactFlow variabili
    (`type`, `animated`, `label`, `style`, `markerStart`, ...) che
    cambiano tra versioni di ReactFlow. Forbid spezzerebbe canvas
    legittimi ad ogni upgrade library. Trade-off: accettiamo il rischio
    minore di tollerare typo/field ignorati; la validazione strutturale
    (id/source/target/join_type tipizzati) copre la superficie critica.
    """
    id: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    target: str = Field(..., min_length=1)
    # `full` e' un alias di `outer` (FULL OUTER JOIN). Il FE
    # `JoinModal` espone "FULL" come value `'full'`; il SQL
    # generator `core/estrai/query_joins.py:108-113` accetta sia
    # FULL che OUTER e li mappa entrambi a `FULL OUTER`. Senza
    # `'full'` qui il PUT canvas rispondeva 422 e l'utente vedeva
    # il modale "errore salvataggio" dopo aver scelto FULL nella
    # JoinModal.
    join_type: Optional[Literal[
        "left", "inner", "right", "outer", "full", "cross",
        "output", "carica", "carica_arcgis",
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
        "ST_DWithin", "ST_Touches", "ST_Overlaps",
        "ST_Crosses", "ST_Equals", "ST_Disjoint",
        "ST_Covers", "ST_CoveredBy",
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
    inputs: Dict[str, Any] = Field(default_factory=dict)
