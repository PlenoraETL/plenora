"""Canvas schema helpers for MANIPOLA runtime output metadata."""

from __future__ import annotations

from typing import Any, Optional

from app.shared.execution.canvas_graph import CanvasGraph
from app.core.projects.canvas_models import CanvasState
from app.shared.resources.metadata import ResourceMetadata
from app.core.dataframes.tabular import non_null_head_values
from app.core.manipola.types import ColumnMetadataRows, StringMap


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
