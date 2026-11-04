"""Preparation shared by plugin execution and validation; no I/O or row execution."""

import inspect
from app.core.dataframes.schema import empty_dataframe_from_column_metadata as empty_dataframe_from_column_metadata
from app.core.dataframes.values import is_null_scalar as is_null_scalar
from app.shared.contracts.payloads import JsonObject

from typing import Any

from app.core.manipola.transforms.formula import prepare_formula
from app.core.manipola.transforms.filtering import prepare_filter
from app.core.manipola.transforms.joins import prepare_join


ColumnOrigins = dict[str, list[str]]


def prepare_operation(function: Any, operation: str, inputs: list, params: JsonObject, *, multi: bool) -> JsonObject:
    """Bind each registered function and apply its column/expression preparation."""
    argument = inputs if multi else (inputs[0] if inputs else None)
    inspect.signature(function).bind(argument, **params)
    if operation == "formula" and inputs:
        prepare_formula(inputs[0].columns, **params)
    elif operation == "filter" and inputs:
        prepare_filter(inputs[0], **params)
    elif operation == "join" and len(inputs) >= 2:
        keys = {key: value for key, value in params.items() if key in inspect.signature(prepare_join).parameters}
        prepare_join(inputs[0], inputs[1], **keys)
    return params


def rename_collisions(names: list[str], params: JsonObject) -> ColumnOrigins:
    """Report physical destinations without collapsing duplicate occurrences."""
    mapping = {item.get("old_name"): item.get("new_name") for item in params.get("renames") or []
               if item.get("old_name") and item.get("new_name")}
    if not mapping and params.get("old_name") and params.get("new_name"):
        mapping[params["old_name"]] = params["new_name"]
    origins = {}
    for name in names:
        origins.setdefault(mapping.get(name, name), []).append(name)
    return {name: sources for name, sources in origins.items() if len(sources) > 1}
