"""Result grouping plan: keys first, then alias or column_normalized-function."""
from typing import Any
from app.shared.execution.node_fields import node_field


def result_group_plan(node: Any, names: list[str]) -> tuple[list[str], list[tuple[str, str, str]]]:
    if not node_field(node, "group_by_enabled", False):
        return [], []
    groups = node_field(node, "group_by_columns", []) or []
    aggregations = []
    for raw in node_field(node, "aggregations", []) or []:
        agg = raw.model_dump() if hasattr(raw, "model_dump") else raw
        col = agg.get("column")
        func = str(agg.get("function", "count")).lower()
        func = {"avg": "mean"}.get(func, func)
        aggregations.append((col, func, agg.get("alias") or f"{col}_{func}"))
    if not groups and not aggregations:
        return [], []
    missing = [name for name in groups if name not in names]
    groups = [name for name in groups if name in names]
    if not groups:
        raise ValueError(f"GROUP BY: nessuna delle colonne configurate esiste nel DataFrame ({missing!r}).")
    aggregations = [(col, func, alias) for col, func, alias in aggregations
                    if func == "count" or (col in names and func in {"sum", "mean", "min", "max", "std"})]
    if not aggregations:
        raise ValueError("GROUP BY: nessuna aggregazione valida; verifica colonne e funzioni nel nodo Output.")
    return groups, aggregations
