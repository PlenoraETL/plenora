"""Inferenza degli schemi sul grafo del canvas progetto."""
from app.core.estrai.query_projection import QueryProjectionRenderer
from app.core.estrai.query_generator import QueryGenerator
from app.core.arricchisci.enrichment_service import resolve_enrichment_output_mapping, generator_mapping_column_names
from app.shared.execution.api_mapping import mapping_destination
from app.core.arricchisci.canvas_orchestrator import _merge_node_data
from app.shared.execution.node_fields import node_field
from app.shared.execution.result_grouping import result_group_plan
from app.shared.execution.schema_column_plans import aggregation_output_type, type_family
from dataclasses import dataclass, replace
from copy import copy
from types import SimpleNamespace
from app.core.manipola.plugin_executor import get_executor
from app.core.manipola.lookup_type_plan import lookup_type_plan
from app.core.manipola.schema_source_plans import static_columns, unique_source_names
from app.shared.execution.source_column_plan import source_column_plan
from app.shared.execution.join_preparation import multi_join_renames, normalize_key_list as _normalize_key_list, normalize_join_pairs as _normalize_join_pairs
from typing import Dict, List, Optional, Any, Tuple, Mapping
import uuid

from app.shared.execution.column_selection import expand_column_plan, select_column_plan
from app.core.projects.project_context_loader import (
    load_project_source_schema_context,
)
from app.core.projects.schema_transform_handlers import TransformSchemaHandlers
from app.core.projects.canvas_models import CanvasState, Node
from app.core.projects.types import (
    EdgesByTarget,
    JsonObject,
    SchemaMap,
    SchemaRows,
    TransformHandlerRegistry,
)
from app.core.manipola.transform_ordering import incoming_edge_sort_key_for_nodes
from app.shared.execution.config_column_refs import resolve_config_column_refs
from app.shared.execution.output_names import make_unique_column_names
from app.shared.execution.output_column_refs import resolve_output_column_refs, unique_column_names
from app.shared.execution.schema_types import ColumnSchema, InferredSchema
from app.core.projects import schema_execution_sources
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _node_config(node: Any, keys: tuple[str, ...]) -> Mapping[str, Any]:
    return {key: node_field(node, key) for key in keys}


def _stable_column_id(*parts: str) -> str:
    return f"col_{uuid.uuid5(uuid.NAMESPACE_URL, ':'.join(str(part) for part in parts)).hex}"


@dataclass(frozen=True)
class _NodeSchemaContext:
    node: Any
    nodes_by_id: dict
    edges_by_target: dict
    project_id: str


class SchemaInferenceService:
    """Inferisce gli schemi delle colonne attraverso il grafo dei nodi."""
    
    def __init__(self, project_service: Any, *, api_configs: Optional[dict[str, dict]] = None) -> None:
        self.project_service = project_service
        self.api_configs = api_configs
        self._transform_handlers = TransformSchemaHandlers(ColumnSchema)

        self._schema_cache: Dict[str, List[ColumnSchema]] = {}
        # node_id -> (stato dei nomi, motivo), separato da tipi e ordine.
        self._status_cache: Dict[str, Tuple[str, str]] = {}
        self._types_status_cache: Dict[str, Tuple[str, str]] = {}
        self._order_status_cache: Dict[str, str] = {}
        self._project_types = {}
        self._canvas_edges = []
        self._independent_outputs = set()
        self._sql_providers = {}
    
    def _new_call(self) -> "SchemaInferenceService":
        """Isola le cache per chiamata, conservando servizi e configurazione."""
        call = copy(self)
        call._schema_cache = {}
        call._status_cache = {}
        call._types_status_cache = {}
        call._order_status_cache = {}
        call._project_types = {}
        call._canvas_edges = []
        call._independent_outputs = set()
        call._sql_providers = {}
        return call

    def infer_schemas_response(self, project_id: str, canvas: Optional[CanvasState] = None) -> JsonObject:
        """Colonne e stati provengono dalla medesima chiamata isolata."""
        call = self._new_call()
        schemas = call._infer_all_schemas(project_id, canvas)
        states = {}
        for node_id, columns in schemas.items():
            names, reason = call._status_cache.get(node_id, ("certain", ""))
            types, types_reason = call._types_status_cache.get(node_id, (names, reason))
            order = call._order_status_cache.get(node_id, names)
            states[node_id] = {"names_status": names, "types_status": types,
                               "order_status": order, "reason": reason or types_reason}
            for col in columns:
                col.setdefault("optional", False)
                col["name"] = str(col["name"])
                col["source_name"] = str(col.get("source_name", col["name"]))
        return {"schemas": schemas, "schema_states": states}

    def _project_type(self, context: _NodeSchemaContext) -> str:
        if context.project_id not in self._project_types:
            loader = getattr(self.project_service, "get_project_metadata", None)
            metadata = loader(context.project_id) if loader else None
            kind = getattr(metadata, "project_type", None)
            if kind not in ("estrai", "arricchisci", "manipola"):
                kinds = {node.type for node in context.nodes_by_id.values()}
                kind = "arricchisci" if "api_enrich" in kinds else "estrai" if "source" in kinds else "manipola"
            self._project_types[context.project_id] = kind
        return self._project_types[context.project_id]

    def infer_all_schemas(
        self, project_id: str, canvas: Optional[CanvasState] = None,
    ) -> SchemaMap:
        """Inferisce il canvas fornito o quello salvato con cache dedicate."""
        return self._new_call()._infer_all_schemas(project_id, canvas)

    def infer_node_schema(self, project_id: str, node_id: str) -> SchemaRows:
        """Inferisce un nodo con cache dedicate alla chiamata."""
        return self._new_call()._infer_single_node_schema(project_id, node_id)

    def _infer_all_schemas(
        self, project_id: str, canvas: Optional[CanvasState] = None,
    ) -> SchemaMap:
        """
        Inferisce tutti i nodi del canvas fornito, oppure carica quello salvato.
        Il canvas fornito non viene mai persistito.
        Restituisce una mappa da identificativo nodo a colonne con tipo.
        """
        logger.info(f"Inferenza schema per progetto: {project_id}")

        if canvas is None:
            canvas = self.project_service.get_project_canvas(project_id)
        if not canvas:
            logger.warning(f"Canvas non trovato per {project_id}")
            return {}
        
        nodes_by_id = {n.id: n for n in canvas.nodes}
        edges_by_target = self._group_edges_by_target(canvas.edges)

        result = {}
        for node in canvas.nodes:
            schema = self._infer_node_schema(
                node=node, nodes_by_id=nodes_by_id,
                edges_by_target=edges_by_target, project_id=project_id,
            )
            result[node.id] = [col.to_dict() for col in schema]

        logger.info(f"Schema inferito per {len(result)} nodi")
        return result
    
    def _infer_single_node_schema(self, project_id: str, node_id: str) -> SchemaRows:
        """
        Calcola lo schema per un singolo nodo.
        Utile per aggiornamenti incrementali.
        """
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

        Faccia tipizzata per l'engine di validazione canvas:
        condivide la STESSA computazione del metodo pubblico (``.columns`` ==
        ``infer_node_schema(...)``) e vi aggiunge lo ``status``. Nessuna
        duplicazione di logica → niente drift.
        """
        call = self._new_call()
        rows = call._infer_single_node_schema(project_id, node_id)
        # La chiamata isolata popola lo stato del nodo e dei suoi antenati.
        # Canvas o nodo assente → il nodo
        # non è calcolato → default `certain` (problema strutturale, preso altrove).
        status, reason = call._status_cache.get(node_id, ("certain", ""))
        types_status, types_reason = call._types_status_cache.get(node_id, (status, reason))
        order_status = call._order_status_cache.get(node_id, status)
        combined = "indeterminate" if "indeterminate" in (status, types_status, order_status) else "certain"
        return InferredSchema(columns=rows, status=combined, reason=reason or types_reason,
                              names_status=status, types_status=types_status, order_status=order_status)

    def _infer_node_schema(
        self, node: Any, nodes_by_id: dict, edges_by_target: dict, project_id: str,
    ) -> List[ColumnSchema]:
        """Un solo boundary locale protegge handler, identita e serializzazione."""
        try:
            schema = self._infer_node_schema_unchecked(node, nodes_by_id, edges_by_target, project_id)
            # La stessa serializzazione usata da tutti gli endpoint deve riuscire qui.
            for column in schema:
                column.to_dict()
            return schema
        except Exception as exc:
            # Boundary per nodo: anche un handler/plugin guasto non interrompe la mappa.
            logger.warning("Schema issue for node %s: %s", node.id, exc)
            inputs = self._get_input_schemas(node.id, nodes_by_id, edges_by_target, project_id)
            schema = inputs[0] if inputs else []
            self._status_cache[node.id] = ("indeterminate", str(exc))
            self._types_status_cache[node.id] = ("indeterminate", str(exc))
            self._order_status_cache[node.id] = "indeterminate"
            self._schema_cache[node.id] = schema
            return schema

    def _infer_node_schema_unchecked(
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
        else:
            self._status_cache[node.id] = ("indeterminate", f"Tipo nodo non gestito: {node.type}")

        status_edges = self._status_edges(context)
        self._record_node_status(node, replace(context, edges_by_target=status_edges))
        if any(self._order_status_cache.get(edge.source) == "indeterminate"
               for edge in status_edges.get(node.id, [])):
            self._order_status_cache[node.id] = "indeterminate"
        inherited_types = [self._types_status_cache.get(edge.source, self._status_cache.get(edge.source, ("certain", "")))
                           for edge in status_edges.get(node.id, [])]
        if any(state[0] == "indeterminate" for state in inherited_types):
            self._types_status_cache[node.id] = ("indeterminate", "Tipi a monte incerti")
        schema = [replace(col, id=getattr(col, "id", None) or _stable_column_id(node.id, col.name),
                          source_name=col.source_name if col.source_name is not None else col.name)
                  for col in schema]
        counts = {}
        for col in schema:
            counts[col.id] = counts.get(col.id, 0) + 1
        occurrences = {}
        unique = []
        for col in schema:
            identity = col.id
            if counts[identity] > 1:
                key = (identity, col.name)
                occurrence = occurrences.get(key, 0)
                occurrences[key] = occurrence + 1
                identity = _stable_column_id(identity, col.name, str(occurrence))
            unique.append(replace(col, id=identity))
        schema = unique
        self._schema_cache[node.id] = schema
        return schema

    @staticmethod
    def _merge_schema_identities(schema: List[ColumnSchema], inputs: List[List[ColumnSchema]]) -> List[ColumnSchema]:
        """Le copie suffissate dipendono dall'identit? sorgente e dal nome finale."""
        origins = {}
        for input_schema in inputs:
            for col in input_schema:
                origins.setdefault(col.id, set()).add(col.name)
        return [replace(col, id=_stable_column_id(col.id, col.name))
                if col.id and col.id in origins and col.name not in origins[col.id] else col
                for col in schema]

    def _status_edges(self, context: _NodeSchemaContext) -> EdgesByTarget:
        """L'incertezza segue gli stessi ingressi consumati dal nodo."""
        edges = context.edges_by_target
        if context.node.id in self._independent_outputs or context.node.type in self.NON_COLUMN_TYPES:
            return {**edges, context.node.id: []}
        if context.node.type == "api_enrich" or (context.node.type == "result" and self._project_type(context) == "arricchisci"):
            return {**edges, context.node.id: edges.get(context.node.id, [])[:1]}
        return edges

    def _record_node_status(self, node, context: _NodeSchemaContext) -> None:
        """Propaga soltanto l'incertezza dei nomi, indipendente dai tipi."""
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
            "datasource_node": self._infer_file_source_node_schema,
            "staticValues": self._infer_static_node_schema,
            "multi_join": self._infer_multi_join_node_schema,
            "feature_source": self._infer_feature_source_node_schema,
            "project_source": self._infer_project_source_node_schema,
            "project_input": self._infer_project_input_node_schema,
            "transform": self._infer_transform_node_schema,
            "api_enrich": self._infer_api_enrich_node_schema,
            "result": self._infer_result_node_schema,
            "comment": lambda context: [],
            "carica": lambda context: [],
            "carica_arcgis": lambda context: [],
        }
        # NODE-DISPATCH-REGISTRY:end
        return handlers

    # Le chiavi visuali dei tre editor sono tradotte prima del canvas backend.
    NON_COLUMN_TYPES = frozenset({"comment", "carica", "carica_arcgis"})
    EDITOR_NODE_ALIASES = {"tableNode": "source", "resultNode": "result",
                           "featureLayerNode": "feature_source", "datasourceNode": "datasource_node",
                           "caricaArcgis": "carica_arcgis"}

    def _infer_static_node_schema(self, context: _NodeSchemaContext) -> List[ColumnSchema]:
        """I valori statici sono configurazione: usa il caricatore effettivo."""

        fields = node_field(context.node, "fields") or []
        return [ColumnSchema(
            name=col["name"], type=col["type"],
            id=_stable_column_id(context.node.id, col["name"]),
            source_name=col["name"], source_node=context.node.id,
        ) for col in static_columns(fields)]

    def _infer_multi_join_node_schema(self, context: _NodeSchemaContext) -> List[ColumnSchema]:
        """Propaga i passi del multi-join con la preparazione dei nomi runtime."""

        inputs = self._get_input_schemas(context.node.id, context.nodes_by_id,
                                        context.edges_by_target, context.project_id)
        if not inputs:
            self._status_cache[context.node.id] = ("indeterminate", "Multi-join senza ingressi")
            return []
        node = context.node
        config = (node.data or {}).get("config", {})
        keys = _normalize_key_list(node_field(node, "join_keys") or config.get("join_keys"))
        ids = node_field(node, "join_key_ids") or config.get("join_key_ids") or []
        by_id = {col.id: col.name for schema in inputs for col in schema if col.id}
        if ids:
            keys = [by_id.get(id_, keys[i] if i < len(keys) else None) for i, id_ in enumerate(ids)]
            keys = [key for key in keys if key]
        left = _normalize_key_list(node_field(node, "left_keys") or config.get("left_keys"))
        right = _normalize_key_list(node_field(node, "right_keys") or config.get("right_keys"))
        if not left or not right:
            pair_left, pair_right = _normalize_join_pairs(node_field(node, "join_pairs") or config.get("join_pairs"))
            if pair_left and pair_right:
                left, right = pair_left, pair_right
        paired = len(inputs) == 2 and bool(left or right)
        if paired and (not left or not right or len(left) != len(right)):
            raise ValueError("Multi-join: coppie di chiavi non valide")
        if not paired:
            if not keys:
                common = set(col.name for col in inputs[0])
                for schema in inputs[1:]:
                    common &= {col.name for col in schema}
                keys = list(common)
            left = right = keys
        result = inputs[0]
        if not left or not right:
            return result
        for index, schema in enumerate(inputs[1:], start=2):
            if not set(left) <= {col.name for col in result} or not set(right) <= {col.name for col in schema}:
                return inputs[0]
            renames = multi_join_renames([col.name for col in result], [col.name for col in schema],
                                        left, right, index, paired=paired)
            shared = {lk for lk, rk in zip(left, right) if lk == rk}
            dropped = {rk for lk, rk in zip(left, right) if lk != rk and rk not in left}
            renamed = [replace(col, name=renames.get(col.name, col.name)) for col in schema]
            collisions = ({col.name for col in result} & {col.name for col in renamed}) - shared
            result = [replace(col, name=f"{col.name}_x" if col.name in collisions else col.name) for col in result]
            result += [replace(col, name=f"{col.name}_y" if col.name in collisions else col.name)
                       for col in renamed if col.name not in shared]
            result = [col for col in result if col.name not in dropped]
        self._types_status_cache[node.id] = ("indeterminate", "I merge possono promuovere i tipi in presenza di righe non abbinate")
        return self._merge_schema_identities(result, inputs)

    def _infer_source_node_schema(
        self,
        context: _NodeSchemaContext,
    ) -> List[ColumnSchema]:
        if not isinstance(getattr(context.node, "table_name", None), str) or not context.node.table_name:
            return self._infer_source_schema(context.node)
        rows = []
        QueryProjectionRenderer(QueryGenerator(self._sql_provider(context))).generate_select([context.node], context.node, column_metadata=rows)
        if any(not row["name"] for row in rows):
            self._status_cache[context.node.id] = ("indeterminate", "SQL manuale senza alias: nome dipendente dal database")
        if any(not row.get("type") for row in rows):
            self._types_status_cache[context.node.id] = ("indeterminate", "Tipo SQL non dichiarato")
        return [ColumnSchema(**{**row, "type": row.get("type") or "str",
                                "id": row.get("id") or _stable_column_id(row.get("source_node") or context.node.id, row.get("source_name") or row["name"])}) for row in rows if row["name"]]

    def _infer_file_source_node_schema(
        self,
        context: _NodeSchemaContext,
    ) -> List[ColumnSchema]:
        return self._infer_file_source_schema(context.node)

    def _infer_feature_source_node_schema(
        self,
        context: _NodeSchemaContext,
    ) -> List[ColumnSchema]:
        # feature_source (ArcGIS feature layer) porta colonne dichiarate sul
        # nodo nella stessa forma di file_source → riusa l'estrazione (no-drift).
        node = context.node
        columns = node.columns or (node.data or {}).get("columns", [])
        return [ColumnSchema(name=col.name, type=col.type,
                             id=getattr(col, "id", None) or _stable_column_id(node.id, col.name),
                             source_name=col.name, source_node=node.id)
                for col in [c if hasattr(c, "name") else SimpleNamespace(**c) for c in columns]
                if getattr(col, "visible", True)]

    def _infer_project_source_node_schema(
        self,
        context: _NodeSchemaContext,
    ) -> List[ColumnSchema]:
        return self._infer_project_source_schema(context.node)

    def _infer_project_input_node_schema(
        self,
        context: _NodeSchemaContext,
    ) -> List[ColumnSchema]:
        if getattr(context.node, "input_kind", None) != "file":
            return []
        self._status_cache[context.node.id] = (
            "indeterminate", "Il file runtime può contenere colonne aggiuntive e tipi diversi dal contratto atteso",
        )
        columns = []
        for col in getattr(context.node, "expected_columns", None) or []:
            if not isinstance(col, dict) or not col.get("name"):
                continue
            columns.append(
                ColumnSchema(
                    name=str(col["name"]),
                    type=str(col.get("type") or "str"),
                    id=col.get("id") or _stable_column_id(context.node.id, str(col["name"])),
                    source_name=str(col["name"]),
                    source_node=context.node.id,
                )
            )
        return columns

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
        input_schemas = self._first_input_schema(context)
        return self._infer_api_enrich_schema(context.node, input_schemas)

    def _first_input_schema(self, context: _NodeSchemaContext) -> List[List[ColumnSchema]]:
        """Arricchisci usa il primo arco nel canvas, senza ordinamento per handle."""
        edges = context.edges_by_target.get(context.node.id, [])
        if not edges:
            return []
        source = context.nodes_by_id.get(edges[0].source)
        return [self._infer_node_schema(source, context.nodes_by_id, context.edges_by_target,
                                        context.project_id)] if source else []

    def _sql_provider(self, context: _NodeSchemaContext) -> str:
        if context.project_id in self._sql_providers:
            return self._sql_providers[context.project_id]
        loader = getattr(self.project_service, "get_project_metadata", None)
        metadata = loader(context.project_id) if loader else None
        candidate = getattr(metadata, "default_connection_id", None)
        connection_id = candidate if isinstance(candidate, str) else None
        try:
            provider = schema_execution_sources.load_sql_provider(connection_id) if connection_id else "postgres"
        except Exception:
            provider = "unknown"
            self._status_cache[context.node.id] = ("indeterminate", "Provider non leggibile: normalizzazione dei nomi incerta")
        self._sql_providers[context.project_id] = provider
        return provider

    def _infer_sql_result(self, context: _NodeSchemaContext) -> List[ColumnSchema]:
        nodes = list(context.nodes_by_id.values())
        edges = self._canvas_edges or [edge for group in context.edges_by_target.values() for edge in group]
        provider = self._sql_provider(context)
        rows = schema_execution_sources.sql_projection(nodes, edges, context.node, provider)
        self._independent_outputs.add(context.node.id)
        self._status_cache[context.node.id] = (("indeterminate", "SQL manuale senza alias: nome dipendente dal database")
                                                   if any(not row["name"] for row in rows) else
                                                   (("indeterminate", "Provider non leggibile") if provider == "unknown" else ("certain", "")))
        self._order_status_cache[context.node.id] = "certain"
        if any(not row.get("type") for row in rows):
            self._types_status_cache[context.node.id] = ("indeterminate", "Tipo di espressioni SQL o aggregazioni non verificato dal database")
        else:
            self._types_status_cache[context.node.id] = ("certain", "")
        return [ColumnSchema(**{**row, "type": row.get("type") or "str",
                                "id": row.get("id") or _stable_column_id(row.get("source_node") or context.node.id, row.get("source_name") or row["name"])}) for row in rows if row["name"]]

    def _infer_result_node_schema(
        self,
        context: _NodeSchemaContext,
    ) -> List[ColumnSchema]:
        kind = self._project_type(context)
        if kind == "estrai" and any(node.type == "source" and isinstance(getattr(node, "table_name", None), str) and node.table_name for node in context.nodes_by_id.values()):
            return self._infer_sql_result(context)
        input_schemas = self._first_input_schema(context) if kind == "arricchisci" else self._get_input_schemas(
            context.node.id,
            context.nodes_by_id,
            context.edges_by_target,
            context.project_id,
        )
        if input_schemas:
            columns = _node_config(context.node, ("output_columns",)).get("output_columns")
            schema = input_schemas[0]
            if columns is not None and columns != []:
                references = [col.to_dict() for col in schema]
                columns = resolve_output_column_refs(columns, unique_column_names(references))
                plan = select_column_plan([col.name for col in schema], columns, skip_missing=True)
                schema = [replace(col, name=label) for col, label in expand_column_plan(schema, plan, lambda col: col.name)]
            groups, aggregations = result_group_plan(context.node, [col.name for col in schema]) if kind == "manipola" else ([], [])
            if groups or aggregations:
                by_name = {col.name: col for col in schema}
                grouped = {name: by_name[name] for name in groups if name in by_name}
                for col, func, alias in aggregations:
                    grouped[alias] = ColumnSchema(alias, aggregation_output_type(func, by_name[col].type if col in by_name else "str"), source_node=context.node.id, is_calculated=True)
                schema = list(grouped.values())
            if kind == "manipola":
                names, _ = make_unique_column_names([col.name for col in schema])
                schema = [replace(col, name=name) for col, name in zip(schema, names)]
            return schema
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
            columns = []
            for col in contract_columns:
                col_id = col.get("id")
                col_name = col.get("physical_name") or col.get("display_name") or col.get("source_name") or ""

                columns.append(ColumnSchema(
                    name=col_name,
                    type=(col.get("type") or {}).get("logical", "string"),
                    id=col_id,
                    source_name=col.get("source_name") or col_name,
                    source_node=node.id,
                    is_calculated=False
                ))

            return self._project_source_columns(columns, node_columns, deduplicate=True)
            
        except Exception as e:
            logger.error(f"[CRASH] Errore lettura metadata {source_project_id}: {e}", exc_info=True)
            self._status_cache[node.id] = ("indeterminate", str(e))
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
                if not isinstance(col, dict):
                    col = col.model_dump()
                if col.get("name"):
                    columns.append(ColumnSchema(
                        name=col.get("name", ""),
                        type=col.get("type", "str"),
                        id=col.get("id") or col.get("column_id") or _stable_column_id(node.id, col.get("name", "")),
                        source_name=col.get("source_name") or col.get("name", ""),
                        source_node=node.id,
                        is_calculated=False
                    ))
        
        if not columns:
            self._status_cache[node.id] = ("indeterminate", "Schema fisico della sorgente non disponibile")
        elif node_field(node, "file_source_id") or node_field(node, "datasource_id"):
            self._status_cache[node.id] = ("metadata", "Nomi da metadati salvati")
            self._types_status_cache[node.id] = (
                "indeterminate", "Metadati salvati: lo schema fisico del file esterno non è verificato in inferenza",
            )
        return self._project_source_columns(columns, node_columns)

    def _project_source_columns(self, schema: List[ColumnSchema], columns: list[Any], *, deduplicate: bool = False) -> List[ColumnSchema]:
        """Applica lo stesso piano del caricamento, mantenendo identità e ordine."""

        raw = [col if isinstance(col, dict) else col.model_dump() for col in columns]
        plan = source_column_plan([col.name for col in schema], raw)
        projected = ([replace(col, name=alias) for name, alias in plan for col in schema if col.name == name]
                     if plan else list(schema))
        if deduplicate:
            names = unique_source_names([col.name for col in projected])
            projected = [replace(col, name=name) for col, name in zip(projected, names)]
        return projected
    
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


        executor = get_executor()
        if transform_op in executor.registry:

            schema_node = SimpleNamespace(data={"inputColumns": [col.to_dict() for schema in input_schemas for col in schema]})
            config = executor._resolve_column_references(config, schema_node, executor.registry[transform_op])
            config = executor.prepare_params(transform_op, config)
        else:
            self._status_cache[node.id] = ("indeterminate", "Operazione assente dal registry di esecuzione")

        if not transform_op:
            self._status_cache[node.id] = ("indeterminate", "Operazione non specificata")
            logger.warning(f"Transform {node.id} senza transform_op")
            return input_schemas[0] if input_schemas else []

        handlers = self._build_transform_handlers(edges_by_target)
        handler = handlers.get(transform_op)
        if handler is None:
            self._status_cache[node.id] = ("indeterminate", f"Operazione non gestita: {transform_op}")
            logger.warning(f"Operazione {transform_op} non gestita - passthrough")
            return self._transform_handlers.schema_passthrough(input_schemas, node.id)
        schema = handler(input_schemas, config, node.id)
        if transform_op in {"cross_join", "sjoin", "geo_nearest", "geo_overlay", "geo_union",
                            "geo_intersection", "geo_symmetric_difference"}:
            schema = self._merge_schema_identities(schema, input_schemas)
        self._types_status_cache.setdefault(node.id, ("certain", ""))
        if transform_op in {
            "pivot", "flatten_json", "table_diff",
            "formula", "conditional", "fill_na", "replace", "type_cast",
            "date_extract", "geo_nearest", "geo_count_points_in_polygons",
            "string_pad", "string_length", "string_extract",
            "window_function", "geo_distance", "geo_line_builder",
            "geo_polygon_builder", "statistics",
            "geo_union", "geo_symmetric_difference", "geo_to_wkt",
        }:
            self._types_status_cache[node.id] = (
                "indeterminate", "Tipi dipendenti dai valori effettivi dell'ingresso",
            )
        if transform_op == "lookup" and input_schemas:
            # Fase 5, T6 (BA1): certo solo se l'esecuzione da' lo stesso tipo
            # qualunque siano i valori (manipola/lookup_type_plan).
            source = next((col for col in input_schemas[0] if col.name == (config.get("column") or "")), None)
            if source is not None and not lookup_type_plan(source.type, config).certain:
                self._types_status_cache[node.id] = (
                    "indeterminate", "Tipo del Lookup dipendente dai valori effettivi dell'ingresso",
                )
        if transform_op in {"pivot", "transpose", "flatten_json", "table_diff",
                            "geo_count_points_in_polygons"}:
            self._status_cache[node.id] = ("indeterminate", "Nomi o ordine dipendenti dai valori o dal vuoto")
        if transform_op == "pivot" and config.get("mapping"):
            self._status_cache.pop(node.id, None)
            self._order_status_cache[node.id] = "indeterminate"
        if transform_op == "split_column" and not config.get("new_columns"):
            self._status_cache[node.id] = ("indeterminate", "Nomi delle parti generati dai valori")
        if transform_op in {"split_column", "string_pad", "string_length", "uuid_generator", "window_function", "geo_nearest"} and input_schemas:
            before = [col.name for col in input_schemas[0]]
            after = [col.name for col in schema]
            if set(before) != set(after):
                schema = [replace(col, optional=True) if col.name not in before else col for col in schema]
            elif before != after:
                self._order_status_cache[node.id] = "indeterminate"
        if transform_op == "table_diff" and len(input_schemas) == 2:
            left_names = {col.name for col in input_schemas[0]}
            right_names = {col.name for col in input_schemas[1]}
            if left_names == right_names:
                self._status_cache.pop(node.id, None)
        if transform_op == "geo_count_points_in_polygons" and not config.get("aggregate_column"):
            self._status_cache.pop(node.id, None)
            self._types_status_cache[node.id] = ("certain", "")
        if transform_op == "reorder_columns":
            self._order_status_cache[node.id] = "indeterminate"
        if transform_op in {"join", "sjoin", "geo_overlay", "geo_union", "geo_symmetric_difference"} and config.get("how", "inner") != "inner":
            self._types_status_cache[node.id] = ("indeterminate", "Le righe non abbinate possono promuovere i tipi numerici")
        if transform_op == "concat" and input_schemas:
            signatures = {tuple((col.name, type_family(col.type)) for col in schema) for schema in input_schemas}
            if len(signatures) > 1:
                self._types_status_cache[node.id] = ("indeterminate", "Promozione dei tipi dipendente dai valori e dagli ingressi vuoti")
        return schema

    def _resolve_schema_config_column_refs(
        self,
        config: JsonObject,
        input_schemas: List[List[ColumnSchema]],
    ) -> JsonObject:
        """Risolve i ref-colonna by-id → nome corrente, via la regola condivisa.

        Costruisce ``id_to_name`` dagli schemi in input e delega a
        ``config_column_refs.resolve_config_column_refs`` — STESSA logica che usa
        il validatore manipola (no-drift strutturale).
        """
        if not isinstance(config, dict):
            return config
        id_to_name = {
            str(getattr(col, "id", "")): col.name
            for schema in input_schemas
            for col in schema
            if getattr(col, "id", None)
        }
        return resolve_config_column_refs(config, id_to_name)

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
            is_calculated=col.is_calculated,
            optional=col.optional,
        ) for col in base_schema]


        raw_node = node.model_dump(exclude_unset=True) if isinstance(node, Node) else {
            "output_mapping": node_field(node, "output_mapping"), "data": node.data or {},
        }
        node_data = _merge_node_data(raw_node)
        api_id = node_field(node, "api_id")
        api_config = (self.api_configs.get(api_id) if self.api_configs is not None
                      else schema_execution_sources.load_api_schema_config(api_id)) if isinstance(api_id, str) and api_id else None
        if not api_config:
            self._status_cache[node.id] = ("indeterminate", "Configurazione API corrente non disponibile")
            api_config = {}
        generator = api_config.get("api_type") == "generator" or not input_schemas
        mapping = (api_config.get("output_mapping") or []) if generator else resolve_enrichment_output_mapping(node_data, api_config)
        if generator:
            result = []
            self._independent_outputs.add(node.id)
        dynamic = not mapping or api_config.get("connection_type") == "rest_file" or (
            generator and bool(api_config.get("response_config") or api_config.get("response_preset") or node_field(node, "execute_if")))
        if dynamic:
            self._status_cache[node.id] = ("indeterminate", "Colonne determinate dalla risposta API")
            self._order_status_cache[node.id] = "indeterminate"
        elif generator and api_config:
            self._status_cache[node.id] = ("certain", "")
            self._order_status_cache[node.id] = "certain"
        self._types_status_cache[node.id] = ("indeterminate", "Tipi dipendenti dai valori restituiti dall'API")
        if not generator:
            # Il runtime prealloca column-if-present prima di applicare le risposte.
            for item in mapping:
                destination = mapping_destination(item, phase="columns")
                name = "nan" if destination is None else destination
                if not any(col.name == name for col in result):
                    source = mapping_destination(item, phase="source") or name
                    result.append(ColumnSchema(name=name, type=item.get("type") or "str",
                        id=_stable_column_id(node.id, source, "preallocated") if not destination else _stable_column_id(node.id, source),
                        source_name=source, source_node=node.id, is_calculated=True))
        existing = {col.name: index for index, col in enumerate(result)}
        generator_names = generator_mapping_column_names(mapping) if generator else []
        for mapping_index, item in enumerate(mapping):
            destination = mapping_destination(item, phase="columns")
            name = generator_names[mapping_index] if generator else mapping_destination(item)
            if not destination:
                self._status_cache[node.id] = ("indeterminate", "Il runtime include destinazioni API vuote o nulle")
                self._order_status_cache[node.id] = "indeterminate"
            if not name and not generator:
                self._status_cache[node.id] = ("indeterminate", "Destinazione API vuota o non risolvibile")
                continue
            if name in existing:
                # La risposta sovrascrive anche colonne dell'ingresso: il tipo non e piu garantito.
                result[existing[name]] = replace(result[existing[name]], type=item.get("type") or "str")
                continue
            result.append(ColumnSchema(
                name=name, type=item.get("type") or "str",
                id=_stable_column_id(node.id, mapping_destination(item, phase="source") or name),
                source_name=mapping_destination(item, phase="source") or name, source_node=node.id,
                is_calculated=True, optional=dynamic or bool(node_field(node, "execute_if")),
            ))
            existing[name] = len(result) - 1

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
        incoming_edges = sorted(
            edges_by_target.get(node_id, []),
            key=lambda edge: incoming_edge_sort_key_for_nodes(edge, nodes_by_id),
        )
        
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
        self._canvas_edges = list(edges)
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
