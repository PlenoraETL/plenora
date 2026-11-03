"""Riferimenti a colonne nei config dei transform — sorgente unica condivisa.

Le key-set (quali chiavi di config sono ref-colonna) e la risoluzione id→nome
erano duplicate in ``projects/schema_inference`` e ``output_contract/canvas_sync``.
Qui sono UNA copia, importata da entrambi + dal validatore ``manipola`` (#1/#26).

``resolve_config_column_refs`` rispecchia la risoluzione del runtime (id batte
nome, fallback al nome stale se l'id non risolve) → PUT e runtime non possono
divergere (no-drift strutturale, come ``check_mapping`` in 0.4).
"""

from __future__ import annotations

from typing import Any, Mapping, Set

from app.shared.contracts.payloads import JsonObject

# Chiavi scalari il cui valore è un nome-colonna.
SCALAR_COLUMN_CONFIG_KEYS = frozenset({
    "aggregate_column",
    "by_column",
    "column",
    "geom_column",
    "geometry_column",
    "group_by",
    "group_column",
    "id_column",
    "index_col",
    "input_geom",
    "lat_column",
    "left_geom",
    "left_geom_column",
    "left_on",
    "lon_column",
    "mask_geom",
    "old_name",
    "order_column",
    "partition_column",
    "pivot_col",
    "point_geom_column",
    "polygon_geom_column",
    "right_geom",
    "right_geom_column",
    "right_on",
    "source_column",
    "stratify_column",
    "target_column",
    "value_col",
    "wkt_column",
})

# Chiavi-lista i cui elementi sono nomi-colonna.
LIST_COLUMN_CONFIG_KEYS = frozenset({
    "column_order",
    "columns",
    "compare_columns",
    "group_by",
    "group_by_columns",
    "id_columns",
    "join_keys",
    "left_columns",
    "left_keys",
    "right_columns",
    "right_keys",
    "subset",
    "value_columns",
})

# Chiavi-lista di dict con un campo nome-colonna + un campo id-colonna.
_NESTED_REF_KEYS = {
    "aggregations": ("column", "column_id"),
    "aggs": ("column", "column_id"),
    # Filtro multi-condizione (redesign Manipola, B3).
    "conditions": ("column", "column_id"),
    "renames": ("old_name", "old_name_id"),
    "maskings": ("column", "column_id"),
}


def _resolve_dict_ref(
    item: Any, id_to_name: Mapping[str, str], name_field: str, id_field: str,
) -> Any:
    if not isinstance(item, dict):
        return item
    out = dict(item)
    column_id = out.get(id_field)
    if column_id and str(column_id) in id_to_name:
        out[name_field] = id_to_name[str(column_id)]
    return out


def resolve_config_column_refs(
    config: JsonObject, id_to_name: Mapping[str, str],
) -> JsonObject:
    """Risolve i ref-colonna by-id → nome corrente (id batte nome).

    Per le chiavi ``<base>_column_id``/``<base>_column_ids`` riscrive ``<base>``
    col nome risolto dall'id (fallback al valore stale se l'id non risolve — è il
    drift che il validatore poi cattura). Riscrive anche i nested
    aggregations/renames/maskings. Senza id risolvibili ritorna il config invariato.
    """
    if not isinstance(config, dict) or not id_to_name:
        return config

    out = dict(config)
    for key, value in list(out.items()):
        if key.endswith("_column_id"):
            base = key[:-10]
            if base in out and value:
                out[base] = id_to_name.get(str(value), out[base])
        elif key.endswith("_column_ids"):
            base = key[:-11]
            if base in out and isinstance(value, list) and value:
                current_values = out.get(base)
                if not isinstance(current_values, list):
                    current_values = []
                out[base] = [
                    id_to_name.get(
                        str(column_id),
                        current_values[idx] if idx < len(current_values) else None,
                    )
                    for idx, column_id in enumerate(value)
                ]

    for key, value in list(out.items()):
        if not isinstance(value, list):
            continue
        nested = _NESTED_REF_KEYS.get(key)
        if nested:
            name_field, id_field = nested
            out[key] = [
                _resolve_dict_ref(item, id_to_name, name_field, id_field)
                for item in value
            ]

    return out


def referenced_column_names(config: JsonObject) -> Set[str]:
    """Estrae l'insieme dei nomi-colonna referenziati da un config (già risolto).

    Copre le chiavi scalari, le chiavi-lista e i nested aggregations/renames/
    maskings — esattamente ciò che ``resolve_config_column_refs`` tocca.
    """
    if not isinstance(config, dict):
        return set()
    names: Set[str] = set()

    for key in SCALAR_COLUMN_CONFIG_KEYS:
        value = config.get(key)
        if isinstance(value, str) and value:
            names.add(value)

    for key in LIST_COLUMN_CONFIG_KEYS:
        value = config.get(key)
        if isinstance(value, list):
            names.update(v for v in value if isinstance(v, str) and v)

    for key, (name_field, _id) in _NESTED_REF_KEYS.items():
        value = config.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    nm = item.get(name_field)
                    if isinstance(nm, str) and nm:
                        names.add(nm)

    return names


__all__ = [
    "SCALAR_COLUMN_CONFIG_KEYS",
    "LIST_COLUMN_CONFIG_KEYS",
    "resolve_config_column_refs",
    "referenced_column_names",
]
