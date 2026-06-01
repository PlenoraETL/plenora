"""Schema inference across project canvas graphs."""
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple
import uuid

from app.core.projects.project_context_loader import (
    load_project_source_schema_context,
)
from app.core.projects.schema_transform_handlers import TransformSchemaHandlers
from app.core.projects.types import (
    EdgesByTarget,
    JsonObject,
    SchemaMap,
    SchemaRows,
    StringMap,
    TransformHandlerRegistry,
)
from app.shared.execution.schema_types import ColumnSchema, InferredSchema
from app.utils.logger import get_logger

logger = get_logger(__name__)


SCALAR_COLUMN_CONFIG_KEYS = frozenset({
    "aggregate_column",
    "by_column",
    "column",
    "geom_column",
    "geometry_column",
    "group_by",
    "group_column",
    "id_column",
    "index_col",
    "input_geom",
    "lat_column",
    "left_geom",
    "left_geom_column",
    "left_on",
    "lon_column",
    "mask_geom",
    "old_name",
    "order_column",
    "partition_column",
    "pivot_col",
    "point_geom_column",
    "polygon_geom_column",
    "right_geom",
    "right_geom_column",
    "right_on",
    "source_column",
    "stratify_column",
    "target_column",
    "value_col",
    "wkt_column",
})

LIST_COLUMN_CONFIG_KEYS = frozenset({
    "column_order",
    "columns",
    "compare_columns",
    "group_by",
    "group_by_columns",
    "id_columns",
    "join_keys",
    "left_keys",
    "right_keys",
    "subset",
    "value_columns",
})


def _stable_column_id(*parts: str) -> str:
    return f"col_{uuid.uuid5(uuid.NAMESPACE_URL, ':'.join(parts)).hex}"


@dataclass(frozen=True)
class _NodeSchemaContext:
    node: Any
    nodes_by_id: dict
    edges_by_target: dict
    project_id: str


class SchemaInferenceService:
    """Infers column schemas through a canvas node graph."""
    
    def __init__(self, project_service):
        self.project_service = project_service
        self._transform_handlers = TransformSchemaHandlers(ColumnSchema)

        self._schema_cache: Dict[str, List[ColumnSchema]] = {}
        # node_id -> (status, reason). Popolato in parallelo a _schema_cache.
        self._status_cache: Dict[str, Tuple[str, str]] = {}
    
    def infer_all_schemas(self, project_id: str) -> SchemaMap:
        """
        Calcola lo schema per tutti i nodi di un progetto.
        
        Returns:
            Dict mapping node_id -> lista di colonne con tipo
        """
        logger.info(f"Inferenza schema per progetto: {project_id}")

        self._schema_cache = {}
        self._status_cache = {}

        canvas = self.project_service.get_project_canvas(project_id)
        if not canvas:
            logger.warning(f"Canvas non trovato per {project_id}")
            return {}
        
        nodes_by_id = {n.id: n for n in canvas.nodes}
        edges_by_target = self._group_edges_by_target(canvas.edges)

        result = {}
        for node in canvas.nodes:
            schema = self._infer_node_schema(
                node=node,
                nodes_by_id=nodes_by_id,
                edges_by_target=edges_by_target,
                project_id=project_id
            )
            result[node.id] = [col.to_dict() for col in schema]
        
        logger.info(f"Schema inferito per {len(result)} nodi")
        return result
    
    def infer_node_schema(self, project_id: str, node_id: str) -> SchemaRows:
        """
        Calcola lo schema per un singolo nodo.
        Utile per aggiornamenti incrementali.
        """
        self._schema_cache = {}
        self._status_cache = {}

        canvas = self.project_service.get_project_canvas(project_id)
        if not canvas:
            return []
        
        nodes_by_id = {n.id: n for n in canvas.nodes}
        edges_by_target = self._group_edges_by_target(canvas.edges)
        
        node = nodes_by_id.get(node_id)
        if not node:
            logger.warning(f"Nodo {node_id} non trovato")
            return []
        
        schema = self._infer_node_schema(
            node=node,
            nodes_by_id=nodes_by_id,
            edges_by_target=edges_by_target,
            project_id=project_id
        )
        return [col.to_dict() for col in schema]

    def infer_node_schema_typed(self, project_id: str, node_id: str) -> InferredSchema:
        """Come ``infer_node_schema`` ma con esito esplicito (``InferredSchema``).

        Faccia tipizzata per l'engine di validazione (Epic Canvas Validation):
        condivide la STESSA computazione del metodo pubblico (``.columns`` ==
        ``infer_node_schema(...)``) e vi aggiunge lo ``status``. Nessuna
        duplicazione di logica → niente drift.
        """
        rows = self.infer_node_schema(project_id, node_id)
        # `infer_node_schema` popola `_status_cache` per il nodo (e i suoi
        # antenati) durante il calcolo. Canvas/nodo assente (134/142) → il nodo
        # non è calcolato → default `certain` (problema strutturale, preso altrove).
        status, reason = self._status_cache.get(node_id, ("certain", ""))
        return InferredSchema(columns=rows, status=status, reason=reason)

    def _infer_node_schema(
        self,
        node,
        nodes_by_id: dict,
        edges_by_target: dict,
        project_id: str
    ) -> List[ColumnSchema]:
        """
        Inferisce lo schema di output per un nodo.
        Ricorsivamente risolve le dipendenze.
        """
        if node.id in self._schema_cache:
            return self._schema_cache[node.id]
        
        schema: List[ColumnSchema] = []

        context = _NodeSchemaContext(
            node=node,
            nodes_by_id=nodes_by_id,
            edges_by_target=edges_by_target,
            project_id=project_id,
        )
        node_type_handler = self._build_node_type_handlers().get(node.type)
        if node_type_handler is not None:
            schema = node_type_handler(context)

        self._record_node_status(node, context)
        self._schema_cache[node.id] = schema
        return schema

    def _record_node_status(self, node, context: _NodeSchemaContext) -> None:
        """Determina (status, reason) per il nodo, dopo il calcolo dello schema.

        Precedenza:
        1. reason specifica già registrata da un handler critico
           (project_source 307/312/321, result 260) → vince;
        2. **un qualsiasi upstream indeterminate → indeterminate**, anche con
           schema NON-vuoto: i combinatori multi-input (join/cross_join/
           table_diff/concat/sjoin/geo_overlay/geo_nearest) producono uno schema
           *parziale* quando un input è indeterminate (mancano le sue colonne);
           marcarlo ``certain`` causerebbe un FALSO BLOCCO sui ref a valle. Il
           check va PRIMA di guardare ``schema`` (regola centrale: copre tutti i
           combinatori presenti e futuri, niente fix per-handler);
        3. altrimenti ``certain`` (colonne presenti, o vuoto dichiarato/edge).
        """
        if node.id in self._status_cache:
            return  # reason specifica già fissata dall'handler critico
        inherited = self._inherited_indeterminate(node, context.edges_by_target)
        if inherited is not None:
            self._status_cache[node.id] = inherited
            return
        self._status_cache[node.id] = ("certain", "")

    def _inherited_indeterminate(
        self, node, edges_by_target: dict
    ) -> Optional[Tuple[str, str]]:
        """``('indeterminate', reason)`` se un nodo a monte è indeterminate."""
        for edge in edges_by_target.get(node.id, []):
            src = getattr(edge, "source", None)
            if src and self._status_cache.get(src, ("certain", ""))[0] == "indeterminate":
                return (
                    "indeterminate",
                    f"schema a monte non risolvibile (nodo '{src}')",
                )
        return None

    def _build_node_type_handlers(
        self,
    ) -> TransformHandlerRegistry:
        """Registry node.type -> schema handler.

        All node.type-specific inference is registered here. Keep
        `_infer_node_schema` as a registry lookup so new node types do not
        grow central branches.
        """
        # NODE-DISPATCH-REGISTRY:start
        handlers = {
            "source": self._infer_source_node_schema,
            "file_source": self._infer_file_source_node_schema,
            "project_source": self._infer_project_source_node_schema,
            "transform": self._infer_transform_node_schema,
            "api_enrich": self._infer_api_enrich_node_schema,
            "result": self._infer_result_node_schema,
        }
        # NODE-DISPATCH-REGISTRY:end
        return handlers

    def _infer_source_node_schema(
        self,
        context: _NodeSchemaContext,
    ) -> List[ColumnSchema]:
        return self._infer_source_schema(context.node)

    def _infer_file_source_node_schema(
        self,
        context: _NodeSchemaContext,
    ) -> List[ColumnSchema]:
        return self._infer_file_source_schema(context.node)

    def _infer_project_source_node_schema(
        self,
        context: _NodeSchemaContext,
    ) -> List[ColumnSchema]:
        return self._infer_project_source_schema(context.node)

    def _infer_transform_node_schema(
        self,
        context: _NodeSchemaContext,
    ) -> List[ColumnSchema]:
        input_schemas = self._get_input_schemas(
            context.node.id,
            context.nodes_by_id,
            context.edges_by_target,
            context.project_id,
        )
        return self._infer_transform_schema(
            context.node,
            input_schemas,
            context.edges_by_target,
        )

    def _infer_api_enrich_node_schema(
        self,
        context: _NodeSchemaContext,
    ) -> List[ColumnSchema]:
        input_schemas = self._get_input_schemas(
            context.node.id,
            context.nodes_by_id,
            context.edges_by_target,
            context.project_id,
        )
        return self._infer_api_enrich_schema(context.node, input_schemas)

    def _infer_result_node_schema(
        self,
        context: _NodeSchemaContext,
    ) -> List[ColumnSchema]:
        input_schemas = self._get_input_schemas(
            context.node.id,
            context.nodes_by_id,
            context.edges_by_target,
            context.project_id,
        )
        if input_schemas:
            return input_schemas[0]
        self._status_cache[context.node.id] = (
            "indeterminate",
            "result senza input risolvibile a monte",
        )
        return []

    def _infer_source_schema(self, node) -> List[ColumnSchema]:
        """
        Schema per nodo source (tabella database).
        Legge le colonne direttamente dal nodo.
        """
        columns = []
        
        node_columns = node.columns or []
        for col in node_columns:
            if col is None or not getattr(col, "visible", True):
                continue
            columns.append(ColumnSchema(
                name=col.alias or col.name,
                type=col.type or "str",
                id=getattr(col, "id", None) or _stable_column_id(node.id, col.name),
                source_name=col.name,
                source_node=node.id,
                is_calculated=False
            ))

        calculated = node.calculated_columns or []
        for calc in calculated:
            if not isinstance(calc, dict):
                continue
            columns.append(ColumnSchema(
                name=calc.get("name", "calculated"),
                type=calc.get("output_type", "str"),
                source_name=calc.get("name", "calculated"),
                source_node=node.id,
                is_calculated=True
            ))
        
        return columns
    
    def _infer_project_source_schema(self, node) -> List[ColumnSchema]:
        """
        Schema per nodo project_source.
        Legge esclusivamente l'output_contract pubblico del progetto sorgente.
        """
        source_project_id = None
        
        source_project_id = node.source_project_id or (node.data.get("source_project_id") if node.data else None)
        
        if not source_project_id:
            logger.warning(f"Nodo {node.id} senza source_project_id")
            self._status_cache[node.id] = (
                "indeterminate",
                "project_source senza source_project_id (nodo incompleto)",
            )
            return []

        source_context = load_project_source_schema_context(source_project_id)
        if not source_context:
            logger.warning(f"Metadata non trovato per progetto {source_project_id}")
            self._status_cache[node.id] = (
                "indeterminate",
                f"metadata del progetto sorgente '{source_project_id}' non risolvibile",
            )
            return []

        try:
            output_contract = source_context.output_contract
            if not isinstance(output_contract, dict):
                logger.warning(
                    f"output_contract mancante per project_source {source_project_id}. "
                    "Schema vuoto."
                )
                self._status_cache[node.id] = (
                    "indeterminate",
                    f"output_contract del sorgente '{source_project_id}' assente "
                    "(progetto non ancora eseguito?)",
                )
                return []
            contract_columns = [
                c for c in (output_contract.get("columns") or [])
                if isinstance(c, dict) and c.get("status", "active") == "active"
            ]
            node_columns = node.columns or (node.data.get("columns") if node.data else None) or []
            visible_ids = set()
            visible_names = set()
            if node_columns:
                for raw_col in node_columns:
                    col = raw_col
                    if not isinstance(col, dict):
                        col = {
                            "id": getattr(raw_col, "id", None),
                            "column_id": getattr(raw_col, "column_id", None),
                            "name": getattr(raw_col, "name", None),
                            "source_name": getattr(raw_col, "source_name", None),
                            "display_name": getattr(raw_col, "display_name", None),
                            "visible": getattr(raw_col, "visible", True),
                        }
                    if not col.get("visible", True):
                        continue
                    col_id = col.get("id") or col.get("column_id")
                    if col_id:
                        visible_ids.add(str(col_id))
                    for name_key in ("source_name", "name", "display_name"):
                        name = col.get(name_key)
                        if name:
                            visible_names.add(str(name))

            columns = []
            for col in contract_columns:
                col_id = col.get("id")
                col_name = col.get("physical_name") or col.get("display_name") or col.get("source_name") or ""

                if visible_ids and str(col_id) not in visible_ids:
                    continue
                if (
                    not visible_ids
                    and visible_names
                    and str(col.get("source_name") or "") not in visible_names
                    and str(col_name) not in visible_names
                    and str(col.get("display_name") or "") not in visible_names
                ):
                    continue

                columns.append(ColumnSchema(
                    name=col_name,
                    type=(col.get("type") or {}).get("logical", "string"),
                    id=col_id,
                    source_name=col.get("source_name") or col_name,
                    source_node=node.id,
                    is_calculated=False
                ))

            return columns
            
        except Exception as e:
            logger.error(f"[CRASH] Errore lettura metadata {source_project_id}: {e}", exc_info=True)
            return []
    
    def _infer_file_source_schema(self, node) -> List[ColumnSchema]:
        """
        Schema per nodo file_source (file esterno GeoJSON, GPKG, SHP, CSV).
        Legge le colonne da node.data.columns.
        """
        columns = []
        
        node_columns = node.columns or (node.data.get("columns") if node.data else None) or []
        if node_columns:
            for col in node_columns:
                if col.get("visible", True):
                    columns.append(ColumnSchema(
                        name=col.get("name", ""),
                        type=col.get("type", "str"),
                        id=col.get("id") or col.get("column_id"),
                        source_name=col.get("source_name") or col.get("name", ""),
                        source_node=node.id,
                        is_calculated=False
                    ))
        
        return columns
    
    def _infer_transform_schema(
        self, 
        node, 
        input_schemas: List[List[ColumnSchema]],
        edges_by_target: dict
    ) -> List[ColumnSchema]:
        """
        Schema per nodo transform.
        Dipende dal tipo di operazione.
        """
        config = node.config or (node.data.get("config", {}) if node.data else {})
        transform_op = node.transform_op or (node.data.get("transform_op") if node.data else None)
        config = self._resolve_schema_config_column_refs(config, input_schemas)

        if not transform_op:
            logger.warning(f"Transform {node.id} senza transform_op")
            return input_schemas[0] if input_schemas else []

        handlers = self._build_transform_handlers(edges_by_target)
        handler = handlers.get(transform_op)
        if handler is None:
            logger.warning(f"Operazione {transform_op} non gestita - passthrough")
            return self._transform_handlers.schema_passthrough(input_schemas, node.id)
        return handler(input_schemas, config, node.id)

    def _resolve_schema_config_column_refs(
        self,
        config: JsonObject,
        input_schemas: List[List[ColumnSchema]],
    ) -> JsonObject:
        if not isinstance(config, dict):
            return config
        id_to_name = {
            str(getattr(col, "id", "")): col.name
            for schema in input_schemas
            for col in schema
            if getattr(col, "id", None)
        }
        if not id_to_name:
            return config

        out = dict(config)
        for key, value in list(out.items()):
            if key.endswith("_column_id"):
                base = key[:-10]
                if base in out and value:
                    out[base] = id_to_name.get(str(value), out[base])
            elif key.endswith("_column_ids"):
                base = key[:-11]
                if base in out and isinstance(value, list) and value:
                    current_values = out.get(base)
                    if not isinstance(current_values, list):
                        current_values = []
                    out[base] = [
                        id_to_name.get(str(column_id), current_values[idx] if idx < len(current_values) else None)
                        for idx, column_id in enumerate(value)
                    ]

        for key, value in list(out.items()):
            if not isinstance(value, list):
                continue
            if key in {"aggregations", "aggs"}:
                out[key] = [
                    self._resolve_schema_dict_ref(item, id_to_name, "column")
                    for item in value
                ]
            elif key == "renames":
                out[key] = [
                    self._resolve_schema_dict_ref(item, id_to_name, "old_name", "old_name_id")
                    for item in value
                ]
            elif key == "maskings":
                out[key] = [
                    self._resolve_schema_dict_ref(item, id_to_name, "column")
                    for item in value
                ]

        return out

    @staticmethod
    def _resolve_schema_dict_ref(
        item: Any,
        id_to_name: StringMap,
        name_field: str,
        id_field: str = "column_id",
    ) -> Any:
        if not isinstance(item, dict):
            return item
        out = dict(item)
        column_id = out.get(id_field)
        if column_id and str(column_id) in id_to_name:
            out[name_field] = id_to_name[str(column_id)]
        return out

    def _build_transform_handlers(
        self,
        edges_by_target: dict,
    ) -> TransformHandlerRegistry:
        return self._transform_handlers.build_transform_handlers(edges_by_target)

    def _infer_api_enrich_schema(
        self,
        node,
        input_schemas: List[List[ColumnSchema]]
    ) -> List[ColumnSchema]:
        """
        Schema per nodo api_enrich.
        Input + campi aggiunti dall'API.
        """
        base_schema = input_schemas[0] if input_schemas else []
        result = [ColumnSchema(
            name=col.name,
            type=col.type,
            id=col.id,
            source_name=col.source_name,
            source_node=node.id,
            is_calculated=col.is_calculated
        ) for col in base_schema]

        # output_mapping is canonical; tolerate historical frontend aliases.
        output_mapping = node.data.get("output_mapping", []) if node.data else []
        existing = {col.name for col in result}
        for mapping in output_mapping:
            if not isinstance(mapping, dict):
                continue
            name = (
                mapping.get("column")
                or mapping.get("target_column")
                or mapping.get("name")
            )
            if not name or name in existing:
                continue
            result.append(ColumnSchema(
                name=name,
                type=mapping.get("type", "str"),
                source_name=name,
                source_node=node.id,
                is_calculated=True,
            ))
            existing.add(name)

        return result
    
    def _get_input_schemas(
        self,
        node_id: str,
        nodes_by_id: dict,
        edges_by_target: dict,
        project_id: str
    ) -> List[List[ColumnSchema]]:
        """
        Recupera gli schemi dei nodi in input a un nodo.
        """
        incoming_edges = edges_by_target.get(node_id, [])
        
        input_schemas = []
        for edge in incoming_edges:
            if not getattr(edge, "source", None):
                continue
            source_node = nodes_by_id.get(edge.source)
            if source_node:
                schema = self._infer_node_schema(
                    node=source_node,
                    nodes_by_id=nodes_by_id,
                    edges_by_target=edges_by_target,
                    project_id=project_id
                )
                input_schemas.append(schema)
        
        return input_schemas
    
    def _group_edges_by_target(self, edges) -> EdgesByTarget:
        """
        Raggruppa gli edge per nodo target.
        """
        result = {}
        for edge in edges:
            if edge.target not in result:
                result[edge.target] = []
            result[edge.target].append(edge)
        return result


class ConnectionValidator:
    """Validates node connections against inferred schemas."""
    
    def __init__(self, schema_service: SchemaInferenceService):
        self.schema_service = schema_service
    
    def validate_connection(
        self, 
        project_id: str,
        source_node_id: str,
        target_node_id: str,
        connection_config: dict = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Valida una connessione tra due nodi.
        
        Returns:
            (is_valid, error_message)
        """
        schemas = self.schema_service.infer_all_schemas(project_id)
        
        source_schema = schemas.get(source_node_id, [])
        
        if not source_schema:
            return False, f"Nodo sorgente {source_node_id} non ha schema"
        
        if connection_config:
            if "left_on" in connection_config:
                left_col = connection_config.get("left_on")
                
                source_cols = {c["name"] for c in source_schema}
                
                if left_col and left_col not in source_cols:
                    return False, f"Colonna '{left_col}' non trovata nel nodo sorgente"
        
        return True, None
    
    def validate_all_connections(self, project_id: str) -> SchemaRows:
        """
        Valida tutte le connessioni di un progetto.
        
        Returns:
            Lista di errori [{edge_id, message, severity}]
        """
        canvas = self.schema_service.project_service.get_project_canvas(project_id)
        if not canvas:
            return []
        
        errors = []
        schemas = self.schema_service.infer_all_schemas(project_id)
        
        for edge in canvas.edges:
            source_schema = schemas.get(edge.source, [])
            
            if not source_schema:
                errors.append({
                    "edge_id": edge.id,
                    "message": f"Nodo sorgente '{edge.source}' non ha colonne",
                    "severity": "error"
                })
                continue
            
            if edge.source_column:
                source_cols = {c["name"] for c in source_schema}
                if edge.source_column not in source_cols:
                    errors.append({
                        "edge_id": edge.id,
                        "message": f"Colonna '{edge.source_column}' non trovata",
                        "severity": "error"
                    })
        
        return errors
