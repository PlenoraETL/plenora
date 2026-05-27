"""
Canvas column types: canonical names and shape adapters.

Three concepts have adjacent names but distinct semantics:
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional
from app.shared.contracts.payloads import StringMap, StringRows


# Canonical vocabulary + aliases


CANONICAL_BASE_TYPES = frozenset({"int", "real", "str", "bool", "date", "geometry"})
"""Minimal vocabulary of logical types. `geometry` accepts suffixes
(`geometry:Point:4326`) - see `is_canonical_type`."""


TYPE_ALIASES: Dict[str, str] = {
    "integer": "int",
    "float": "real",
    "double": "real",
    "boolean": "bool",
}
"""Input aliases accepted by `normalize_canvas_type` and the dispatch
canvas-type -> pandas-dtype. Maintains compatibility with API / DB payloads
using extended names. Case-insensitive: always applied after `.lower()`."""


GEOMETRY_SUBTYPES = frozenset({
    "point", "line", "polygon",
    "multipoint", "multiline", "multipolygon",
})
"""Supported geometry sub-types after `geometry:` in the canvas-type string."""


# Introspection


def is_canonical_type(t: str) -> bool:
    """True if `t` is a canonical logical type (case-sensitive).

    `geometry` accepts optional sub-type/SRID via `:` (for example
    `geometry:Point:4326`). Aliases (`integer`, `float`, ...) are not
    canonical; call `normalize_canvas_type` first.
    """
    if not isinstance(t, str) or not t:
        return False
    if t in CANONICAL_BASE_TYPES:
        return True
    if t.startswith("geometry:"):
        return True
    return False


def normalize_canvas_type(canvas_type: Optional[str]) -> str:
    """canvas-type string with optional alias/case/suffix -> canonical string.

    Rules:
    - None / empty -> ""
    - `geometry[:<sub>[:<srid>]]` -> "geometry" (preserves concept, drops suffix)
    - accepts TYPE_ALIASES case-insensitively (integer->int, float->real, ...)
    - case-insensitive on base_type
    - unknown types -> "" (caller chooses fallback)
    """
    if not canvas_type or not isinstance(canvas_type, str):
        return ""
    base = canvas_type.split(":")[0].lower()
    if base == "geometry":
        return "geometry"
    if base in CANONICAL_BASE_TYPES:
        return base
    if base in TYPE_ALIASES:
        return TYPE_ALIASES[base]
    return ""


def lookup_canvas_column_type(
    column_name: str,
    canvas_type_map: Mapping[str, str],
    *alternate_names: str,
) -> Optional[str]:
    """Return the canvas type for a column name using exact then case-insensitive lookup.

    `alternate_names` covers boundaries where a DB/driver returns a normalized
    output name while canvas metadata still uses the original cursor name.
    """
    candidate_names = (column_name, *alternate_names)
    for name in candidate_names:
        if name in canvas_type_map:
            return canvas_type_map[name]

    canvas_type_map_lower = {k.lower(): v for k, v in canvas_type_map.items()}
    for name in candidate_names:
        lowered = name.lower()
        if lowered in canvas_type_map_lower:
            return canvas_type_map_lower[lowered]

    return None


# Shape adapters (list <-> dict)


def metadata_to_dict(rows: Iterable[Mapping[str, Any]] | None) -> StringMap:
    """list[{"name","type"}] -> dict{name: type}.

    Silently skips malformed rows (missing `name` or `type`, or non-dict):
    callers are already best-effort. Preserves input order for consumers that
    expect stable order (Python 3.7+ ordered dict).
    """
    out: Dict[str, str] = {}
    if not rows:
        return out
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = row.get("name")
        type_ = row.get("type")
        if isinstance(name, str) and name and isinstance(type_, str) and type_:
            out[name] = type_
    return out


def dict_to_metadata(mapping: Mapping[str, str] | None) -> StringRows:
    """dict{name: type} -> list[{"name","type"}].

    Preserves insertion order. Empty name/type pairs are skipped for symmetry
    with `metadata_to_dict`.
    """
    out: List[Dict[str, str]] = []
    if not mapping:
        return out
    for name, type_ in mapping.items():
        if isinstance(name, str) and name and isinstance(type_, str) and type_:
            out.append({"name": name, "type": type_})
    return out
