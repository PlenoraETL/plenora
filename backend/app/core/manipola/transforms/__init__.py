"""
Transforms Package — Funzioni di trasformazione per MANIPOLA.

Nota: il PluginExecutor carica le funzioni direttamente dai sottomoduli
"""

# ── Trasformazioni base ──────────────────────────────────────────────────────
from .base import (
    # Join e unione
    op_join,
    op_union,
    # Filtro e formula
    op_filter,
    op_formula,
    # Colonne
    op_concat_columns,
    op_rename,
    op_drop_columns,
    op_split_column,
    op_type_cast,
    # Aggregazione e ordinamento
    op_aggregate,
    op_sort,
    op_distinct,
    # Pulizia dati
    op_fill_na,
    op_replace,
    op_text_normalize,
    # Sicurezza / hashing
    op_md5_hash,
    op_mask_data,
    # Geometria da coordinate
    op_geo_from_coords,
    # Logica condizionale
    op_conditional,
    # Analisi differenziale
    op_table_diff,
    # Pivot
    op_pivot,
    # Statistiche e testo
    op_statistics,
    op_string_extract,
)

# Alias: op_concat e' sinonimo di op_union (unione verticale di DataFrame
from .base import op_union as op_concat  # noqa: F401

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
