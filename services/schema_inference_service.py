"""
SchemaInferenceService - Propagazione tipi attraverso il grafo dei nodi

Questo servizio calcola lo schema (colonne + tipi) in output per ogni nodo,
permettendo al frontend di:
1. Popolare dropdown con le colonne disponibili
2. Validare connessioni tra nodi
3. Mostrare preview dei tipi

Funziona per tutti i tipi di progetto: ESTRAI, MANIPOLA, ARRICCHISCI
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ColumnSchema:
    """Schema di una singola colonna"""
    name: str
    type: str  # int, real, str, bool, date, geometry:point:4326, etc.
    source_node: Optional[str] = None  # ID del nodo da cui proviene
    is_calculated: bool = False  # True se è una colonna calcolata/formula
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "source_node": self.source_node,
            "is_calculated": self.is_calculated
        }


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
        self.PROJECTS_DIR = settings.PROJECTS_DIR
        
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
        
        # Calcola schema per ogni nodo (ordine topologico implicito nella ricorsione)
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
        
        logger.debug(f"Inferenza schema per nodo {node.id} (tipo: {node.type})")
        
        schema: List[ColumnSchema] = []
        
        # === NODO SOURCE (tabella DB) ===
        if node.type == "source":
            schema = self._infer_source_schema(node)
        
        # === NODO PROJECT_SOURCE (riferimento a progetto ESTRAI) ===
        elif node.type == "project_source":
            schema = self._infer_project_source_schema(node)
        
        # === NODO TRANSFORM ===
        elif node.type == "transform":
            # Recupera schemi degli input
            input_schemas = self._get_input_schemas(
                node.id, nodes_by_id, edges_by_target, project_id
            )
            schema = self._infer_transform_schema(node, input_schemas, edges_by_target)
        
        # === NODO API_ENRICH ===
        elif node.type == "api_enrich":
            input_schemas = self._get_input_schemas(
                node.id, nodes_by_id, edges_by_target, project_id
            )
            schema = self._infer_api_enrich_schema(node, input_schemas)
        
        # === NODO RESULT ===
        elif node.type == "result":
            # Passthrough: stesso schema dell'input
            input_schemas = self._get_input_schemas(
                node.id, nodes_by_id, edges_by_target, project_id
            )
            if input_schemas:
                schema = input_schemas[0]  # Prende il primo (e unico) input
        
        # Cache result
        self._schema_cache[node.id] = schema
        return schema
    
    def _infer_source_schema(self, node) -> List[ColumnSchema]:
        """
        Schema per nodo source (tabella database).
        Legge le colonne direttamente dal nodo.
        """
        columns = []
        
        # Colonne dal database
        node_columns = node.columns or []
        for col in node_columns:
            if col.visible:  # Solo colonne visibili
                columns.append(ColumnSchema(
                    name=col.alias or col.name,  # Usa alias se presente
                    type=col.type or "str",
                    source_node=node.id,
                    is_calculated=False
                ))
        
        # Colonne calcolate
        calculated = node.calculated_columns or []
        for calc in calculated:
            columns.append(ColumnSchema(
                name=calc.get("name", "calculated"),
                type=calc.get("output_type", "str"),
                source_node=node.id,
                is_calculated=True
            ))
        
        logger.debug(f"Source {node.id}: {len(columns)} colonne")
        return columns
    
    def _infer_project_source_schema(self, node) -> List[ColumnSchema]:
        """
        Schema per nodo project_source (riferimento a progetto ESTRAI).
        Legge i metadati delle colonne dal progetto referenziato.
        """
        source_project_id = None
        
        # source_project_id può essere in node.data o direttamente nel nodo
        if node.data and node.data.get("source_project_id"):
            source_project_id = node.data["source_project_id"]
        elif hasattr(node, "source_project_id") and node.source_project_id:
            source_project_id = node.source_project_id
        
        if not source_project_id:
            logger.warning(f"Nodo {node.id} senza source_project_id")
            return []
        
        # Carica metadata del progetto sorgente
        metadata_path = self.PROJECTS_DIR / source_project_id / "metadata.json"
        if not metadata_path.exists():
            logger.warning(f"Metadata non trovato per progetto {source_project_id}")
            return []
        
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            
            columns_metadata = metadata.get("columns_metadata", [])
            
            # Filtra per colonne visibili se specificato in node.data
            visible_columns = None
            if node.data and node.data.get("columns"):
                visible_columns = {
                    col["name"] for col in node.data["columns"] 
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
            
            logger.debug(f"ProjectSource {node.id} -> {source_project_id}: {len(columns)} colonne")
            return columns
            
        except Exception as e:
            logger.error(f"Errore lettura metadata {source_project_id}: {e}")
            return []
    
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
        config = node.data.get("config", {}) if node.data else {}
        transform_op = node.data.get("transform_op") if node.data else None
        
        if not transform_op:
            logger.warning(f"Transform {node.id} senza transform_op")
            return input_schemas[0] if input_schemas else []
        
        logger.debug(f"Transform {node.id}: op={transform_op}")
        
        # Dispatcher per tipo operazione
        if transform_op == "join":
            return self._schema_join(input_schemas, config, node.id, edges_by_target)
        elif transform_op == "sjoin":
            return self._schema_sjoin(input_schemas, config, node.id)
        elif transform_op == "aggregate":
            return self._schema_aggregate(input_schemas, config, node.id)
        elif transform_op == "formula":
            return self._schema_formula(input_schemas, config, node.id)
        elif transform_op == "filter":
            return self._schema_filter(input_schemas, node.id)
        elif transform_op == "concat":
            return self._schema_concat(input_schemas, config, node.id)
        elif transform_op == "sort":
            return self._schema_sort(input_schemas, node.id)
        elif transform_op == "distinct":
            return self._schema_distinct(input_schemas, config, node.id)
        elif transform_op == "rename":
            return self._schema_rename(input_schemas, config, node.id)
        elif transform_op == "drop":
            return self._schema_drop(input_schemas, config, node.id)
        # Operazioni geometriche
        elif transform_op == "geo_buffer":
            return self._schema_geo_buffer(input_schemas, config, node.id)
        elif transform_op == "geo_centroid":
            return self._schema_geo_centroid(input_schemas, config, node.id)
        elif transform_op == "geo_area":
            return self._schema_geo_area(input_schemas, config, node.id)
        elif transform_op == "geo_clip":
            return self._schema_geo_clip(input_schemas, config, node.id)
        elif transform_op == "geo_to_crs":
            return self._schema_passthrough(input_schemas, node.id)
        else:
            logger.warning(f"Operazione {transform_op} non gestita - passthrough")
            return self._schema_passthrough(input_schemas, node.id)
    
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
        
        # Aggiungi campi output dall'API (se configurati)
        if node.data:
            output_fields = node.data.get("output_fields", [])
            for field in output_fields:
                result.append(ColumnSchema(
                    name=field.get("name", "api_field"),
                    type=field.get("type", "str"),
                    source_node=node.id,
                    is_calculated=True  # Considera come "calcolato"
                ))
        
        return result
    
    # =========================================================================
    # SCHEMA PER OPERAZIONI SPECIFICHE
    # =========================================================================
    
    def _schema_join(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str,
        edges_by_target: dict
    ) -> List[ColumnSchema]:
        """
        Join: merge colonne sinistra + destra con suffissi _L e _R
        """
        if len(input_schemas) < 2:
            return input_schemas[0] if input_schemas else []
        
        left_schema = input_schemas[0]
        right_schema = input_schemas[1]
        
        left_on = config.get("left_on", "")
        right_on = config.get("right_on", "")
        
        result = []
        
        # Colonne da sinistra con suffisso _L
        for col in left_schema:
            new_name = f"{col.name}_L" if col.name != left_on else col.name
            result.append(ColumnSchema(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated
            ))
        
        # Colonne da destra con suffisso _R (esclusa join key se uguale)
        for col in right_schema:
            if col.name == right_on and left_on == right_on:
                continue  # Skip duplicato join key
            new_name = f"{col.name}_R" if col.name != right_on else f"{col.name}_R"
            result.append(ColumnSchema(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated
            ))
        
        return result
    
    def _schema_sjoin(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Spatial Join: simile a join ma con index_right aggiunto
        """
        if len(input_schemas) < 2:
            return input_schemas[0] if input_schemas else []
        
        left_schema = input_schemas[0]
        right_schema = input_schemas[1]
        
        result = []
        
        # Tutte le colonne da sinistra
        for col in left_schema:
            result.append(ColumnSchema(
                name=col.name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated
            ))
        
        # Colonne da destra (con suffisso per evitare conflitti)
        left_names = {col.name for col in left_schema}
        for col in right_schema:
            new_name = col.name
            if new_name in left_names:
                new_name = f"{col.name}_right"
            result.append(ColumnSchema(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated
            ))
        
        # GeoPandas aggiunge index_right
        result.append(ColumnSchema(
            name="index_right",
            type="int",
            source_node=node_id,
            is_calculated=True
        ))
        
        return result
    
    def _schema_aggregate(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Aggregate: group_by columns + aggregated columns
        """
        if not input_schemas:
            return []
        
        input_schema = input_schemas[0]
        input_by_name = {col.name: col for col in input_schema}
        
        group_by = config.get("group_by", [])
        aggs = config.get("aggs", {})  # {"col": "sum", "col2": "count"}
        
        result = []
        
        # Colonne group by (mantengono tipo originale)
        for col_name in group_by:
            if col_name in input_by_name:
                col = input_by_name[col_name]
                result.append(ColumnSchema(
                    name=col_name,
                    type=col.type,
                    source_node=node_id,
                    is_calculated=False
                ))
        
        # Colonne aggregate
        for col_name, agg_func in aggs.items():
            orig_col = input_by_name.get(col_name)
            
            # Determina tipo output basato su funzione
            if agg_func in ("count", "nunique", "size"):
                out_type = "int"
            elif agg_func in ("sum", "mean", "std", "var", "min", "max"):
                out_type = orig_col.type if orig_col else "real"
                # Sum/mean su int -> real
                if agg_func in ("mean", "std", "var"):
                    out_type = "real"
            elif agg_func == "first" or agg_func == "last":
                out_type = orig_col.type if orig_col else "str"
            else:
                out_type = "str"
            
            result.append(ColumnSchema(
                name=col_name,
                type=out_type,
                source_node=node_id,
                is_calculated=True
            ))
        
        return result
    
    def _schema_formula(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Formula: input + nuova colonna calcolata
        """
        if not input_schemas:
            return []
        
        # Copia schema input
        result = [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        # Aggiungi nuova colonna
        new_col = config.get("new_column", "calculated")
        output_type = config.get("output_type", "real")  # Default numerico
        
        result.append(ColumnSchema(
            name=new_col,
            type=output_type,
            source_node=node_id,
            is_calculated=True
        ))
        
        return result
    
    def _schema_filter(
        self, 
        input_schemas: List[List[ColumnSchema]],
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Filter: stesso schema (filtra righe, non colonne)
        """
        return self._schema_passthrough(input_schemas, node_id)
    
    def _schema_concat(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Concat: unione colonne (outer) o intersezione (inner)
        """
        if not input_schemas:
            return []
        
        join_mode = config.get("join", "outer")
        
        if join_mode == "inner":
            # Solo colonne comuni a tutti gli input
            common_cols = None
            for schema in input_schemas:
                col_names = {col.name for col in schema}
                if common_cols is None:
                    common_cols = col_names
                else:
                    common_cols &= col_names
            
            if not common_cols:
                return []
            
            # Prendi tipi dal primo schema
            result = []
            for col in input_schemas[0]:
                if col.name in common_cols:
                    result.append(ColumnSchema(
                        name=col.name,
                        type=col.type,
                        source_node=node_id,
                        is_calculated=col.is_calculated
                    ))
            return result
        
        else:  # outer
            # Tutte le colonne da tutti gli input
            seen = {}
            for schema in input_schemas:
                for col in schema:
                    if col.name not in seen:
                        seen[col.name] = ColumnSchema(
                            name=col.name,
                            type=col.type,
                            source_node=node_id,
                            is_calculated=col.is_calculated
                        )
            return list(seen.values())
    
    def _schema_sort(
        self, 
        input_schemas: List[List[ColumnSchema]],
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Sort: stesso schema (cambia solo ordine righe)
        """
        return self._schema_passthrough(input_schemas, node_id)
    
    def _schema_distinct(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Distinct: stesso schema o sottoinsieme se specificato subset
        """
        if not input_schemas:
            return []
        
        subset = config.get("subset", None)  # Lista colonne per distinct
        
        if subset:
            # Solo colonne nel subset
            input_by_name = {col.name: col for col in input_schemas[0]}
            result = []
            for col_name in subset:
                if col_name in input_by_name:
                    col = input_by_name[col_name]
                    result.append(ColumnSchema(
                        name=col.name,
                        type=col.type,
                        source_node=node_id,
                        is_calculated=col.is_calculated
                    ))
            return result
        
        return self._schema_passthrough(input_schemas, node_id)
    
    def _schema_rename(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Rename: stesso schema con nomi cambiati
        Config: {"columns": {"old_name": "new_name", ...}}
        """
        if not input_schemas:
            return []
        
        rename_map = config.get("columns", {})
        
        result = []
        for col in input_schemas[0]:
            new_name = rename_map.get(col.name, col.name)
            result.append(ColumnSchema(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated
            ))
        
        return result
    
    def _schema_drop(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Drop: input meno colonne specificate
        Config: {"columns": ["col1", "col2"]}
        """
        if not input_schemas:
            return []
        
        drop_cols = set(config.get("columns", []))
        
        result = []
        for col in input_schemas[0]:
            if col.name not in drop_cols:
                result.append(ColumnSchema(
                    name=col.name,
                    type=col.type,
                    source_node=node_id,
                    is_calculated=col.is_calculated
                ))
        
        return result
    
    # =========================================================================
    # SCHEMA PER OPERAZIONI GEOMETRICHE
    # =========================================================================
    
    def _schema_geo_buffer(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Buffer: stesso schema, geometria diventa polygon
        """
        if not input_schemas:
            return []
        
        geom_col = config.get("geom_column", "geometry")
        
        result = []
        for col in input_schemas[0]:
            if col.name == geom_col:
                # Buffer produce sempre polygon
                result.append(ColumnSchema(
                    name=col.name,
                    type="geometry:polygon",
                    source_node=node_id,
                    is_calculated=True
                ))
            else:
                result.append(ColumnSchema(
                    name=col.name,
                    type=col.type,
                    source_node=node_id,
                    is_calculated=col.is_calculated
                ))
        
        return result
    
    def _schema_geo_centroid(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Centroid: stesso schema, geometria diventa point
        """
        if not input_schemas:
            return []
        
        geom_col = config.get("geom_column", "geometry")
        
        result = []
        for col in input_schemas[0]:
            if col.name == geom_col:
                result.append(ColumnSchema(
                    name=col.name,
                    type="geometry:point",
                    source_node=node_id,
                    is_calculated=True
                ))
            else:
                result.append(ColumnSchema(
                    name=col.name,
                    type=col.type,
                    source_node=node_id,
                    is_calculated=col.is_calculated
                ))
        
        return result
    
    def _schema_geo_area(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Area: input + colonna area (real)
        """
        if not input_schemas:
            return []
        
        result = [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        area_col = config.get("output_column", "area")
        result.append(ColumnSchema(
            name=area_col,
            type="real",
            source_node=node_id,
            is_calculated=True
        ))
        
        return result
    
    def _schema_geo_clip(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Clip: stesso schema del primo input (layer da ritagliare)
        """
        if not input_schemas:
            return []
        
        return self._schema_passthrough(input_schemas, node_id)
    
    # =========================================================================
    # UTILITY
    # =========================================================================
    
    def _schema_passthrough(
        self, 
        input_schemas: List[List[ColumnSchema]],
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Passthrough: stesso schema dell'input
        """
        if not input_schemas:
            return []
        
        return [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
    
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


# ============================================================================
# VALIDAZIONE CONNESSIONI
# ============================================================================

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
        target_schema = schemas.get(target_node_id, [])
        
        if not source_schema:
            return False, f"Nodo sorgente {source_node_id} non ha schema"
        
        # Validazioni specifiche per tipo connessione
        if connection_config:
            # Validazione join
            if "left_on" in connection_config:
                left_col = connection_config.get("left_on")
                right_col = connection_config.get("right_on")
                
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
