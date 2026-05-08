"""
Servizio di inferenza e propagazione schema attraverso il grafo canvas.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from app.core.projects.project_context_loader import (
    load_project_source_schema_context,
)
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
            return self._schema_passthrough(input_schemas, node.id)
        return handler(input_schemas, config, node.id)

    def _build_transform_handlers(
        self,
        edges_by_target: dict,
    ) -> Dict[str, Callable[[List[List[ColumnSchema]], dict, str], List[ColumnSchema]]]:
        """Registry transform_op -> schema handler.

        Tutti gli handler esposti dal registry condividono la firma
        canonica `(input_schemas, config, node_id) -> List[ColumnSchema]`.
        Gli handler con signature non standard sono adattati via lambda:

        - `_schema_join` richiede `edges_by_target` (closure su param);
        - `_schema_geo_overlay` richiede `overlay_type` (lambda fissa
          `intersection`/`union`/`difference`/`symmetric_difference`,
          o legge `config["how"]` per `geo_overlay`);
        - `_schema_filter`, `_schema_sort`, `_schema_passthrough`,
          `_schema_cross_join`, `_schema_geo_within` ignorano `config`
          (lambda passa solo `(i, n)`).

        Aliases preservati (presenti nel dispatcher ma non nel
        `transforms_registry.json` — lock parity test):
        - `drop` -> `_schema_drop` (alias storico per `drop_columns`)
        - `geo_to_crs` -> `_schema_passthrough` (alias legacy per
          `geo_reproject`)

        i transform_op tra i marker `# DISPATCH-REGISTRY:start` e
        `# DISPATCH-REGISTRY:end` qui sotto.
        """
        pt = lambda i, c, n: self._schema_passthrough(i, n)
        return {
            # DISPATCH-REGISTRY:start
            # base
            "join": lambda i, c, n: self._schema_join(i, c, n, edges_by_target),
            "sjoin": lambda i, c, n: self._schema_sjoin(i, c, n),
            "aggregate": lambda i, c, n: self._schema_aggregate(i, c, n),
            "formula": lambda i, c, n: self._schema_formula(i, c, n),
            "filter": lambda i, c, n: self._schema_filter(i, n),
            "concat": lambda i, c, n: self._schema_concat(i, c, n),
            "sort": lambda i, c, n: self._schema_sort(i, n),
            "distinct": lambda i, c, n: self._schema_distinct(i, c, n),
            "rename": lambda i, c, n: self._schema_rename(i, c, n),
            "drop": lambda i, c, n: self._schema_drop(i, c, n),
            "drop_columns": lambda i, c, n: self._schema_drop(i, c, n),
            "fill_na": pt,
            "replace": pt,
            "split_column": lambda i, c, n: self._schema_split_column(i, c, n),
            # geo
            "geo_buffer": lambda i, c, n: self._schema_geo_buffer(i, c, n),
            "geo_centroid": lambda i, c, n: self._schema_geo_centroid(i, c, n),
            "geo_area": lambda i, c, n: self._schema_geo_area(i, c, n),
            "geo_clip": lambda i, c, n: self._schema_geo_clip(i, c, n),
            "geo_dissolve": lambda i, c, n: self._schema_geo_dissolve(i, c, n),
            "geo_simplify": pt,
            "geo_convex_hull": lambda i, c, n: self._schema_geo_convex_hull(i, c, n),
            "geo_reproject": pt,
            "geo_intersection": lambda i, c, n: self._schema_geo_overlay(i, c, n, "intersection"),
            "geo_union": lambda i, c, n: self._schema_geo_overlay(i, c, n, "union"),
            "geo_difference": lambda i, c, n: self._schema_geo_overlay(i, c, n, "difference"),
            "geo_to_crs": pt,
            # privacy
            "md5_hash": lambda i, c, n: self._schema_md5_hash(i, c, n),
            "mask_data": lambda i, c, n: self._schema_mask_data(i, c, n),
            # base aggiuntive
            "type_cast": lambda i, c, n: self._schema_type_cast(i, c, n),
            "conditional": lambda i, c, n: self._schema_conditional(i, c, n),
            "text_normalize": lambda i, c, n: self._schema_text_normalize(i, c, n),
            # passthrough espliciti
            "concat_columns": pt,
            "pivot": pt,
            "geo_envelope": pt,
            # spatial
            "geo_from_coords": lambda i, c, n: self._schema_geo_from_coords(i, c, n),
            "add_row_number": lambda i, c, n: self._schema_add_row_number(i, c, n),
            "uuid_generator": lambda i, c, n: self._schema_uuid_generator(i, c, n),
            "string_length": lambda i, c, n: self._schema_string_length(i, c, n),
            "date_extract": lambda i, c, n: self._schema_date_extract(i, c, n),
            "geo_distance": lambda i, c, n: self._schema_geo_distance(i, c, n),
            "geo_to_wkt": lambda i, c, n: self._schema_geo_to_wkt(i, c, n),
            "geo_length": lambda i, c, n: self._schema_geo_length(i, c, n),
            "geo_perimeter": lambda i, c, n: self._schema_geo_perimeter(i, c, n),
            "geo_vertex_count": lambda i, c, n: self._schema_geo_vertex_count(i, c, n),
            "string_extract": lambda i, c, n: self._schema_string_extract(i, c, n),
            "bin": lambda i, c, n: self._schema_bin(i, c, n),
            "string_pad": lambda i, c, n: self._schema_string_pad(i, c, n),
            "geo_bounds_extractor": lambda i, c, n: self._schema_geo_bounds_extractor(i, c, n),
            "geo_count_points_in_polygons": lambda i, c, n: self._schema_geo_count_points_in_polygons(i, c, n),
            "geo_line_builder": lambda i, c, n: self._schema_geo_line_builder(i, c, n),
            "geo_polygon_builder": lambda i, c, n: self._schema_geo_polygon_builder(i, c, n),
            "geo_point_on_surface": lambda i, c, n: self._schema_geo_point_on_surface(i, c, n),
            "geo_boundary": lambda i, c, n: self._schema_geo_boundary(i, c, n),
            "reorder_columns": lambda i, c, n: self._schema_reorder_columns(i, c, n),
            "geo_voronoi": lambda i, c, n: self._schema_geo_voronoi(i, c, n),
            "cross_join": lambda i, c, n: self._schema_cross_join(i, n),
            "geo_within": lambda i, c, n: self._schema_geo_within(i, n),
            "geo_overlay": lambda i, c, n: self._schema_geo_overlay(
                i, c, n, c.get("how", "intersection"),
            ),
            "geo_symmetric_difference": lambda i, c, n: self._schema_geo_overlay(
                i, c, n, "symmetric_difference",
            ),
            "geo_nearest": lambda i, c, n: self._schema_geo_nearest(i, c, n),
            "lookup": lambda i, c, n: self._schema_lookup(i, c, n),
            "table_diff": lambda i, c, n: self._schema_table_diff(i, c, n),
            "melt": lambda i, c, n: self._schema_melt(i, c, n),
            "statistics": lambda i, c, n: self._schema_statistics(i, c, n),
            "window_function": lambda i, c, n: self._schema_window_function(i, c, n),
            # —
            # config-explicit schema contracts; safe deterministic
            # fallback (passthrough) when `output_columns` is absent
            "flatten_json": lambda i, c, n: self._schema_flatten_json(i, c, n),
            "transpose": lambda i, c, n: self._schema_transpose(i, c, n),
            # DISPATCH-REGISTRY:end
        }
    
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
    
    # SCHEMA PER OPERAZIONI SPECIFICHE (base
    # Ogni metodo _schema_xxx simula l'effetto della trasformazione sullo
    # schema, senza eseguire l'operazione. Questo permette al frontend di
    # conoscere le colonne output PRIMA dell'esecuzione della pipeline
    
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
        
        # Colonne da destra con suffisso _R, ESCLUSA la join key
        # se left_on == right_on: la join key di destra e' un duplicato
        # della sinistra (gia' inclusa senza suffisso) → skip
        # se left_on != right_on: la join key di destra resta nel
        # risultato col suo nome originale (senza _R) — e' una colonna
        # significativa, non un attributo collaterale
        # Pre-fix bug: la condizione ternaria aveva entrambi i rami uguali
        # `f"{col.name}_R" if ... else f"{col.name}_R"`), quindi la join
        # key di destra finiva sempre come `id_right_R`, confondendo il
        # downstream sul nome reale della colonna
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
        
        # Colonne da destra (con suffisso per evitare conflitti
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
        
        # Colonne group by (mantengono tipo originale
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
    
    # SCHEMA PER OPERAZIONI GEOMETRICHE
    # Le operazioni geometriche possono cambiare il tipo della colonna geometry
    # es: buffer punto → poligono, centroid poligono → punto) e aggiungere
    # colonne metriche (area_m2, length_m, distance_m
    
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
        
        # Colonne da destra (con suffisso per evitare conflitti
        left_names = {col.name for col in left_schema}
        for col in right_schema:
            # Skip geometry columns from right (result uses combined geometry
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
    
    # TRASFORMAZIONI PRIVACY
    # Operazioni di anonimizzazione: hash MD5 e mascheramento dati
    # Non cambiano il tipo colonna, ma possono aggiungere nuove colonne hash
    
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
    
    # TRASFORMAZIONI BASE AGGIUNTIVE
    # Operazioni aggiunte successivamente: cast tipi, condizionali
    # normalizzazione testo, creazione geometria da coordinate
    
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

        # left schema (primo input). Se manca anche un secondo input
        # il runtime degrada graziosamente; lo schema FE annunciato resta
        # comunque coerente col success-path canonico (left + distance
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
            # Ordine pattern: ordina per indice di gruppo (1-based
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

        # Preserva il tipo originale di group_column se presente
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
        `representative_point` e lo scrive in `output_column`
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

    # — BE-PHASE2-SCHEMA-INFER-005
    # Handler dedicati per il riordino colonne (`reorder_columns`) e per
    # `geo_voronoi`. Le altre 4 candidati (dedup_advanced
    # geo_clean_topology, geo_make_valid, geo_explode) sono rows-only
    # rimangono in fallback passthrough e vengono riclassificate come
    # PASSTHROUGH_OK_GAP nel test di parity (non richiedono codice

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
        di input (`result = gdf.copy`) e sovrascrive `result['geometry']`
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


    def _schema_table_diff(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """table_diff: confronto LEFT vs RIGHT con metadati diff.

        Runtime: `op_table_diff` (reshape.py) e' multi-input. Output:
        - `key_cols` (= `left_keys`, types da LEFT)
        - `common_value_cols` (intersezione di non-key cols
          dopo key-rename RIGHT→LEFT; types da LEFT; filtrate
          opzionalmente da `compare_columns`)
        - `only_old_cols` (LEFT non-key not in RIGHT post-rename;
          types da LEFT)
        - `only_new_cols` (RIGHT non-key post-rename not in LEFT;
          types da RIGHT)
        - `_diff_status` (str), `_diff_columns` (str),
          `_diff_old_values` (str)

        Fallback runtime → passthrough di LEFT:
        - `left_keys`/`right_keys` mancanti o lunghezze diverse;
        - una key non esiste in LEFT/RIGHT.
        """
        if not input_schemas:
            return []
        if len(input_schemas) < 2:
            return self._copy_schema(input_schemas[0], node_id)

        left_schema = input_schemas[0]
        right_schema = input_schemas[1]

        left_keys = self._normalize_str_list(config.get("left_keys"))
        right_keys = self._normalize_str_list(config.get("right_keys"))

        if (
            not left_keys
            or not right_keys
            or len(left_keys) != len(right_keys)
        ):
            return self._copy_schema(left_schema, node_id)

        left_by_name = {c.name: c for c in left_schema}
        right_by_name = {c.name: c for c in right_schema}
        if not all(k in left_by_name for k in left_keys):
            return self._copy_schema(left_schema, node_id)
        if not all(k in right_by_name for k in right_keys):
            return self._copy_schema(left_schema, node_id)

        # Apply key-rename to RIGHT (rk → lk where lk != rk
        key_rename = {
            rk: lk for lk, rk in zip(left_keys, right_keys) if lk != rk
        }
        right_post: List[ColumnSchema] = []
        for col in right_schema:
            new_name = key_rename.get(col.name, col.name)
            right_post.append(ColumnSchema(
                name=new_name,
                type=col.type,
                source_node=col.source_node,
                is_calculated=col.is_calculated,
            ))
        right_post_by_name = {c.name: c for c in right_post}

        key_cols = list(left_keys)
        key_set = set(key_cols)
        left_non_key = [c.name for c in left_schema if c.name not in key_set]
        right_non_key = [c.name for c in right_post if c.name not in key_set]

        common_value_cols = [c for c in left_non_key if c in right_non_key]
        compare_cols = self._normalize_str_list(config.get("compare_columns"))
        if compare_cols:
            common_value_cols = [
                c for c in compare_cols if c in common_value_cols
            ]

        only_old_cols = [c for c in left_non_key if c not in right_non_key]
        only_new_cols = [c for c in right_non_key if c not in left_non_key]

        result: List[ColumnSchema] = []
        for k in key_cols:
            col = left_by_name[k]
            result.append(ColumnSchema(
                name=k,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
        for c in common_value_cols:
            col = left_by_name[c]
            result.append(ColumnSchema(
                name=c,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
        for c in only_old_cols:
            col = left_by_name[c]
            result.append(ColumnSchema(
                name=c,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
        for c in only_new_cols:
            col = right_post_by_name[c]
            result.append(ColumnSchema(
                name=c,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
        for fixed in ("_diff_status", "_diff_columns", "_diff_old_values"):
            result.append(ColumnSchema(
                name=fixed,
                type="str",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_melt(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """melt: wide → long.

        Runtime: `op_melt` (reshape.py) usa `pd.melt`. Output:
        - `id_columns` (preservate, types da input)
        - `var_name` (str, default `"variable"`) — sempre str
        - `value_name` (default `"value"`) — tipo:
          - se i `value_columns` (o tutti i non-id cols se vuoto)
            sono omogenei in tipo, usa quel tipo;
          - altrimenti `str` (mirror del comportamento pandas che
            promuove a `object` su tipi misti).

        `id_columns`/`value_columns` accettati come list[str] o str
        comma-separated (il runtime fa il filtro su `df.columns`, qui
        replichiamo).
        """
        if not input_schemas:
            return []
        input_schema = input_schemas[0]
        by_name = {c.name: c for c in input_schema}

        id_cols = [
            c for c in self._normalize_str_list(config.get("id_columns"))
            if c in by_name
        ]
        raw_value = config.get("value_columns")
        if raw_value is None or raw_value == "" or raw_value == []:
            value_cols_names = [
                c.name for c in input_schema if c.name not in set(id_cols)
            ]
        else:
            normalized = [
                c for c in self._normalize_str_list(raw_value)
                if c in by_name
            ]
            value_cols_names = normalized or [
                c.name for c in input_schema if c.name not in set(id_cols)
            ]

        var_name = config.get("var_name") or "variable"
        value_name = config.get("value_name") or "value"

        types = {by_name[c].type for c in value_cols_names if c in by_name}
        if len(types) == 1:
            val_type = next(iter(types))
        else:
            val_type = "str"

        result: List[ColumnSchema] = []
        for c in id_cols:
            col = by_name[c]
            result.append(ColumnSchema(
                name=c,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
        result.append(ColumnSchema(
            name=var_name,
            type="str",
            source_node=node_id,
            is_calculated=True,
        ))
        result.append(ColumnSchema(
            name=value_name,
            type=val_type,
            source_node=node_id,
            is_calculated=True,
        ))
        return result

    def _schema_statistics(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """statistics: input + 1 colonna per ogni stat valida.

        Runtime: `op_statistics` (analysis.py).
        - prefix default = `f"{column}_"` (se `output_prefix` vuoto)
        - col name = `f"{prefix}{stat}"`
        - Tipo:
          - `count` → `int` (count pandas)
          - tutti gli altri (`min`, `max`, `sum`, `mean`, `median`,
            `std`, `var`, `q25`, `q75`) → `real` (numerici).
        - Stats unknown ignorate (allineato al `if stat_name in
          stat_funcs` runtime).
        - `column` mancante / non in input → schema invariato (no-op
          + warning runtime).
        - Default `stats = ["count","min","max","mean","median","std"]`.
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

        raw_stats = config.get("stats")
        if raw_stats is None:
            stats = ["count", "min", "max", "mean", "median", "std"]
        elif isinstance(raw_stats, str):
            stats = [s.strip() for s in raw_stats.split(",") if s.strip()]
        elif isinstance(raw_stats, list):
            stats = [str(s).strip() for s in raw_stats if str(s).strip()]
        else:
            stats = ["count", "min", "max", "mean", "median", "std"]

        valid_stats = {
            "count", "min", "max", "sum", "mean", "median",
            "std", "var", "q25", "q75",
        }
        prefix = config.get("output_prefix") or f"{column}_"

        for stat in stats:
            if stat not in valid_stats:
                continue
            col_name = f"{prefix}{stat}"
            if col_name in existing:
                continue
            stat_type = "int" if stat == "count" else "real"
            result.append(ColumnSchema(
                name=col_name,
                type=stat_type,
                source_node=node_id,
                is_calculated=True,
            ))
            existing.add(col_name)
        return result

    def _schema_window_function(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """window_function: input + 1 colonna `output_column`.

        Runtime: `op_window_function` (aggregation.py).
        - `output_column` default = `f"{column}_{function}"`.
        - Funzioni valide: rank, dense_rank, cumsum, cumcount, lag,
          lead, pct_change, running_mean. Funzione unknown → no-op
          (passthrough).
        - Tipo output by function:
          - `rank`, `dense_rank`, `pct_change`, `running_mean`
            → `real` (pandas float64);
          - `cumcount` → `int`;
          - `cumsum`, `lag`, `lead` → preserva il tipo della colonna
            sorgente (approssimazione: lag/lead di int possono
            promuovere a float per NaN ma mantengono la semantica
            del dtype originale).
        - `column` mancante / non in input → schema invariato.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        if not column:
            return result
        by_name = {c.name: c for c in result}
        if column not in by_name:
            return result

        function = config.get("function") or "rank"
        valid_functions = {
            "rank", "dense_rank", "cumsum", "cumcount",
            "lag", "lead", "pct_change", "running_mean",
        }
        if function not in valid_functions:
            return result  # mirror runtime warning + return df

        output_col = config.get("output_column") or f"{column}_{function}"
        if output_col in by_name:
            return result

        if function in ("rank", "dense_rank", "pct_change", "running_mean"):
            out_type = "real"
        elif function == "cumcount":
            out_type = "int"
        else:  # cumsum, lag, lead
            out_type = by_name[column].type

        result.append(ColumnSchema(
            name=output_col,
            type=out_type,
            source_node=node_id,
            is_calculated=True,
        ))
        return result

    # — BE-PHASE2-SCHEMA-INFER-009-ZERO-RESIDUALS
    # Handler dedicati per i 2 transform precedentemente classificati
    # `MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT` (output dipendente dai dati
    # La direzione e' config-explicit: l'utente dichiara lo schema atteso
    # via `output_columns`. Quando il contratto e' assente, fallback
    # deterministico a passthrough (NON sampling, NON I/O
    # Forma del contratto `output_columns` (accettata in entrambe le
    # forme, mixed allowed
    # `List[str]` : ogni stringa diventa una colonna `str`
    # `List[{name, type}]`: tipo letto da `type`, default `str` se
    # vuoto/missing
    # Entry malformate (None, dict senza `name`, non-str/non-dict
    # vengono saltate (mirror della no-throw policy degli altri handler

    def _schema_flatten_json(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """flatten_json: input + N colonne dichiarate via `output_columns`.

        Runtime: `op_flatten_json` (analysis.py) espande le chiavi del
        dict parsato (data-dependent). Il contratto schema-inference
        e' invece **config-explicit** (BE-PHASE2-SCHEMA-INFER-009):
        l'utente dichiara le chiavi attese tramite
        `config["output_columns"]`. Le chiavi dichiarate vengono
        prefissate da `prefix` o, se assente, da `f"{column}_"`
        (allineato al runtime `col_prefix = prefix or f"{column}_"`).

        Fallback deterministico: se `output_columns` e' assente o
        vuota, lo schema dichiarato e' il passthrough dell'input
        (mirror del runtime no-op + warning, e mirror del fallback
        pre-009 che restava in `_schema_passthrough`).

        Collisioni con colonne esistenti: skip (no duplicate),
        allineato al pattern di tutti gli handler additivi.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        output_cols = self._normalize_output_columns(config.get("output_columns"))
        if not output_cols:
            return result

        existing = {c.name for c in result}
        column = config.get("column") or ""
        prefix = config.get("prefix") or (f"{column}_" if column else "")
        for entry in output_cols:
            name = entry["name"]
            full_name = f"{prefix}{name}" if prefix else name
            if not full_name or full_name in existing:
                continue
            result.append(ColumnSchema(
                name=full_name,
                type=entry["type"],
                source_node=node_id,
                is_calculated=True,
            ))
            existing.add(full_name)
        return result

    def _schema_transpose(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """transpose: schema dichiarato via `output_columns`.

        Runtime: `op_transpose` (reshape.py) traspone le righe in
        colonne; i nomi delle nuove colonne dipendono dai valori
        della colonna `id_column` (data-dependent) o sono
        `f"col_{i}"` con N runtime-dependent. Il contratto
        schema-inference e' **config-explicit**: l'utente dichiara
        le colonne output attese tramite `config["output_columns"]`.

        Output:
        - se `id_column` e' specificata e presente nell'input, e'
          preservata in testa col tipo originale (mirror del runtime
          `result.rename(columns={"index": id_column})`);
        - tutte le entry di `output_columns` vengono aggiunte come
          colonne dichiarate.

        Fallback deterministico: se `output_columns` e' assente o
        vuota, lo schema dichiarato e' il passthrough dell'input.
        """
        if not input_schemas:
            return []
        output_cols = self._normalize_output_columns(config.get("output_columns"))
        if not output_cols:
            return self._copy_schema(input_schemas[0], node_id)

        input_by_name = {c.name: c for c in input_schemas[0]}
        result: List[ColumnSchema] = []
        seen: set = set()

        id_column = config.get("id_column") or ""
        if id_column and id_column in input_by_name:
            col = input_by_name[id_column]
            result.append(ColumnSchema(
                name=id_column,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
            seen.add(id_column)

        for entry in output_cols:
            name = entry["name"]
            if not name or name in seen:
                continue
            result.append(ColumnSchema(
                name=name,
                type=entry["type"],
                source_node=node_id,
                is_calculated=True,
            ))
            seen.add(name)
        return result

    def _normalize_output_columns(
        self,
        raw,
    ) -> List[Dict[str, str]]:
        """Helper: normalizza `output_columns` in `List[{name, type}]`.

        Accetta:
        - `None` / non-list/non-str → `[]`;
        - `str` singolo non vuoto → `[{name: <s>, type: 'str'}]`;
        - `list` mista di:
          - `str` non vuoti → `{name, type: 'str'}`;
          - `dict` con `name` non vuoto → `{name, type: dict.type or 'str'}`;
          - altri valori (None, dict senza name, non-str/non-dict) → skip.

        Tipi non validati: il FE puo' inviare qualunque stringa per
        `type`. Default `'str'` per entry vuote/missing — coerente
        col pattern del registry per i nuovi field di nominazione.
        """
        if raw is None:
            return []
        if isinstance(raw, str):
            s = raw.strip()
            return [{"name": s, "type": "str"}] if s else []
        if not isinstance(raw, list):
            return []

        out: List[Dict[str, str]] = []
        for entry in raw:
            if isinstance(entry, str):
                s = entry.strip()
                if s:
                    out.append({"name": s, "type": "str"})
            elif isinstance(entry, dict):
                name = str(entry.get("name") or "").strip()
                if not name:
                    continue
                type_ = str(entry.get("type") or "").strip() or "str"
                out.append({"name": name, "type": type_})
            # else: skip non-str/non-dict entries
        return out

    def _normalize_str_list(self, raw) -> List[str]:
        """Helper: normalizza un valore di config in lista di stringhe.

        - `str` → split per virgola;
        - `list` → ogni elemento stringificato e strippato;
        - `None`/altro → lista vuota.

        Vuoti/whitespace-only filtrati. Usato per `id_columns`,
        `value_columns`, `left_keys`, `right_keys`, `compare_columns`,
        `stats` ecc. dove il FE puo' inviare entrambe le shape.
        """
        if raw is None:
            return []
        if isinstance(raw, str):
            return [c.strip() for c in raw.split(",") if c.strip()]
        if isinstance(raw, list):
            return [str(c).strip() for c in raw if str(c).strip()]
        return []

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

    # METODI DI SUPPORTO

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
