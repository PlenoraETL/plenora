"""Canvas schema helpers for MANIPOLA runtime output metadata."""

from __future__ import annotations

from typing import Any, Optional

from app.shared.execution.canvas_graph import CanvasGraph
from app.core.projects.canvas_models import CanvasState, Edge
from app.shared.resources.metadata import ResourceMetadata
from app.core.dataframes.tabular import non_null_head_values
from app.core.manipola.types import ColumnMetadataRows, StringMap
from app.core.projects.schema_transform_handlers import TransformSchemaHandlers
from app.core.manipola.transform_ordering import incoming_edge_sort_key_for_nodes
from app.shared.execution.graph import kahn_topological_order
from app.shared.execution.schema_types import ColumnSchema


def extract_srid_from_canvas(
    canvas: CanvasState,
    *,
    canvas_graph: Optional[CanvasGraph] = None,
) -> Optional[int]:
    """Return the first EPSG SRID declared by canvas column metadata."""
    graph = canvas_graph or CanvasGraph.from_canvas(canvas)
    for cn in graph.nodes:
        raw = cn.get_field("columns")
        if not raw:
            continue
        cols_dicts = [
            {
                "name": c.get("name") if isinstance(c, dict) else getattr(c, "name", None),
                "type": c.get("type", "") if isinstance(c, dict) else getattr(c, "type", ""),
            }
            for c in raw
        ]
        meta = ResourceMetadata.from_columns_list(cols_dicts, source_kind="file")
        if meta.crs_epsg:
            return meta.crs_epsg
    return None


def build_columns_with_types(
    df: Any,
    canvas_type_map: StringMap,
    *,
    logger: Any = None,
) -> ColumnMetadataRows:
    """I dtype effettivi prevalgono sui metadati precedenti alle trasformazioni."""
    columns = []
    for index, col in enumerate(df.columns):
        dtype = str(df.iloc[:, index].dtype).lower()
        if "int" in dtype:
            column_type = "int"
        elif "float" in dtype:
            column_type = "real"
        elif "bool" in dtype:
            column_type = "bool"
        elif "datetime" in dtype:
            column_type = "date"
        elif "geometry" in dtype:
            column_type = "geometry"
        elif dtype in {"str", "string"} or dtype.startswith("string["):
            column_type = detect_geometry_from_content(df, col, logger=logger)
        else:
            column_type = detect_geometry_from_content(df, col, logger=logger)
        columns.append({"name": col, "type": column_type})
    return columns


def detect_geometry_from_content(
    df: Any,
    col_name: str,
    *,
    logger: Any = None,
) -> str:
    """Detect WKT or Shapely geometry values from a DataFrame column sample."""
    try:
        if col_name.lower() in ("geometry", "geom", "the_geom", "shape"):
            sample = non_null_head_values(df[col_name], 1)
            if sample:
                sample_value = sample[0]
                if hasattr(sample_value, "geom_type"):
                    return "geometry"
                sample_text = str(sample_value).upper().strip()
                if sample_text and sample_text[0] in "PLMG":
                    return "geometry"

        sample = non_null_head_values(df[col_name], 1)
        if not sample:
            return "str"
        sample_value = sample[0]

        if hasattr(sample_value, "geom_type"):
            return "geometry"

        sample_text = str(sample_value).upper().strip()
        prefixes = (
            "POINT",
            "LINESTRING",
            "POLYGON",
            "MULTIPOINT",
            "MULTILINESTRING",
            "MULTIPOLYGON",
            "GEOMETRYCOLLECTION",
        )
        return "geometry" if any(sample_text.startswith(p) for p in prefixes) else "str"
    except Exception as geo_probe_err:
        if logger is not None:
            logger.debug(
                f"Rilevamento geometria da contenuto fallito per "
                f"colonna '{col_name}', fallback a 'str': {geo_probe_err}"
            )
        return "str"


# Tipi dichiarati per il SALVATAGGIO dell'output (conversione delle celle,
# es. XLSX). Ripristinata identica a prima della A1 del Blocco A: la A1 l'aveva
# tolta insieme all'uso nell'anteprima, ma il salvataggio la usa ancora
# (regressione trovata nel collaudo dal vivo: ogni esecuzione Manipola falliva
# al salvataggio). Regola del Blocco A: l'esecuzione resta identica.

def build_canvas_type_map(
    canvas: CanvasState,
    *,
    canvas_graph: Optional[CanvasGraph] = None,
) -> StringMap:
    """Build a column-name to type map from canvas columns and calculated columns."""
    type_map: StringMap = {}
    graph = canvas_graph or CanvasGraph.from_canvas(canvas)
    for node in graph.nodes:
        columns = node.get_field("columns")
        if columns:
            for col in columns:
                if isinstance(col, dict):
                    column_name = col.get("alias") or col.get("name")
                    column_type, original_name = col.get("type", "str"), col.get("name")
                else:
                    column_name = col.alias if hasattr(col, "alias") and col.alias else col.name
                    column_type = col.type if hasattr(col, "type") and col.type else "str"
                    original_name = col.name if hasattr(col, "name") else None
                if column_name:
                    type_map[column_name] = type_map[f"{column_name}_L"] = type_map[f"{column_name}_R"] = column_type
                if original_name and original_name != column_name:
                    type_map[original_name] = type_map[f"{original_name}_L"] = type_map[f"{original_name}_R"] = column_type

        calculated = node.get_field("calculated_columns")
        if calculated:
            for calc in calculated:
                column_name = calc.get("name") if isinstance(calc, dict) else getattr(calc, "name", None)
                column_type = (
                    calc.get("output_type", "str")
                    if isinstance(calc, dict)
                    else getattr(calc, "output_type", "str")
                )
                if column_name:
                    type_map[column_name] = type_map[f"{column_name}_L"] = type_map[f"{column_name}_R"] = column_type
    # Solo i nuovi contratti richiedono la propagazione per ramo; il percorso
    # dei canvas legacy conserva la precedenza storica dei metadati.
    if any(
        node.get_field("transform_op") == "select_columns"
        or any(key in (node.get_field("config") or {}) for key in ("items", "left_columns", "right_columns", "name_conflict"))
        for node in graph.nodes
    ):
        type_map.update(_new_transform_types(graph))
    return type_map


def _new_transform_types(graph: CanvasGraph) -> StringMap:
    """Propaga nomi e tipi separatamente per ciascun ramo del canvas."""
    nodes = {node.id: node for node in graph.nodes}
    raw_nodes = {node_id: graph.raw(node_id) for node_id in nodes}
    handlers = TransformSchemaHandlers(ColumnSchema).build_transform_handlers({})
    schemas = {}
    result: StringMap = {}
    order = kahn_topological_order(list(nodes), [(e["source"], e["target"]) for e in graph.edges], on_cycle="raise")
    for node_id in order:
        node = nodes[node_id]
        incoming = [edge for edge in graph.edges if edge["target"] == node_id]
        # Stesso ordinamento dei lati adottato dal runtime dei transform.
        incoming.sort(key=lambda edge: incoming_edge_sort_key_for_nodes(
            Edge.model_validate(edge), raw_nodes
        ))
        inputs = [schemas.get(edge["source"], []) for edge in incoming]
        config = node.get_field("config") or {}
        op = node.get_field("transform_op")
        if op in handlers and inputs and any(inputs):
            try:
                schema = handlers[op](inputs, config, node_id)
            except (ValueError, KeyError):
                # I metadati salvati possono essere parziali rispetto ai dati
                # caricati. Il runtime ha gia' validato le colonne: usa i dtype.
                schema = []
        elif inputs:
            schema = inputs[0]
        else:
            schema = [ColumnSchema(name=col.get("alias") or col["name"], type=col.get("type") or "str")
                      for col in (node.get_field("columns") or node.get_field("fields") or [])
                      if isinstance(col, dict) and col.get("name")]
        if op == "formula":
            # Il tipo convenzionale dell'inferenza (real) non descrive formule
            # stringa/bool. Senza tipo esplicito lascia decidere al dtype reale.
            items = config.get("items")
            formulas = items if items is not None else [config]
            output_types = {item.get("new_column", "calculated"): item.get("output_type") or "" for item in formulas}
            for col in schema:
                if col.name in output_types:
                    col.type = output_types[col.name]
        schemas[node_id] = schema
        result.update({col.name: col.type for col in schema})
    return result
