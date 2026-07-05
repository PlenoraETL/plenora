"""
Transforms Package — Funzioni di trasformazione per MANIPOLA.

Nota: il PluginExecutor carica le funzioni direttamente dai sottomoduli
"""

# ── Trasformazioni non-spaziali ──────────────────────────────────────────────
from .aggregation import op_aggregate, op_distinct, op_sort
from .analysis import op_statistics
from .cleansing import op_fill_na, op_replace, op_type_cast
from .columns import op_concat_columns, op_drop_columns, op_rename, op_split_column
from .filtering import op_conditional, op_filter
from .formula import op_formula
from .geo import op_geo_from_coords
from .joins import op_join, op_union
from .reshape import op_pivot, op_table_diff
from .security import op_mask_data, op_md5_hash
# ── Trasformazioni spaziali ──────────────────────────────────────────────────
from .spatial import (
    op_sjoin,
    op_geo_buffer,
    op_geo_centroid,
    op_geo_area,
    op_geo_clip,
    op_geo_dissolve,
    op_geo_intersection,
    op_geo_union,
    op_geo_difference,
    op_geo_simplify,
    op_geo_convex_hull,
    op_geo_reproject,
    op_geo_envelope,
    op_geo_count_points_in_polygons,
    op_geo_clean_topology,
    op_geo_overlay,
)
from .strings import op_string_extract, op_text_normalize

# Alias: op_concat e' sinonimo di op_union (unione verticale di DataFrame).
op_concat = op_union

__all__ = [
    "op_aggregate",
    "op_concat",
    "op_concat_columns",
    "op_conditional",
    "op_distinct",
    "op_drop_columns",
    "op_fill_na",
    "op_filter",
    "op_formula",
    "op_geo_area",
    "op_geo_buffer",
    "op_geo_centroid",
    "op_geo_clean_topology",
    "op_geo_clip",
    "op_geo_convex_hull",
    "op_geo_count_points_in_polygons",
    "op_geo_difference",
    "op_geo_dissolve",
    "op_geo_envelope",
    "op_geo_from_coords",
    "op_geo_intersection",
    "op_geo_overlay",
    "op_geo_reproject",
    "op_geo_simplify",
    "op_geo_union",
    "op_join",
    "op_mask_data",
    "op_md5_hash",
    "op_pivot",
    "op_replace",
    "op_rename",
    "op_sjoin",
    "op_sort",
    "op_split_column",
    "op_statistics",
    "op_string_extract",
    "op_table_diff",
    "op_text_normalize",
    "op_type_cast",
    "op_union",
]
