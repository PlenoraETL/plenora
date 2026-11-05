"""Anteprima della query di ESTRAI sul canvas corrente (Fase 5, T6, R3.13, N14).

La query mostrata nel canvas e' quella dell'esecuzione: stesso piano
(``query_plan``), stesso ``QueryGenerator``, stesse opzioni della connessione
del progetto. Il frontend non ricostruisce piu' niente: mostra quello che
arriva da qui (ORDER BY, HAVING, layer ArcGIS, tabelle non collegate, filtro
0, IN/BETWEEN, colonne calcolate con alias e tutte le regole future vengono
dallo stesso codice).

Input runtime: un filtro collegato a un input prende il valore al momento
dell'esecuzione. Qui il campo collegato riceve un segnaposto, applicato con
la STESSA scrittura dell'esecuzione (``eseguibili.runtime._apply_scalar_binding``);
il parametro che lo contiene si restituisce come "valore dall'input X".
"""

from __future__ import annotations

import copy
import datetime as _dt
from typing import Any, Callable, Mapping, NamedTuple

from app.core.eseguibili.manifest import compile_manifest
from app.core.eseguibili.runtime import _apply_scalar_binding
from app.core.estrai import query_plan
from app.core.estrai.query_generator import QueryGenerator
from app.core.projects.canvas_models import CanvasState
from app.core.projects.types import JsonObject, StringMap

_SENTINEL = "⁣plenora-input:"


def _sentinel(key: str) -> str:
    return f"{_SENTINEL}{key}⁣"


def _input_of(value: Any) -> str | None:
    """Chiave dell'input il cui segnaposto e' nel valore (anche dentro %...%)."""
    if isinstance(value, str) and _SENTINEL in value:
        return value.split(_SENTINEL, 1)[1].split("⁣", 1)[0]
    if isinstance(value, (list, tuple)):
        found = [_input_of(item) for item in value]
        return next((key for key in found if key), None)
    return None


def _shown_value(value: Any, labels: StringMap) -> Any:
    """Valore del parametro con il segnaposto sostituito da «input»."""
    if isinstance(value, str) and _SENTINEL in value:
        head, rest = value.split(_SENTINEL, 1)
        key, tail = (rest.split("⁣", 1) + [""])[:2]
        return f"{head}«{labels.get(key, key)}»{_shown_value(tail, labels)}"
    if isinstance(value, (list, tuple)):
        return [_shown_value(item, labels) for item in value]
    return _jsonable(value)


def _between_sentinels(value: Any) -> None:
    """Un filtro BETWEEN collegato riceve "x AND y" (forma dell'input)."""
    if isinstance(value, dict):
        bound = value.get("value")
        if str(value.get("operator", "")).upper() == "BETWEEN" and isinstance(bound, str) \
                and bound.startswith(_SENTINEL) and " AND " not in bound:
            value["value"] = f"{bound} AND {bound}"
        for item in value.values():
            _between_sentinels(item)
    elif isinstance(value, list):
        for item in value:
            _between_sentinels(item)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    return str(value)


class _BoundCanvas(NamedTuple):
    canvas: JsonObject
    labels: StringMap
    notes: list[str]


def _bind_runtime_inputs(canvas_dict: JsonObject, *, project_id: str, project_type: str,
                         default_connection_id: str | None) -> _BoundCanvas:
    """Segnaposto nei campi collegati agli input runtime (scalari)."""
    notes: list[str] = []
    labels: StringMap = {}
    nodes = canvas_dict.get("nodes") or []
    if not any(isinstance(n, Mapping) and n.get("type") == "project_input" for n in nodes):
        return _BoundCanvas(canvas_dict, labels, notes)
    try:
        manifest = compile_manifest(for_project=True, source_project_id=project_id, project_type=project_type,
                                    canvas=canvas_dict, default_connection_id=default_connection_id)
    except ValueError as exc:
        notes.append(f"Input runtime non validi: {exc}")
        return _BoundCanvas(canvas_dict, labels, notes)
    by_id = {str(n.get("id")): n for n in nodes if isinstance(n, dict)}
    for definition in manifest.inputs:
        labels[definition.key] = definition.label
        if definition.kind == "connection":
            notes.append(f"La connessione puo' cambiare all'avvio (input «{definition.label}»): "
                         "la query mostrata usa quella del progetto.")
    for binding in manifest.bindings:
        if binding.mode != "node_field" or not binding.target_field:
            continue
        node = by_id.get(binding.target_node_id)
        if node is None:
            continue
        try:
            _apply_scalar_binding(node, binding.target_field, _sentinel(binding.input_key))
        except (ValueError, TypeError, IndexError):
            continue
        _between_sentinels(node)
    return _BoundCanvas(canvas_dict, labels, notes)


def describe_estrai_query(
    canvas: CanvasState,
    *,
    project_id: str,
    project_type: str,
    default_connection_id: str | None,
    load_sql_options: Callable[[str], JsonObject],
) -> JsonObject:
    """La query che l'esecuzione lancerebbe sul canvas dato."""
    canvas_dict = copy.deepcopy(canvas.model_dump())
    canvas_dict, labels, notes = _bind_runtime_inputs(
        canvas_dict, project_id=project_id, project_type=project_type,
        default_connection_id=default_connection_id,
    )
    bound = CanvasState(**canvas_dict)
    result_node = query_plan.estrai_result_node(bound.nodes)
    base: JsonObject = {"kind": "none", "sql": None, "params": [], "tables_used": [],
                        "feature_service": None, "error": None, "notes": notes}
    if result_node is None:
        return {**base, "error": "Aggiungi un Output per vedere la query"}
    if not default_connection_id:
        return {**base, "error": "Il progetto non ha una connessione associata"}
    try:
        sql_options = load_sql_options(default_connection_id)
    except ValueError:
        sql_options = None
    if sql_options is None:
        plan = query_plan.feature_service_query(bound.nodes, result_node)
        if plan is None:
            return {**base, "kind": "feature_service", "error": "Nessun layer ArcGIS nel canvas"}
        if plan["ignored_layers"]:
            notes.append("L'esecuzione legge solo il primo layer: gli altri non entrano nella lettura.")
        where_input = _input_of(plan["where"])
        return {**base, "kind": "feature_service", "feature_service": {
            **{k: v for k, v in plan.items() if k != "ignored_layers"},
            "where": _shown_value(plan["where"], labels),
            "where_input": where_input, "where_input_label": labels.get(where_input) if where_input else None,
        }}
    try:
        info = query_plan.sql_query(QueryGenerator(), bound.nodes, bound.edges, result_node, sql_options)
    except (ValueError, TypeError, KeyError) as exc:
        return {**base, "kind": "sql", "error": str(exc)}
    params = []
    for name, value in (info.get("params") or {}).items():
        key = _input_of(value)
        params.append({"name": name, "value": _shown_value(value, labels),
                       "input_key": key, "input_label": labels.get(key) if key else None})
    return {**base, "kind": "sql", "sql": info.get("sql"), "params": params,
            "tables_used": list(info.get("tables_used") or [])}


__all__ = ["describe_estrai_query"]
