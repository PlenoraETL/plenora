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

Tipi interni PLENORA propagati: int, real, str, bool, date, geometry, geometry:point,
geometry:polygon, geometry:line, con SRID opzionale (geometry:point:4326).

Cache: i risultati vengono cachati per evitare ricalcoli durante la stessa
sessione di editing del canvas.

Uso:
    service = SchemaInferenceService(connection_service, project_service)
    schema = service.infer_node_schema(canvas, node_id, db_type)
"""

import json
import re
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

            # Fonte primaria: `result_node.columns` del canvas sorgente, scritto
            # autorevolmente al PUT /canvas dal C9 fix
            # (app/shared/canvas_columns.compute_result_columns -> mutate
            # canvas.nodes[result].columns prima della persistenza).
            # Fallback: `canvas.columns_metadata` (legacy / canvas mai salvato
            # post-C9 / progetti pre-fix). Niente chiamata a
            # compute_result_columns qui per evitare import/costo/ricorsione.
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
        # Utility / strings (BE-PHASE2-SCHEMA-INFER-002 batch 1)
        elif transform_op == "add_row_number":
            return self._schema_add_row_number(input_schemas, config, node.id)
        elif transform_op == "uuid_generator":
            return self._schema_uuid_generator(input_schemas, config, node.id)
        elif transform_op == "string_length":
            return self._schema_string_length(input_schemas, config, node.id)
        elif transform_op == "date_extract":
            return self._schema_date_extract(input_schemas, config, node.id)
        elif transform_op == "geo_distance":
            return self._schema_geo_distance(input_schemas, config, node.id)
        elif transform_op == "geo_to_wkt":
            return self._schema_geo_to_wkt(input_schemas, config, node.id)
        # Geo metrics + base strings/binning (BE-PHASE2-SCHEMA-INFER-003 batch 2)
        elif transform_op == "geo_length":
            return self._schema_geo_length(input_schemas, config, node.id)
        elif transform_op == "geo_perimeter":
            return self._schema_geo_perimeter(input_schemas, config, node.id)
        elif transform_op == "geo_vertex_count":
            return self._schema_geo_vertex_count(input_schemas, config, node.id)
        elif transform_op == "string_extract":
            return self._schema_string_extract(input_schemas, config, node.id)
        elif transform_op == "bin":
            return self._schema_bin(input_schemas, config, node.id)
        elif transform_op == "string_pad":
            return self._schema_string_pad(input_schemas, config, node.id)
        # Generative geo (BE-PHASE2-SCHEMA-INFER-004 batch 3)
        elif transform_op == "geo_bounds_extractor":
            return self._schema_geo_bounds_extractor(input_schemas, config, node.id)
        elif transform_op == "geo_count_points_in_polygons":
            return self._schema_geo_count_points_in_polygons(input_schemas, config, node.id)
        elif transform_op == "geo_line_builder":
            return self._schema_geo_line_builder(input_schemas, config, node.id)
        elif transform_op == "geo_polygon_builder":
            return self._schema_geo_polygon_builder(input_schemas, config, node.id)
        elif transform_op == "geo_point_on_surface":
            return self._schema_geo_point_on_surface(input_schemas, config, node.id)
        elif transform_op == "geo_boundary":
            return self._schema_geo_boundary(input_schemas, config, node.id)
        # Reorder + voronoi (BE-PHASE2-SCHEMA-INFER-005 batch 4)
        elif transform_op == "reorder_columns":
            return self._schema_reorder_columns(input_schemas, config, node.id)
        elif transform_op == "geo_voronoi":
            return self._schema_geo_voronoi(input_schemas, config, node.id)
        # Multi-input + lookup (BE-PHASE2-SCHEMA-INFER-006 batch 5)
        elif transform_op == "cross_join":
            return self._schema_cross_join(input_schemas, node.id)
        elif transform_op == "geo_within":
            return self._schema_geo_within(input_schemas, node.id)
        elif transform_op == "geo_overlay":
            how = config.get("how", "intersection")
            return self._schema_geo_overlay(input_schemas, config, node.id, how)
        elif transform_op == "geo_symmetric_difference":
            return self._schema_geo_overlay(
                input_schemas, config, node.id, "symmetric_difference",
            )
        elif transform_op == "geo_nearest":
            return self._schema_geo_nearest(input_schemas, config, node.id)
        elif transform_op == "lookup":
            return self._schema_lookup(input_schemas, config, node.id)
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

        # Campi output dall'API: chiave canonica `output_mapping` (con fallback
        # tolleranti su nomi storici/varianti FE). `output_fields` non e' mai
        # stato popolato in produzione — sostituito qui per allineamento al
        # resto del codebase (api_connector / enrichment_response_processing).
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
        
        # Colonne da destra con suffisso _R, ESCLUSA la join key:
        #   - se left_on == right_on: la join key di destra e' un duplicato
        #     della sinistra (gia' inclusa senza suffisso) → skip
        #   - se left_on != right_on: la join key di destra resta nel
        #     risultato col suo nome originale (senza _R) — e' una colonna
        #     significativa, non un attributo collaterale.
        # Pre-fix bug: la condizione ternaria aveva entrambi i rami uguali
        # (`f"{col.name}_R" if ... else f"{col.name}_R"`), quindi la join
        # key di destra finiva sempre come `id_right_R`, confondendo il
        # downstream sul nome reale della colonna.
        for col in right_schema:
            if col.name == right_on and left_on == right_on:
                continue  # Skip duplicato join key
            new_name = col.name if col.name == right_on else f"{col.name}_R"
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
    # BATCH 1 — BE-PHASE2-SCHEMA-INFER-002
    # =========================================================================
    # Handler dedicati per i 6 transform_op del primo batch di parity gap
    # closure. Ogni handler corrisponde 1:1 alla shape di output del runtime
    # in `app/core/manipola/transforms/{utility,strings,spatial}.py`.
    #
    # Convenzione: input + nuove colonne (mai mutazione del nome di colonne
    # esistenti, salvo collisione esplicita gestita come "skip add"). Tutti
    # gli handler sono pure: input → output, niente I/O, niente env.

    def _schema_add_row_number(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """add_row_number: input + colonna `output_column` (int).

        Runtime: `op_add_row_number` (utility.py) aggiunge `output_column`
        come int sequenziale globale o per-partition. Gli altri parametri
        (`start`, `partition_column`, `order_column`, `ascending`) non
        alterano lo schema.
        """
        if not input_schemas:
            return []

        result = self._copy_schema(input_schemas[0], node_id)
        output_col = config.get("output_column") or "row_number"
        if output_col and output_col not in {c.name for c in result}:
            result.append(ColumnSchema(
                name=output_col,
                type="int",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_uuid_generator(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """uuid_generator: input + colonna `output_column` (str UUIDv4).

        Runtime: `op_uuid_generator` aggiunge una colonna stringa con UUID
        v4 per riga.
        """
        if not input_schemas:
            return []

        result = self._copy_schema(input_schemas[0], node_id)
        output_col = config.get("output_column") or "uuid"
        if output_col and output_col not in {c.name for c in result}:
            result.append(ColumnSchema(
                name=output_col,
                type="str",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_string_length(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """string_length: input + colonna `output_column` (int).

        Runtime: `op_string_length` (strings.py) aggiunge la lunghezza di
        `column` in `output_column` (default `"{column}_length"`). Se
        `column` non esiste a runtime il transform e' no-op (warning) —
        qui replichiamo: senza `column` valido o senza output_col
        derivabile, schema invariato.
        """
        if not input_schemas:
            return []

        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        configured_out = config.get("output_column")
        if configured_out:
            output_col = configured_out
        elif column:
            output_col = f"{column}_length"
        else:
            return result  # senza column ne' output_column — schema invariato

        if output_col not in {c.name for c in result}:
            result.append(ColumnSchema(
                name=output_col,
                type="int",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_date_extract(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """date_extract: input + 1 colonna per ogni `parts` valida.

        Runtime: `op_date_extract` (utility.py) crea
        `f"{prefix or column + '_'}{part}"` per ogni `part` in
        {year, month, day, quarter, weekday, week, hour, minute, second}.
        Tipo output: `int` per tutte le parti (week e' Int64 in pandas
        ma resta semanticamente int per il FE).

        Config:
        - `column`: colonna sorgente datetime (required)
        - `parts`: list[str] o str singolo (default `["year"]`)
        - `prefix`: str opzionale (se vuoto, usa `f"{column}_"`)

        Le parts non riconosciute sono ignorate (allineato al runtime).
        """
        if not input_schemas:
            return []

        result = self._copy_schema(input_schemas[0], node_id)

        column = config.get("column") or ""
        if not column:
            return result  # no source col -> no schema delta

        raw_parts = config.get("parts")
        if raw_parts is None:
            parts: List[str] = ["year"]
        elif isinstance(raw_parts, str):
            parts = [raw_parts]
        elif isinstance(raw_parts, list):
            parts = [str(p) for p in raw_parts if p]
        else:
            parts = ["year"]

        prefix = config.get("prefix")
        col_prefix = prefix if prefix else f"{column}_"

        valid_parts = {
            "year", "month", "day", "quarter", "weekday",
            "week", "hour", "minute", "second",
        }

        existing = {c.name for c in result}
        for part in parts:
            part_lower = part.lower().strip()
            if part_lower not in valid_parts:
                continue
            col_name = f"{col_prefix}{part_lower}"
            if col_name in existing:
                continue
            result.append(ColumnSchema(
                name=col_name,
                type="int",
                source_node=node_id,
                is_calculated=True,
            ))
            existing.add(col_name)

        return result

    def _schema_geo_distance(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_distance: input sinistro + colonna `output_column` (real).

        Runtime: `op_geo_distance` (spatial.py) e' multi-input (dfs[0] =
        left, dfs[1] = right). L'output e' un GeoDataFrame con TUTTE le
        colonne del LEFT + `output_column` (distanza float). Il right
        non contribuisce colonne allo schema output.

        Right input vuoto/mancante: il runtime ritorna `dfs[0]` (o un
        empty GDF) — lo schema dichiarato resta comunque LEFT +
        output_column (allineato col percorso success-path canonico).
        """
        if not input_schemas:
            return []

        # left schema (primo input). Se manca anche un secondo input,
        # il runtime degrada graziosamente; lo schema FE annunciato resta
        # comunque coerente col success-path canonico (left + distance).
        result = self._copy_schema(input_schemas[0], node_id)
        output_col = config.get("output_column") or "distance_m"
        if output_col and output_col not in {c.name for c in result}:
            result.append(ColumnSchema(
                name=output_col,
                type="real",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_geo_to_wkt(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_to_wkt: input + 1 o 3 colonne in base a `mode`.

        Runtime: `op_geo_to_wkt` (spatial.py) supporta 3 modalita':
        - `"wkt"` (default): aggiunge `wkt_column` (str)
        - `"latlon"`: aggiunge `lat_column` (real) + `lon_column` (real)
        - `"both"`: aggiunge tutte e 3

        Config defaults: `mode="wkt"`, `wkt_column="geometry_wkt"`,
        `lat_column="latitude"`, `lon_column="longitude"`.
        """
        if not input_schemas:
            return []

        result = self._copy_schema(input_schemas[0], node_id)
        existing = {c.name for c in result}

        mode = (config.get("mode") or "wkt").lower().strip()
        if mode not in ("wkt", "latlon", "both"):
            mode = "wkt"  # allineato al default runtime

        if mode in ("wkt", "both"):
            wkt_col = config.get("wkt_column") or "geometry_wkt"
            if wkt_col and wkt_col not in existing:
                result.append(ColumnSchema(
                    name=wkt_col, type="str",
                    source_node=node_id, is_calculated=True,
                ))
                existing.add(wkt_col)

        if mode in ("latlon", "both"):
            lat_col = config.get("lat_column") or "latitude"
            lon_col = config.get("lon_column") or "longitude"
            if lat_col and lat_col not in existing:
                result.append(ColumnSchema(
                    name=lat_col, type="real",
                    source_node=node_id, is_calculated=True,
                ))
                existing.add(lat_col)
            if lon_col and lon_col not in existing:
                result.append(ColumnSchema(
                    name=lon_col, type="real",
                    source_node=node_id, is_calculated=True,
                ))
                existing.add(lon_col)

        return result

    # =========================================================================
    # BATCH 2 — BE-PHASE2-SCHEMA-INFER-003
    # =========================================================================
    # Handler dedicati per transform geo metrici (length/perimeter/vertex_count)
    # e base strings/binning (string_extract, bin, string_pad).
    # Stessa convenzione del batch 1: pure handler input -> output, niente I/O.

    def _schema_geo_length(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_length: input + colonna `output_column` (real).

        Runtime: `op_geo_length` (spatial.py) aggiunge `output_column`
        (default `"length_m"`) come float in metri (auto-UTM se il CRS
        e' geografico). `geom_column` e `metric_crs` non influenzano
        lo schema FE.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        output_col = config.get("output_column") or "length_m"
        if output_col and output_col not in {c.name for c in result}:
            result.append(ColumnSchema(
                name=output_col,
                type="real",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_geo_perimeter(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_perimeter: input + colonna `output_column` (real).

        Runtime: `op_geo_perimeter` (spatial.py) aggiunge `output_column`
        (default `"perimeter_m"`) come float in metri. `metric_crs` non
        impatta lo schema.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        output_col = config.get("output_column") or "perimeter_m"
        if output_col and output_col not in {c.name for c in result}:
            result.append(ColumnSchema(
                name=output_col,
                type="real",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_geo_vertex_count(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_vertex_count: input + colonna `output_column` (int).

        Runtime: `op_geo_vertex_count` (spatial.py) aggiunge il numero
        di vertici per ogni geometria (default colonna `"vertex_count"`).
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        output_col = config.get("output_column") or "vertex_count"
        if output_col and output_col not in {c.name for c in result}:
            result.append(ColumnSchema(
                name=output_col,
                type="int",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_string_extract(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """string_extract: input + colonne estratte da regex (str).

        Runtime: `op_string_extract` (strings.py).
        - Pattern con gruppi nominati `(?P<name>...)`: una colonna str
          per ogni gruppo nominato (nomi e ordine derivati da
          `re.compile(pattern).groupindex`).
        - Pattern senza gruppi nominati: una sola colonna str
          `output_column or f"{column}_extracted"` (vale anche per
          `extract_all=True`, che cambia solo i valori di runtime).

        No-op (schema invariato) se `column`/`pattern` mancanti, se
        `column` non esiste in input, o se il pattern e' regex non
        valida (allineato al runtime: warning + return df).

        Caveat: pattern con misto named/unnamed groups → schema
        annunciato copre solo i named groups (allineato al subset
        deterministico). Casi rari nei flussi FE.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        pattern = config.get("pattern") or ""
        if not column or not pattern:
            return result
        existing = {c.name for c in result}
        if column not in existing:
            return result
        try:
            compiled = re.compile(pattern)
        except re.error:
            return result

        named_groups = compiled.groupindex
        if named_groups:
            # Ordine pattern: ordina per indice di gruppo (1-based).
            ordered_names = sorted(named_groups, key=named_groups.get)
            for name in ordered_names:
                if name and name not in existing:
                    result.append(ColumnSchema(
                        name=name,
                        type="str",
                        source_node=node_id,
                        is_calculated=True,
                    ))
                    existing.add(name)
        else:
            out_col = config.get("output_column") or f"{column}_extracted"
            if out_col and out_col not in existing:
                result.append(ColumnSchema(
                    name=out_col,
                    type="str",
                    source_node=node_id,
                    is_calculated=True,
                ))
        return result

    def _schema_bin(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """bin: input + colonna discretizzata (str).

        Runtime: `op_bin` (analysis.py) usa `pd.cut` e produce una
        colonna categorica/string (le label sono str). Default
        `output_column = f"{column}_bin"`. No-op se `column` mancante
        o non in input (allineato al runtime warning path).

        Tipo registrato come `"str"` (le label categorical vengono
        consumate dal FE come stringhe).
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        if not column:
            return result
        existing = {c.name for c in result}
        if column not in existing:
            return result
        out_col = config.get("output_column") or f"{column}_bin"
        if out_col and out_col not in existing:
            result.append(ColumnSchema(
                name=out_col,
                type="str",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_string_pad(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """string_pad: pad la colonna a larghezza fissa.

        Runtime: `op_string_pad` (strings.py) forza `astype(str)` e
        scrive nel target. Schema delta:
        - `output_column` vuoto (o uguale a `column`): la colonna
          sorgente viene sovrascritta come `str`.
        - `output_column` diversa da `column`: la sorgente resta
          invariata (preservato il suo tipo) e viene aggiunta una
          nuova colonna `str`. Se collide con un altro nome esistente,
          quella colonna viene sovrascritta a `str`.

        No-op (schema invariato) se `column` mancante o non in input.
        Registry classifica `output: same`, ma il runtime introduce
        comunque un type-shift verso str → schema parity dedicata
        invece di passthrough generico.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        if not column:
            return result
        existing_names = {c.name for c in result}
        if column not in existing_names:
            return result

        out_col_cfg = config.get("output_column")
        target = out_col_cfg if out_col_cfg else column

        if target == column:
            for i, col in enumerate(result):
                if col.name == column:
                    result[i] = ColumnSchema(
                        name=col.name,
                        type="str",
                        source_node=node_id,
                        is_calculated=True,
                    )
                    break
            return result

        if target in existing_names:
            for i, col in enumerate(result):
                if col.name == target:
                    result[i] = ColumnSchema(
                        name=col.name,
                        type="str",
                        source_node=node_id,
                        is_calculated=True,
                    )
                    break
        else:
            result.append(ColumnSchema(
                name=target,
                type="str",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    # =========================================================================
    # BATCH 3 — BE-PHASE2-SCHEMA-INFER-004
    # =========================================================================
    # Handler dedicati per geometrie generative deterministiche
    # (bounds_extractor, count_points_in_polygons, line_builder,
    # polygon_builder, point_on_surface, boundary).
    # Stessa convenzione dei batch 1+2: pure handler input -> output,
    # niente I/O, niente env reads, type-hinted, riuso di
    # `_copy_schema` per l'identita' di base.

    def _schema_geo_bounds_extractor(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_bounds_extractor: input + 4 colonne bounding box (real).

        Runtime: `op_geo_bounds_extractor` (spatial.py) aggiunge
        `f"{prefix}min_x"`, `f"{prefix}min_y"`, `f"{prefix}max_x"`,
        `f"{prefix}max_y"` (notazione `min_x`, NON `minx` — il prefisso
        e' applicato al nome canonico con underscore). `prefix` default
        `""`.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        prefix = config.get("prefix") or ""
        existing = {c.name for c in result}
        for suffix in ("min_x", "min_y", "max_x", "max_y"):
            col_name = f"{prefix}{suffix}"
            if col_name and col_name not in existing:
                result.append(ColumnSchema(
                    name=col_name,
                    type="real",
                    source_node=node_id,
                    is_calculated=True,
                ))
                existing.add(col_name)
        return result

    def _schema_geo_count_points_in_polygons(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_count_points_in_polygons: LEFT (poligoni) + colonna
        conteggio (int) + colonna aggregata opzionale (real).

        Runtime: `op_geo_count_points_in_polygons` (spatial.py) e'
        multi-input (dfs[0]=poligoni LEFT, dfs[1]=punti RIGHT). Output:
        - tutte le colonne del LEFT (poligoni)
        - `count_column` (default `"point_count"`, int)
        - se `aggregate_column` non vuoto: `f"{aggregate_column}_{func}"`
          dove `func` e' uno tra `sum/mean/min/max` (default `"sum"`,
          fallback su valori unknown). Tipo: `real`.

        Il RIGHT non contribuisce colonne. La colonna temporanea
        `__poly_idx__` viene droppata dal runtime, quindi non appare
        nello schema dichiarato.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        existing = {c.name for c in result}

        count_col = config.get("count_column") or "point_count"
        if count_col and count_col not in existing:
            result.append(ColumnSchema(
                name=count_col,
                type="int",
                source_node=node_id,
                is_calculated=True,
            ))
            existing.add(count_col)

        agg_col = config.get("aggregate_column") or ""
        if agg_col:
            raw_func = config.get("aggregate_func") or "sum"
            func = raw_func if raw_func in ("sum", "mean", "min", "max") else "sum"
            agg_name = f"{agg_col}_{func}"
            if agg_name not in existing:
                result.append(ColumnSchema(
                    name=agg_name,
                    type="real",
                    source_node=node_id,
                    is_calculated=True,
                ))
                existing.add(agg_name)
        return result

    def _schema_geo_line_builder(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_line_builder: ristrutturazione completa.

        Runtime: `op_geo_line_builder` (spatial.py) raggruppa per
        `group_column` e produce un GeoDataFrame con SOLO le colonne
        `[group_column, output_column]`. Tutte le altre colonne
        dell'input vengono scartate.

        Output:
        - `group_column` con tipo dell'input (preservato se trovato).
        - `output_column` (default `"geometry"`) di tipo
          `geometry:line`.

        Se `group_column` manca dalla config: schema invariato
        (passthrough). Senza `group_column` il runtime crash a
        groupby; non guess su nuovo schema.
        """
        return self._schema_geo_builder(
            input_schemas, config, node_id, geom_subtype="line",
        )

    def _schema_geo_polygon_builder(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_polygon_builder: ristrutturazione completa.

        Runtime: `op_geo_polygon_builder` (spatial.py) raggruppa per
        `group_column` e produce un GeoDataFrame con SOLO le colonne
        `[group_column, output_column]`. output_column di tipo
        `geometry:polygon` (Polygon chiuso automaticamente).
        """
        return self._schema_geo_builder(
            input_schemas, config, node_id, geom_subtype="polygon",
        )

    def _schema_geo_builder(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
        geom_subtype: str,
    ) -> List[ColumnSchema]:
        """Helper condiviso per `geo_line_builder` / `geo_polygon_builder`.

        I due transform hanno la stessa shape (rebuild a 2 colonne)
        ma un sottotipo geometrico diverso.
        """
        if not input_schemas:
            return []
        group_col = config.get("group_column") or ""
        output_col = config.get("output_column") or "geometry"
        if not group_col:
            return self._copy_schema(input_schemas[0], node_id)

        # Preserva il tipo originale di group_column se presente.
        input_by_name = {c.name: c for c in input_schemas[0]}
        group_type = input_by_name[group_col].type if group_col in input_by_name else "str"

        result: List[ColumnSchema] = [
            ColumnSchema(
                name=group_col,
                type=group_type,
                source_node=node_id,
                is_calculated=False,
            ),
        ]
        if output_col and output_col != group_col:
            result.append(ColumnSchema(
                name=output_col,
                type=f"geometry:{geom_subtype}",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_geo_point_on_surface(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_point_on_surface: input + colonna `output_column`
        (geometry:point), oppure sostituisce la geometria sorgente.

        Runtime: `op_geo_point_on_surface` (spatial.py) chiama
        `representative_point()` e lo scrive in `output_column`
        (default `"point_on_surface"`). Se `output_column` coincide
        con `geom_column` la sorgente viene sovrascritta a
        `geometry:point`. Stesso pattern del `_schema_geo_centroid`
        legacy.
        """
        if not input_schemas:
            return []
        geom_col = config.get("geom_column", "geometry")
        output_col = config.get("output_column") or "point_on_surface"
        result = self._copy_schema(input_schemas[0], node_id)

        if output_col and output_col != geom_col:
            existing = {c.name for c in result}
            if output_col not in existing:
                result.append(ColumnSchema(
                    name=output_col,
                    type="geometry:point",
                    source_node=node_id,
                    is_calculated=True,
                ))
        else:
            for i, col in enumerate(result):
                if col.name == geom_col:
                    result[i] = ColumnSchema(
                        name=col.name,
                        type="geometry:point",
                        source_node=node_id,
                        is_calculated=True,
                    )
                    break
        return result

    def _schema_geo_boundary(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_boundary: input + colonna `output_column` (geometry).

        Runtime: `op_geo_boundary` (spatial.py) calcola
        `gdf.geometry.boundary` e lo scrive in `output_column`
        (default `"boundary"`). Il sottotipo dipende dall'input
        (poligono → line, line → multipoint), quindi il tipo
        dichiarato e' `"geometry"` generico — non vincoliamo a
        sottotipo per non escludere downstream legittimi.

        Se `output_column == geom_column`, sovrascrive la geometria
        sorgente a `"geometry"` (perde subtype precedente, allineato
        al runtime che cambia il tipo geometrico).
        """
        if not input_schemas:
            return []
        geom_col = config.get("geom_column", "geometry")
        output_col = config.get("output_column") or "boundary"
        result = self._copy_schema(input_schemas[0], node_id)

        if output_col and output_col != geom_col:
            existing = {c.name for c in result}
            if output_col not in existing:
                result.append(ColumnSchema(
                    name=output_col,
                    type="geometry",
                    source_node=node_id,
                    is_calculated=True,
                ))
        else:
            for i, col in enumerate(result):
                if col.name == geom_col:
                    result[i] = ColumnSchema(
                        name=col.name,
                        type="geometry",
                        source_node=node_id,
                        is_calculated=True,
                    )
                    break
        return result

    # =========================================================================
    # BATCH 4 — BE-PHASE2-SCHEMA-INFER-005
    # =========================================================================
    # Handler dedicati per il riordino colonne (`reorder_columns`) e per
    # `geo_voronoi`. Le altre 4 candidati (dedup_advanced,
    # geo_clean_topology, geo_make_valid, geo_explode) sono rows-only:
    # rimangono in fallback passthrough e vengono riclassificate come
    # PASSTHROUGH_OK_GAP nel test di parity (non richiedono codice).

    def _schema_reorder_columns(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """reorder_columns: stesso column-set, ordine ricalcolato.

        Runtime: `op_reorder_columns` (columns.py).
        - Se `columns` valorizzata (lista non vuota): le colonne valide
          vengono messe in testa nell'ordine specificato; le rimanenti
          seguono nell'ordine input originale.
        - Se `columns` vuota e `sort_alphabetical=True`: ordine
          alfabetico.
        - Default (entrambe vuote/false): passthrough.

        Le colonne in `columns` ma non presenti nell'input vengono
        ignorate (allineato al filtro runtime
        `[c for c in columns if c in result.columns]`).
        """
        if not input_schemas:
            return []
        input_schema = input_schemas[0]
        by_name = {c.name: c for c in input_schema}

        cols_cfg = config.get("columns") or []
        sort_alpha = bool(config.get("sort_alphabetical", False))

        if cols_cfg:
            valid = [name for name in cols_cfg if name in by_name]
            remaining = [c.name for c in input_schema if c.name not in valid]
            ordered = valid + remaining
        elif sort_alpha:
            ordered = sorted(by_name.keys())
        else:
            ordered = [c.name for c in input_schema]

        return [ColumnSchema(
            name=by_name[name].name,
            type=by_name[name].type,
            source_node=node_id,
            is_calculated=by_name[name].is_calculated,
        ) for name in ordered if name in by_name]

    def _schema_geo_voronoi(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_voronoi: input + sostituzione del tipo della colonna
        geometria a `geometry:polygon`.

        Runtime: `op_geo_voronoi` (spatial.py) preserva tutte le colonne
        di input (`result = gdf.copy()`) e sovrascrive `result['geometry']`
        con poligoni di Voronoi. Il numero di righe non cambia (1
        polygon per punto input).

        Schema delta: cambia il tipo della colonna `geom_column`
        (default `"geometry"`) a `geometry:polygon` e marca la colonna
        come `is_calculated=True`. Se la colonna `geom_column` non
        esiste nello schema input, ritorna passthrough invariato
        (caso degenere, non si tenta di indovinare).
        """
        if not input_schemas:
            return []
        geom_col = config.get("geom_column") or "geometry"
        result = self._copy_schema(input_schemas[0], node_id)
        for i, col in enumerate(result):
            if col.name == geom_col:
                result[i] = ColumnSchema(
                    name=col.name,
                    type="geometry:polygon",
                    source_node=node_id,
                    is_calculated=True,
                )
                break
        return result

    # =========================================================================
    # BATCH 5 — BE-PHASE2-SCHEMA-INFER-006
    # =========================================================================
    # Handler dedicati per multi-input/spatial join + lookup.
    # `geo_overlay` e `geo_symmetric_difference` riusano direttamente
    # `_schema_geo_overlay` legacy (stessa shape: LEFT + RIGHT non-geom
    # con suffisso `_2` su collisione + geometria combinata).

    def _schema_cross_join(
        self,
        input_schemas: List[List[ColumnSchema]],
        node_id: str,
    ) -> List[ColumnSchema]:
        """cross_join: prodotto cartesiano LEFT × RIGHT.

        Runtime: `op_cross_join` (joins.py) usa `pd.merge(how="cross")`.
        Pandas applica i suffissi default `('_x', '_y')` in caso di
        collisione: ENTRAMBE le colonne in collisione vengono
        suffissate (LEFT → `_x`, RIGHT → `_y`); le colonne non in
        collisione restano col nome originale.
        """
        if len(input_schemas) < 2:
            return input_schemas[0] if input_schemas else []

        left_schema = input_schemas[0]
        right_schema = input_schemas[1]
        left_names = {c.name for c in left_schema}
        right_names = {c.name for c in right_schema}
        collisions = left_names & right_names

        result: List[ColumnSchema] = []
        for col in left_schema:
            new_name = f"{col.name}_x" if col.name in collisions else col.name
            result.append(ColumnSchema(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated,
            ))
        for col in right_schema:
            new_name = f"{col.name}_y" if col.name in collisions else col.name
            result.append(ColumnSchema(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated,
            ))
        return result

    def _schema_geo_within(
        self,
        input_schemas: List[List[ColumnSchema]],
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_within: filter-like su LEFT.

        Runtime: `op_geo_within` (spatial.py) esegue uno spatial join
        `predicate="within"` poi proietta SOLO le colonne LEFT
        (`result = joined[keep_cols]`) e fa drop_duplicates. Schema
        delta nullo: output = LEFT.
        """
        if not input_schemas:
            return []
        return self._copy_schema(input_schemas[0], node_id)

    def _schema_geo_nearest(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_nearest: LEFT + RIGHT non-geom + colonna distanza.

        Runtime: `op_geo_nearest` (spatial.py) usa
        `gpd.sjoin_nearest(left, right, how="left", distance_col=...)`.
        - Output preserva tutte le colonne di LEFT.
        - Aggiunge le colonne non-geometriche di RIGHT (geopandas
          rimuove la geometria del RIGHT in sjoin/sjoin_nearest), con
          suffisso `_right` sulle collisioni con LEFT (allineato al
          pattern del legacy `_schema_sjoin`).
        - Aggiunge `output_column` (default `"distance_m"`, real).
        - Il runtime droppa esplicitamente `index_right`, quindi NON
          viene aggiunto allo schema (differisce da `_schema_sjoin`).
        """
        if len(input_schemas) < 2:
            return input_schemas[0] if input_schemas else []

        left_schema = input_schemas[0]
        right_schema = input_schemas[1]
        result = self._copy_schema(left_schema, node_id)
        left_names = {c.name for c in result}

        for col in right_schema:
            if col.type.startswith("geometry"):
                continue
            new_name = (
                f"{col.name}_right" if col.name in left_names else col.name
            )
            result.append(ColumnSchema(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated,
            ))

        output_col = config.get("output_column") or "distance_m"
        if output_col and output_col not in {c.name for c in result}:
            result.append(ColumnSchema(
                name=output_col,
                type="real",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_lookup(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """lookup: single-input value-mapping.

        Runtime: `op_lookup` (analysis.py). NB: dispetto la guidance
        nel prompt outgoing, il registry classifica `inputs: single`
        e il runtime e' un value-map (non un join multi-input).

        Schema delta:
        - `column` o `mapping` mancante / `column` non in input →
          schema invariato (mirror runtime no-op + warning).
        - `output_column` vuoto o uguale a `column`: in-place
          overwrite. Schema-set invariato. Tipo della sorgente
          preservato (i `fillna(default|original)` mantengono il
          tipo dominante; un eventuale shift e' approssimazione
          accettabile).
        - `output_column` valorizzata e diversa da `column`: aggiunge
          una nuova colonna con tipo = tipo della sorgente
          (approssimazione: i valori del `mapping` ereditano la
          tipologia che il runtime `pd.Series.map` produce sul
          dtype originale).
        - `output_column` collide con un'altra colonna esistente
          (non `column`): nessuna nuova colonna, schema invariato.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        mapping = config.get("mapping")
        if not column or not mapping:
            return result
        existing = {c.name for c in result}
        if column not in existing:
            return result

        out_col_cfg = config.get("output_column")
        target = out_col_cfg if out_col_cfg else column
        if target == column:
            return result
        if target in existing:
            return result

        src_col = next(c for c in result if c.name == column)
        result.append(ColumnSchema(
            name=target,
            type=src_col.type,
            source_node=node_id,
            is_calculated=True,
        ))
        return result

    def _copy_schema(
        self,
        schema: List[ColumnSchema],
        node_id: str,
    ) -> List[ColumnSchema]:
        """Helper pure: ricrea lo schema con `source_node` aggiornato a
        `node_id`. Estratto per evitare la replica del list-comprehension
        in tutti gli handler nuovi."""
        return [ColumnSchema(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated,
        ) for col in schema]

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