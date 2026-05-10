"""
Servizio di inferenza e propagazione schema attraverso il grafo canvas.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import json
from typing import Callable, Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from app.core.projects.project_context_loader import (
    load_project_source_schema_context,
)
from app.core.projects.schema_transform_handlers import TransformSchemaHandlers
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ColumnSchema:
    """Schema di una singola colonna"""
    name: str
    type: str  # int, real, str, bool, date, geometry:point:4326, etc
    source_node: Optional[str] = None  # ID del nodo da cui proviene
    is_calculated: bool = False  # True se è una colonna calcolata/formula
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "source_node": self.source_node,
            "is_calculated": self.is_calculated
        }


@dataclass(frozen=True)
class _NodeSchemaContext:
    node: Any
    nodes_by_id: dict
    edges_by_target: dict
    project_id: str


class SchemaInferenceService:
    """
    Servizio per inferire lo schema delle colonne attraverso il grafo dei nodi.
    
    Uso:
        service = SchemaInferenceService(project_service)
        schemas = service.infer_all_schemas(project_id)
        # schemas = {"node_1": [ColumnSchema, ...], "node_2": [...]}
    """
    
    def __init__(self, project_service):
        self.project_service = project_service
        self._transform_handlers = TransformSchemaHandlers(ColumnSchema)

        # Cache per evitare ricalcoli durante una singola inferenza
        self._schema_cache: Dict[str, List[ColumnSchema]] = {}
    
    def infer_all_schemas(self, project_id: str) -> Dict[str, List[dict]]:
        """
        Calcola lo schema per tutti i nodi di un progetto.
        
        Returns:
            Dict mapping node_id -> lista di colonne con tipo
        """
        logger.info(f"Inferenza schema per progetto: {project_id}")
        
        # Reset cache
        self._schema_cache = {}
        
        # Carica canvas
        canvas = self.project_service.get_project_canvas(project_id)
        if not canvas:
            logger.warning(f"Canvas non trovato per {project_id}")
            return {}
        
        # Costruisci grafo delle dipendenze
        nodes_by_id = {n.id: n for n in canvas.nodes}
        edges_by_target = self._group_edges_by_target(canvas.edges)
        
        # Calcola schema per ogni nodo (ordine topologico implicito nella ricorsione
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
    
    def infer_node_schema(self, project_id: str, node_id: str) -> List[dict]:
        """
        Calcola lo schema per un singolo nodo.
        Utile per aggiornamenti incrementali.
        """
        # Reset cache
        self._schema_cache = {}
        
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
        # Check cache
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

        # Cache result
        self._schema_cache[node.id] = schema
        return schema

    def _build_node_type_handlers(
        self,
    ) -> Dict[str, Callable[[_NodeSchemaContext], List[ColumnSchema]]]:
        """Registry node.type -> schema handler.

        All node.type-specific inference is registered here. Keep
        `_infer_node_schema` as a registry lookup so new node types do not
        grow central branches.
        """
        return {
            # NODE-DISPATCH-REGISTRY:start
            "source": self._infer_source_node_schema,
            "file_source": self._infer_file_source_node_schema,
            "project_source": self._infer_project_source_node_schema,
            "transform": self._infer_transform_node_schema,
            "api_enrich": self._infer_api_enrich_node_schema,
            "result": self._infer_result_node_schema,
            # NODE-DISPATCH-REGISTRY:end
        }

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
        return []
    
    def _infer_source_schema(self, node) -> List[ColumnSchema]:
        """
        Schema per nodo source (tabella database).
        Legge le colonne direttamente dal nodo.
        """
        columns = []
        
        # Colonne dal database
        node_columns = node.columns or []
        for col in node_columns:
            if col is None or not getattr(col, "visible", True):
                continue
            columns.append(ColumnSchema(
                name=col.alias or col.name,  # Usa alias se presente
                type=col.type or "str",
                source_node=node.id,
                is_calculated=False
            ))

        # Colonne calcolate
        calculated = node.calculated_columns or []
        for calc in calculated:
            if not isinstance(calc, dict):
                continue
            columns.append(ColumnSchema(
                name=calc.get("name", "calculated"),
                type=calc.get("output_type", "str"),
                source_node=node.id,
                is_calculated=True
            ))
        
        return columns
    
    def _infer_project_source_schema(self, node) -> List[ColumnSchema]:
        """
        Schema per nodo project_source (riferimento a progetto ESTRAI).
        Legge i metadati delle colonne dal progetto referenziato.
        """
        source_project_id = None
        
        source_project_id = node.source_project_id or (node.data.get("source_project_id") if node.data else None)
        
        if not source_project_id:
            logger.warning(f"Nodo {node.id} senza source_project_id")
            return []
        
        source_context = load_project_source_schema_context(source_project_id)
        if not source_context:
            logger.warning(f"Metadata non trovato per progetto {source_project_id}")
            return []

        try:
            canvas = source_context.canvas
            if isinstance(canvas, str):
                try:
                    canvas = json.loads(canvas)
                except json.JSONDecodeError as _e:
                    logger.warning(
                        f"Canvas corrotto per project_source {source_project_id}: {_e}. "
                        f"Schema vuoto."
                    )
                    return []
            if not isinstance(canvas, dict):
                logger.warning(
                    f"Canvas con tipo inatteso per project_source {source_project_id}: "
                    f"{type(canvas).__name__}. Schema vuoto."
                )
                return []

            # Fonte primaria: `result_node.columns` del canvas sorgente, scritto
            # autorevolmente al PUT /canvas dal C9 fix
            # app/shared/execution/canvas_columns.compute_result_columns -> mutate
            # canvas.nodes[result].columns prima della persistenza
            # Fallback: `canvas.columns_metadata` (legacy / canvas mai salvato
            # post-C9 / progetti pre-fix). Niente chiamata a
            # compute_result_columns qui per evitare import/costo/ricorsione
            primary_columns = []
            for n in canvas.get("nodes", []) or []:
                if not isinstance(n, dict):
                    continue
                if n.get("type") != "result":
                    continue
                cols = n.get("columns") or []
                if not cols and isinstance(n.get("data"), dict):
                    cols = n["data"].get("columns") or []
                primary_columns = [c for c in cols if isinstance(c, dict)]
                break

            columns_metadata = (
                primary_columns
                if primary_columns
                else canvas.get("columns_metadata", [])
            )

            # Filtra per colonne visibili se specificato
            node_columns = node.columns or (node.data.get("columns") if node.data else None) or []
            visible_columns = None
            if node_columns:
                visible_columns = {
                    col["name"] for col in node_columns
                    if col.get("visible", True)
                }

            columns = []
            for col in columns_metadata:
                col_name = col.get("name", "")

                # Skip se non visibile
                if visible_columns and col_name not in visible_columns:
                    continue

                columns.append(ColumnSchema(
                    name=col_name,
                    type=col.get("type", "str"),
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

        if not transform_op:
            logger.warning(f"Transform {node.id} senza transform_op")
            return input_schemas[0] if input_schemas else []

        # Dispatch via registry (BE-PHASE2-SCHEMA-INFER-008-FINAL
        # sostituisce l'if/elif chain di 65 branch con un map lookup
        # Comportamento invariato: gli adapter lambda nel registry
        # adattano gli handler con signature non-standard
        # `_schema_join` richiede `edges_by_target`, `_schema_geo_overlay`
        # richiede `overlay_type`, alcuni handler ignorano `config`
        handlers = self._build_transform_handlers(edges_by_target)
        handler = handlers.get(transform_op)
        if handler is None:
            logger.warning(f"Operazione {transform_op} non gestita - passthrough")
            return self._transform_handlers.schema_passthrough(input_schemas, node.id)
        return handler(input_schemas, config, node.id)

    def _build_transform_handlers(
        self,
        edges_by_target: dict,
    ) -> Dict[str, Callable[[List[List[ColumnSchema]], dict, str], List[ColumnSchema]]]:
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
        # Parti dallo schema input
        base_schema = input_schemas[0] if input_schemas else []
        result = [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node.id,
            is_calculated=col.is_calculated
        ) for col in base_schema]

        # Campi output dall'API: chiave canonica `output_mapping` (con fallback
        # tolleranti su nomi storici/varianti FE). `output_fields` non e' mai
        # stato popolato in produzione — sostituito qui per allineamento al
        # resto del codebase (api_connector / enrichment_response_processing
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
    
    def _group_edges_by_target(self, edges) -> Dict[str, list]:
        """
        Raggruppa gli edge per nodo target.
        """
        result = {}
        for edge in edges:
            if edge.target not in result:
                result[edge.target] = []
            result[edge.target].append(edge)
        return result


# VALIDAZIONE CONNESSIONI TRA NODI
# Verifica che i nodi collegati abbiano schemi compatibili
# es: la colonna di join esiste in entrambi i lati

class ConnectionValidator:
    """
    Valida le connessioni tra nodi basandosi sugli schemi.
    """
    
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
        
        # Validazioni specifiche per tipo connessione
        if connection_config:
            # Validazione join
            if "left_on" in connection_config:
                left_col = connection_config.get("left_on")
                
                source_cols = {c["name"] for c in source_schema}
                
                if left_col and left_col not in source_cols:
                    return False, f"Colonna '{left_col}' non trovata nel nodo sorgente"
        
        return True, None
    
    def validate_all_connections(self, project_id: str) -> List[dict]:
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
            
            # Validazione colonne join
            if edge.source_column:
                source_cols = {c["name"] for c in source_schema}
                if edge.source_column not in source_cols:
                    errors.append({
                        "edge_id": edge.id,
                        "message": f"Colonna '{edge.source_column}' non trovata",
                        "severity": "error"
                    })
        
        return errors
