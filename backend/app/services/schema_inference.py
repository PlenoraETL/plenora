"""
Servizio di inferenza e propagazione schema attraverso il grafo canvas.

Architettura
============
Questo servizio calcola lo schema (colonne + tipi) in output per ogni nodo
del canvas, permettendo al frontend di popolare dropdown con le colonne
disponibili, validare connessioni tra nodi, e mostrare preview dei tipi.

Funziona per tutti e tre i moduli:
- ESTRAI: schema derivato da database_inspector (tabelle reali) + query_generator
  (colonne calcolate, alias, funzioni aggregate)
- MANIPOLA: schema propagato attraverso la catena di trasformazioni, simulando
  l'effetto di ogni operazione (join aggiunge colonne, select le filtra, etc.)
- ARRICCHISCI: schema base + colonne aggiunte dall'output mapping API

Algoritmo di propagazione:
1. Visita topologica dei nodi (dalle sorgenti verso il risultato)
2. Per ogni nodo, calcola lo schema output basandosi su:
   - Schema input (dal nodo/i predecessore/i)
   - Tipo di nodo e sua configurazione
3. Gestisce casi speciali: JOIN (merge colonne), GROUP BY (solo aggregati),
   trasformazioni che aggiungono/rimuovono/rinominano colonne

Tipi interni URBY propagati: int, real, str, bool, date, geometry, geometry:point,
geometry:polygon, geometry:line, con SRID opzionale (geometry:point:4326).

Cache: i risultati vengono cachati per evitare ricalcoli durante la stessa
sessione di editing del canvas.

Uso:
    service = SchemaInferenceService(connection_service, project_service)
    schema = service.infer_node_schema(canvas, node_id, db_type)
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from app.utils.logger import get_logger

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
        
        
        schema: List[ColumnSchema] = []
        
        # === NODO SOURCE (tabella DB) ===
        if node.type == "source":
            schema = self._infer_source_schema(node)
        
        # === NODO PROJECT_SOURCE (riferimento a progetto ESTRAI) ===
        elif node.type == "project_source":
            schema = self._infer_project_source_schema(node)
        
        # === NODO FILE_SOURCE (file esterno) ===
        elif node.type == "file_source":
            schema = self._infer_file_source_schema(node)
        
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
        
        # Carica metadata dal DB (canvas.columns_metadata)
        from app.db import db_service as _db
        row = _db.get_project(source_project_id)
        if not row:
            logger.warning(f"Metadata non trovato per progetto {source_project_id}")
            return []

        try:
            canvas = row.get("canvas", {})
            if isinstance(canvas, str):
                import json as _json
                try:
                    canvas = _json.loads(canvas)
                except _json.JSONDecodeError as _e:
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
            columns_metadata = canvas.get("columns_metadata", [])
            
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
        elif transform_op == "drop" or transform_op == "drop_columns":
            return self._schema_drop(input_schemas, config, node.id)
        elif transform_op == "fill_na":
            return self._schema_passthrough(input_schemas, node.id)
        elif transform_op == "replace":
            return self._schema_passthrough(input_schemas, node.id)
        elif transform_op == "split_column":
            return self._schema_split_column(input_schemas, config, node.id)
        # Operazioni geometriche
        elif transform_op == "geo_buffer":
            return self._schema_geo_buffer(input_schemas, config, node.id)
        elif transform_op == "geo_centroid":
            return self._schema_geo_centroid(input_schemas, config, node.id)
        elif transform_op == "geo_area":
            return self._schema_geo_area(input_schemas, config, node.id)
        elif transform_op == "geo_clip":
            return self._schema_geo_clip(input_schemas, config, node.id)
        elif transform_op == "geo_dissolve":
            return self._schema_geo_dissolve(input_schemas, config, node.id)
        elif transform_op == "geo_simplify":
            return self._schema_passthrough(input_schemas, node.id)
        elif transform_op == "geo_convex_hull":
            return self._schema_geo_convex_hull(input_schemas, config, node.id)
        elif transform_op == "geo_reproject":
            return self._schema_passthrough(input_schemas, node.id)
        elif transform_op == "geo_intersection":
            return self._schema_geo_overlay(input_schemas, config, node.id, "intersection")
        elif transform_op == "geo_union":
            return self._schema_geo_overlay(input_schemas, config, node.id, "union")
        elif transform_op == "geo_difference":
            return self._schema_geo_overlay(input_schemas, config, node.id, "difference")
        elif transform_op == "geo_to_crs":
            return self._schema_passthrough(input_schemas, node.id)
        # Privacy
        elif transform_op == "md5_hash":
            return self._schema_md5_hash(input_schemas, config, node.id)
        elif transform_op == "mask_data":
            return self._schema_mask_data(input_schemas, config, node.id)
        # Nuove trasformazioni base
        elif transform_op == "type_cast":
            return self._schema_type_cast(input_schemas, config, node.id)
        elif transform_op == "conditional":
            return self._schema_conditional(input_schemas, config, node.id)
        elif transform_op == "text_normalize":
            return self._schema_text_normalize(input_schemas, config, node.id)
        # Concat columns, pivot, geo_envelope — passthrough schema
        elif transform_op == "concat_columns":
            return self._schema_passthrough(input_schemas, node.id)
        elif transform_op == "pivot":
            return self._schema_passthrough(input_schemas, node.id)
        elif transform_op == "geo_envelope":
            return self._schema_passthrough(input_schemas, node.id)
        # Spatial
        elif transform_op == "geo_from_coords":
            return self._schema_geo_from_coords(input_schemas, config, node.id)
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
        output_fields = node.data.get("output_fields", []) if node.data else []
        if output_fields:
            for field in output_fields:
                result.append(ColumnSchema(
                    name=field.get("name", "api_field"),
                    type=field.get("type", "str"),
                    source_node=node.id,
                    is_calculated=True  # Considera come "calcolato"
                ))
        
        return result
    
    # =========================================================================
    # SCHEMA PER OPERAZIONI SPECIFICHE (base)
    # =========================================================================
    # Ogni metodo _schema_xxx() simula l'effetto della trasformazione sullo
    # schema, senza eseguire l'operazione. Questo permette al frontend di
    # conoscere le colonne output PRIMA dell'esecuzione della pipeline.
    
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
        
        # Verifica che non esista già
        existing_names = {col.name for col in result}
        if new_col and new_col not in existing_names:
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
        Distinct: stesso schema dell'input (rimuove solo righe duplicate)
        """
        if not input_schemas:
            return []
        
        return [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
    
    def _schema_rename(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Rename: stesso schema con nomi cambiati
        Config: {"columns": {"old_name": "new_name", ...}} or {"old_name": "x", "new_name": "y"}
        """
        if not input_schemas:
            return []
        
        # Supporta entrambi i formati di config
        rename_map = config.get("columns", {})
        if not rename_map and config.get("old_name") and config.get("new_name"):
            rename_map = {config["old_name"]: config["new_name"]}
        
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
    
    def _schema_split_column(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Split Column: input + nuove colonne dalla divisione
        """
        if not input_schemas:
            return []
        
        result = [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        # Aggiungi nuove colonne
        new_columns = config.get("new_columns", [])
        for col_name in new_columns:
            if col_name and col_name not in {c.name for c in result}:
                result.append(ColumnSchema(
                    name=col_name,
                    type="str",
                    source_node=node_id,
                    is_calculated=True
                ))
        
        return result
    
    # =========================================================================
    # SCHEMA PER OPERAZIONI GEOMETRICHE
    # =========================================================================
    # Le operazioni geometriche possono cambiare il tipo della colonna geometry
    # (es: buffer punto → poligono, centroid poligono → punto) e aggiungere
    # colonne metriche (area_m2, length_m, distance_m).
    
    def _schema_geo_buffer(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Buffer: input + nuova colonna buffer (geometry:polygon)
        
        Se output_column è diverso da geom_column, aggiunge una nuova colonna.
        Se sono uguali, sostituisce la geometria esistente.
        """
        if not input_schemas:
            return []
        
        geom_col = config.get("geom_column", "geometry")
        output_col = config.get("output_column", "buffer_geom")
        
        # Copia tutte le colonne esistenti
        result = [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        # Se output_column è diverso, aggiungi nuova colonna
        if output_col and output_col != geom_col:
            # Verifica che non esista già
            existing_names = {col.name for col in result}
            if output_col not in existing_names:
                result.append(ColumnSchema(
                    name=output_col,
                    type="geometry:polygon",
                    source_node=node_id,
                    is_calculated=True
                ))
        else:
            # Sostituisci la geometria esistente
            for i, col in enumerate(result):
                if col.name == geom_col:
                    result[i] = ColumnSchema(
                        name=col.name,
                        type="geometry:polygon",
                        source_node=node_id,
                        is_calculated=True
                    )
                    break
        
        return result
    
    def _schema_geo_centroid(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Centroid: input + nuova colonna centroid (geometry:point)
        
        Se output_column è diverso da geom_column, aggiunge una nuova colonna.
        """
        if not input_schemas:
            return []
        
        geom_col = config.get("geom_column", "geometry")
        output_col = config.get("output_column", "centroid")
        
        # Copia tutte le colonne esistenti
        result = [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        # Se output_column è diverso, aggiungi nuova colonna
        if output_col and output_col != geom_col:
            existing_names = {col.name for col in result}
            if output_col not in existing_names:
                result.append(ColumnSchema(
                    name=output_col,
                    type="geometry:point",
                    source_node=node_id,
                    is_calculated=True
                ))
        else:
            # Sostituisci la geometria esistente
            for i, col in enumerate(result):
                if col.name == geom_col:
                    result[i] = ColumnSchema(
                        name=col.name,
                        type="geometry:point",
                        source_node=node_id,
                        is_calculated=True
                    )
                    break
        
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
        
        area_col = config.get("output_column", "area_m2")
        
        # Verifica che non esista già
        existing_names = {col.name for col in result}
        if area_col not in existing_names:
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
    
    def _schema_geo_dissolve(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Dissolve: colonna group by + geometria
        """
        if not input_schemas:
            return []
        
        by_column = config.get("by_column")
        geom_col = config.get("geom_column", "geometry")
        
        result = []
        
        # Se c'è group by, includi quella colonna
        if by_column:
            for col in input_schemas[0]:
                if col.name == by_column:
                    result.append(ColumnSchema(
                        name=col.name,
                        type=col.type,
                        source_node=node_id,
                        is_calculated=False
                    ))
                    break
        
        # Aggiungi colonna geometria
        result.append(ColumnSchema(
            name=geom_col,
            type="geometry",
            source_node=node_id,
            is_calculated=True
        ))
        
        return result
    
    def _schema_geo_convex_hull(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Convex Hull: input + nuova colonna convex_hull (geometry:polygon)
        """
        if not input_schemas:
            return []
        
        geom_col = config.get("geom_column", "geometry")
        output_col = config.get("output_column", "convex_hull")
        
        result = [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        # Aggiungi nuova colonna se diversa
        if output_col and output_col != geom_col:
            existing_names = {col.name for col in result}
            if output_col not in existing_names:
                result.append(ColumnSchema(
                    name=output_col,
                    type="geometry:polygon",
                    source_node=node_id,
                    is_calculated=True
                ))
        
        return result
    
    def _schema_geo_overlay(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str,
        overlay_type: str
    ) -> List[ColumnSchema]:
        """
        Overlay operations (intersection, union, difference): 
        Combina colonne da entrambi gli input + geometria risultante
        """
        if len(input_schemas) < 2:
            return input_schemas[0] if input_schemas else []
        
        left_schema = input_schemas[0]
        right_schema = input_schemas[1]
        
        result = []
        
        # Colonne da sinistra
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
            # Skip geometry columns from right (result uses combined geometry)
            if col.type.startswith("geometry"):
                continue
            new_name = col.name
            if new_name in left_names:
                new_name = f"{col.name}_2"
            result.append(ColumnSchema(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated
            ))
        
        return result
    
    # =========================================================================
    # TRASFORMAZIONI PRIVACY
    # =========================================================================
    # Operazioni di anonimizzazione: hash MD5 e mascheramento dati.
    # Non cambiano il tipo colonna, ma possono aggiungere nuove colonne hash.
    
    def _schema_md5_hash(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """MD5 Hash: input + nuova colonna hash (str)"""
        if not input_schemas:
            return []
        
        output_col = config.get("output_column", "md5_hash")
        
        result = [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        result.append(ColumnSchema(
            name=output_col,
            type="str",
            source_node=node_id,
            is_calculated=True
        ))
        
        return result
    
    def _schema_mask_data(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """Mask Data: input + colonne mascherate (str)"""
        if not input_schemas:
            return []
        
        overwrite = config.get("overwrite", False)
        maskings = config.get("maskings", [])
        
        result = [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        if not overwrite:
            existing_names = {col.name for col in result}
            for mask in maskings:
                masked_name = f"{mask.get('column', '')}_masked"
                if masked_name and masked_name not in existing_names:
                    result.append(ColumnSchema(
                        name=masked_name,
                        type="str",
                        source_node=node_id,
                        is_calculated=True
                    ))
        
        return result
    
    # =========================================================================
    # TRASFORMAZIONI BASE AGGIUNTIVE
    # =========================================================================
    # Operazioni aggiunte successivamente: cast tipi, condizionali,
    # normalizzazione testo, creazione geometria da coordinate.
    
    def _schema_type_cast(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """Type Cast: cambia il tipo della colonna target"""
        if not input_schemas:
            return []
        
        target_column = config.get("column", "")
        target_type = config.get("target_type", "str")
        
        # Mappa tipo target alla notazione schema
        type_map = {
            "str": "str",
            "int": "int",
            "float": "real",
            "bool": "bool",
            "date": "date",
            "datetime": "datetime"
        }
        new_type = type_map.get(target_type, "str")
        
        result = []
        for col in input_schemas[0]:
            if col.name == target_column:
                result.append(ColumnSchema(
                    name=col.name,
                    type=new_type,
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
    
    def _schema_conditional(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """Conditional: input + nuova colonna output (str)"""
        if not input_schemas:
            return []
        
        output_col = config.get("output_column", "result")
        
        result = [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        existing_names = {col.name for col in result}
        if output_col not in existing_names:
            result.append(ColumnSchema(
                name=output_col,
                type="str",
                source_node=node_id,
                is_calculated=True
            ))
        
        return result
    
    def _schema_text_normalize(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """Text Normalize: passthrough se overwrite, altrimenti aggiunge colonne _norm"""
        if not input_schemas:
            return []
        
        overwrite = config.get("overwrite", True)
        columns = config.get("columns", [])
        
        result = [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        if not overwrite:
            existing_names = {col.name for col in result}
            for col_name in columns:
                norm_name = f"{col_name}_norm"
                if norm_name not in existing_names:
                    result.append(ColumnSchema(
                        name=norm_name,
                        type="str",
                        source_node=node_id,
                        is_calculated=True
                    ))
        
        return result
    
    def _schema_geo_from_coords(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """Geo from Coords: input + nuova colonna geometry"""
        if not input_schemas:
            return []
        
        output_col = config.get("output_column", "geometry")
        crs = config.get("crs", "EPSG:4326")
        
        result = [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        existing_names = {col.name for col in result}
        if output_col not in existing_names:
            result.append(ColumnSchema(
                name=output_col,
                type=f"geometry:point:{crs.replace('EPSG:', '')}",
                source_node=node_id,
                is_calculated=True
            ))
        
        return result
    
    # =========================================================================
    # METODI DI SUPPORTO
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


# =============================================================================
# VALIDAZIONE CONNESSIONI TRA NODI
# =============================================================================
# Verifica che i nodi collegati abbiano schemi compatibili
# (es: la colonna di join esiste in entrambi i lati).

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