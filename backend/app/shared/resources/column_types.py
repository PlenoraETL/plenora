"""Compatibility facade for the canonical column type helpers.

`app.core.resources.column_types` owns the implementation. Keep this module
thin so legacy imports continue to work while new code imports from core.
"""

from app.core.resources.column_types import (
    CANONICAL_BASE_TYPES,
    GEOMETRY_SUBTYPES,
    TYPE_ALIASES,
    canvas_type_to_pandas_dtype,
    dict_to_metadata,
    is_canonical_type,
    lookup_canvas_column_type,
    metadata_to_dict,
    normalize_canvas_type,
    pandas_series_to_canvas_type,
)

__all__ = [
    "CANONICAL_BASE_TYPES",
    "GEOMETRY_SUBTYPES",
    "TYPE_ALIASES",
    "canvas_type_to_pandas_dtype",
    "dict_to_metadata",
    "is_canonical_type",
    "lookup_canvas_column_type",
    "metadata_to_dict",
    "normalize_canvas_type",
    "pandas_series_to_canvas_type",
]
