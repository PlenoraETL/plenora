"""Piano della query di ESTRAI: UNA costruzione per esecuzione e anteprima.

Fase 5, T6 (R3.13, N14): l'anteprima della query nel canvas non ricostruisce
piu' il SQL nel frontend. Esecuzione (``executor._execute_database`` /
``_execute_feature_service``) e anteprima (``POST /projects/{id}/query-preview``, ``projects/query_preview``)
passano da qui:

- connessione database -> ``QueryGenerator.generate_sql`` con le opzioni della
  connessione (``sql_connection_options``): stesso SQL, stessi parametri;
- connessione ArcGIS -> ``feature_service_query``: layer, WHERE, campi,
  geometria e limite della lettura REST.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

from app.core.estrai.types import JsonObject


def estrai_result_node(nodes: List[Any]) -> Optional[Any]:
    """Il nodo Output dell'esecuzione: il primo di tipo ``result``."""
    return next((n for n in nodes if getattr(n, "type", None) == "result"), None)


def _node_columns(node: Any) -> list:
    if getattr(node, "columns", None):
        return node.columns
    data = getattr(node, "data", None)
    if isinstance(data, dict) and "columns" in data:
        return data["columns"] or []
    return []


def _col_attr(col: Any, key: str, default: Any = None) -> Any:
    return col.get(key, default) if isinstance(col, dict) else getattr(col, key, default)


def visible_column_names(node: Any) -> List[str]:
    """Colonne visibili di un nodo feature_source (``visible`` assente = visibile)."""
    names = [_col_attr(col, "name", "") for col in _node_columns(node) if _col_attr(col, "visible", True)]
    return [name for name in names if name]


def is_column_visible(node: Any, column_name: str) -> bool:
    """Visibilita' di una colonna; colonna non elencata = visibile."""
    for col in _node_columns(node):
        if _col_attr(col, "name", "") == column_name:
            return bool(_col_attr(col, "visible", True))
    return True


def feature_service_query(nodes: List[Any], result_node: Any) -> Optional[JsonObject]:
    """Lettura REST del layer (un solo feature_source, come l'esecuzione)."""
    feature_nodes = [n for n in nodes if getattr(n, "type", None) == "feature_source"]
    if not feature_nodes:
        return None
    node = feature_nodes[0]
    data = node.data if isinstance(getattr(node, "data", None), dict) else {}
    visible = [c for c in visible_column_names(node) if c != "geometry"]
    has_geom = bool(getattr(node, "geometry_type", None) or data.get("geometry_type"))
    return {
        "node_id": getattr(node, "id", None),
        "layer_url": getattr(node, "layer_url", None) or data.get("layer_url", ""),
        "where": getattr(node, "where", None) or data.get("where", "") or "1=1",
        "out_fields": ",".join(visible) if visible else "*",
        "return_geometry": has_geom and is_column_visible(node, "geometry"),
        "max_features": result_node.limit if getattr(result_node, "limit", None) else None,
        "geom_column": getattr(result_node, "geomColumn", None) or "geometry",
        "ignored_layers": [getattr(n, "id", None) for n in feature_nodes[1:]],
    }


def sql_query(generator: Any, nodes: List[Any], edges: List[Any], result_node: Any, sql_options: JsonObject) -> JsonObject:
    """SQL e parametri dell'esecuzione (``generate_sql`` con le opzioni della connessione).

    Difesa nell'esecutore (filtro «Utente che esegue» ignorato, 11/11/2026):
    ogni filtro con un valore impostato dal server (``value_literal``) di una
    tabella del canvas eseguito deve essere entrato nella WHERE, con i suoi
    parametri nel SQL. Altrimenti ``ValueError`` e la query non parte. La
    regola completa (ogni binding, prima dell'avvio) e'
    ``projects.identity_effect``."""
    info = generator.generate_sql(nodes, edges, result_node.id, **sql_options)
    missing = unbound_literal_filters(nodes, info)
    if missing:
        raise ValueError(literal_filters_message(missing))
    return info


def _literal_filters(nodes: List[Any]) -> List[tuple[Any, int, Any]]:
    out = []
    for node in nodes:
        if getattr(node, "type", None) not in ("source", "feature_source"):
            continue
        for index, item in enumerate(getattr(node, "filters", None) or []):
            literal = item.get("value_literal") if isinstance(item, dict) else getattr(item, "value_literal", None)
            if literal:
                out.append((node, index, item))
    return out


def _describe_filter(node: Any, item: Any) -> str:
    column = item.get("column") if isinstance(item, dict) else getattr(item, "column", None)
    table = getattr(node, "alias", None) or getattr(node, "table_name", None) or getattr(node, "id", "?")
    return f"{table}.{column or '?'}"


def unbound_literal_filters(nodes: List[Any], query_info: JsonObject) -> List[str]:
    """I filtri ``value_literal`` delle tabelle che la query costruita NON
    lega (``tabella.colonna``): tabella fuori dalla query, filtro saltato,
    parametro assente dal SQL."""
    sql = str(query_info.get("sql") or "")
    bound = {
        (str(entry.get("node_id")), entry.get("filter_index"))
        for entry in query_info.get("bound_filters") or []
        if entry.get("params") and all(re.search(rf":{re.escape(str(name))}\b", sql) for name in entry["params"])
    }
    return [
        _describe_filter(node, item)
        for node, index, item in _literal_filters(nodes)
        if (str(getattr(node, "id", "")), index) not in bound
    ]


def feature_service_unbound_literal_filters(nodes: List[Any]) -> List[str]:
    """Lettura ArcGIS: nessun filtro di tabella SQL entra nella lettura del
    layer, quindi ogni filtro ``value_literal`` resterebbe senza effetto."""
    return [_describe_filter(node, item) for node, _index, item in _literal_filters(nodes)]


def literal_filters_message(missing: List[str]) -> str:
    return (
        "Il filtro «Utente che esegue» collegato a " + ", ".join(missing)
        + " non entra nella query: il filtro per utente verrebbe ignorato e l'esecuzione si blocca."
    )


__all__ = [
    "estrai_result_node",
    "feature_service_query",
    "feature_service_unbound_literal_filters",
    "literal_filters_message",
    "unbound_literal_filters",
    "is_column_visible",
    "sql_query",
    "visible_column_names",
]
