"""
Tipi colonna canvas — nome canonico, adapter di shape e dispatch table.

Tre concetti, oggi con nomi vicini ma semantica distinta:
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional


# Vocabolario canonico + alias


CANONICAL_BASE_TYPES = frozenset({"int", "real", "str", "bool", "date", "geometry"})
"""Vocabolario minimo dei tipi logici. `geometry` ammette suffissi
(`geometry:Point:4326`) — vedi `is_canonical_type`."""


TYPE_ALIASES: Dict[str, str] = {
    "integer": "int",
    "float": "real",
    "double": "real",
    "boolean": "bool",
}
"""Alias di ingresso accettati da `normalize_canvas_type` e dalla dispatch
canvas-type -> pandas-dtype. Mantiene compat con payload API / DB che usano
la forma estesa. Case-insensitive: si applica sempre dopo `.lower()`."""


GEOMETRY_SUBTYPES = frozenset({
    "point", "line", "polygon",
    "multipoint", "multiline", "multipolygon",
})
"""Sub-type di geometry supportati (dopo `geometry:` nel canvas-type string)."""


# Introspezione


def is_canonical_type(t: str) -> bool:
    """True se `t` e' un tipo logico canonico (case-sensitive).

    `geometry` accetta sub-type/SRID opzionali via `:` (es. `geometry:Point:4326`).
    Gli alias (`integer`, `float`, ...) NON sono canonici: servono normalize.
    """
    if not isinstance(t, str) or not t:
        return False
    if t in CANONICAL_BASE_TYPES:
        return True
    if t.startswith("geometry:"):
        return True
    return False


def normalize_canvas_type(canvas_type: Optional[str]) -> str:
    """canvas-type string (possibilmente con alias/case/suffissi) -> canonical str.

    Regole:
    - None / empty -> ""
    - `geometry[:<sub>[:<srid>]]` -> "geometry" (preserva il concetto, perde suffisso)
    - Accetta alias TYPE_ALIASES case-insensitively (integer->int, float->real, ...)
    - Case-insensitive sul base_type
    - Tipi sconosciuti -> "" (fallback vuoto, il chiamante decide il fallback)
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


def canvas_type_to_pandas_dtype(canvas_type: Optional[str]) -> str:
    """canvas-type string -> pandas dtype string (per `pd.Series(dtype=...)`).

    Usato da CARICA/auto-mapping per ricostruire un DataFrame vuoto con i
    tipi dichiarati dal frontend. Gestisce tutti gli alias di
    `normalize_canvas_type` + geometry (oggetto opaco, no dtype nativo).
    Fallback: `"object"` (safe per qualsiasi contenuto).
    """
    canonical = normalize_canvas_type(canvas_type)
    if canonical == "int":
        return "int64"
    if canonical == "real":
        return "float64"
    if canonical == "bool":
        return "bool"
    if canonical == "date":
        return "datetime64[ns]"
    # "geometry" / "str" / "" / unknown -> object (opaque
    return "object"


def pandas_series_to_canvas_type(series: Any) -> str:
    """pandas Series -> canonical canvas-type.

    Dispatch basato su `series.dtype` + euristica N11 per recuperare int
    promossi a float per NaN (cfr. `tests2/security/.../test_n11_type_inference_loss_contract.py`).

    Policy N11 (fissata 2026-04-17):
    - float con TUTTI i non-null integer -> "int"
    - float all-NaN -> fallback "real" (conservativo)
    - float con almeno un frazionario -> "real"
    - object numerici parsable -> "real"
    - object altrimenti -> "str"
    """
    dtype_str = str(series.dtype).lower()

    if "int" in dtype_str:
        return "int"
    if "float" in dtype_str:
        non_null = series.dropna()
        if len(non_null) > 0:
            try:
                if (non_null % 1 == 0).all():
                    return "int"
            except (TypeError, ValueError):
                pass
        return "real"
    if "bool" in dtype_str:
        return "bool"
    if "datetime" in dtype_str:
        return "date"
    if dtype_str == "object":
        sample = series.dropna().head(20)
        if len(sample) == 0:
            return "str"
        all_numeric = True
        for value in sample:
            try:
                float(value)
            except (TypeError, ValueError):
                all_numeric = False
                break
        if all_numeric:
            return "real"
        return "str"
    return "str"


# Adapter di shape (list <-> dict


def metadata_to_dict(rows: Iterable[Mapping[str, Any]] | None) -> Dict[str, str]:
    """list[{"name","type"}]  ->  dict{name: type}.

    Ignora silenziosamente righe malformate (senza `name` o `type`, o non dict):
    il chiamante sta gia' facendo best-effort (es. columns_metadata pescato da
    canvas potenzialmente legacy). Preserva l'ordine di input per consumatori
    che si aspettano ordine stabile (Python 3.7+ dict ordinato).
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


def dict_to_metadata(mapping: Mapping[str, str] | None) -> List[Dict[str, str]]:
    """dict{name: type}  ->  list[{"name","type"}].

    Ordine preservato (insertion order del dict). Coppie con name/type vuoti
    vengono skippate per simmetria con `metadata_to_dict`.
    """
    out: List[Dict[str, str]] = []
    if not mapping:
        return out
    for name, type_ in mapping.items():
        if isinstance(name, str) and name and isinstance(type_, str) and type_:
            out.append({"name": name, "type": type_})
    return out
