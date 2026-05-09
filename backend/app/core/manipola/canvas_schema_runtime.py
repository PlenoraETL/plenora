"""Canvas schema helpers for MANIPOLA runtime output metadata."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from app.shared.execution.canvas_graph import CanvasGraph
from app.core.projects.canvas_models import CanvasState


def extract_srid_from_canvas(
    canvas: CanvasState,
    *,
    canvas_graph: Optional[CanvasGraph] = None,
) -> Optional[int]:
    """Return the first EPSG SRID declared by canvas column metadata."""
    from app.shared.resources.metadata import ResourceMetadata

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


def build_canvas_type_map(
    canvas: CanvasState,
    *,
    canvas_graph: Optional[CanvasGraph] = None,
) -> Dict[str, str]:
    """Build a column-name to type map from canvas columns and calculated columns."""
    type_map: Dict[str, str] = {}
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
    return type_map


def build_columns_with_types(
    df: pd.DataFrame,
    canvas_type_map: Dict[str, str],
    *,
    logger: Any = None,
) -> List[Dict[str, str]]:
    """Build preview column descriptors using canvas metadata before dtype fallback."""
    columns = []
    lower_map = {k.lower(): v for k, v in canvas_type_map.items()}
    for col in df.columns:
        column_type = canvas_type_map.get(col) or lower_map.get(col.lower())

        if column_type is None and (col.endswith("_L") or col.endswith("_R")):
            base = col[:-2]
            column_type = canvas_type_map.get(base) or lower_map.get(base.lower())

        if column_type == "str" and col.lower() in ("geometry", "geom", "the_geom", "shape"):
            column_type = detect_geometry_from_content(df, col, logger=logger)

        if column_type is None:
            dtype = str(df[col].dtype)
            if "int" in dtype:
                column_type = "int"
            elif "float" in dtype:
                column_type = "real"
            elif "bool" in dtype:
                column_type = "bool"
            elif "datetime" in dtype:
                column_type = "date"
            elif "geometry" in dtype.lower():
                column_type = "geometry"
            else:
                column_type = detect_geometry_from_content(df, col, logger=logger)
        columns.append({"name": col, "type": column_type})
    return columns


def detect_geometry_from_content(
    df: pd.DataFrame,
    col_name: str,
    *,
    logger: Any = None,
) -> str:
    """Detect WKT or Shapely geometry values from a DataFrame column sample."""
    try:
        if col_name.lower() in ("geometry", "geom", "the_geom", "shape"):
            sample = df[col_name].dropna().head(1)
            if not sample.empty:
                sample_value = sample.iloc[0]
                if hasattr(sample_value, "geom_type"):
                    return "geometry"
                sample_text = str(sample_value).upper().strip()
                if sample_text and sample_text[0] in "PLMG":
                    return "geometry"

        sample = df[col_name].dropna().head(1)
        if sample.empty:
            return "str"
        sample_value = sample.iloc[0]

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
