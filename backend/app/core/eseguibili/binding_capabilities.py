"""Generic, server-authoritative runtime parameter capabilities.

The frontend may suggest bindings, but publication resolves every target
against this module.  Arbitrary JSON paths are never accepted as writable
runtime configuration.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.shared.contracts.payloads import JsonObject

_SCALAR_PARAMETER_TYPES = frozenset({"string", "number", "boolean", "select"})
_SCHEMA_AFFECTING_PARAMETER_IDS = frozenset(
    {
        "new_column",
        "output_column",
        "output_prefix",
        "prefix",
        "var_name",
        "value_name",
        "wkt_column",
        "lat_column",
        "lon_column",
        "count_column",
        "target_type",
    }
)
_SENSITIVE_PARTS = frozenset(
    {
        "auth",
        "connection_id",
        "api_id",
        "file_source_id",
        "source_project_id",
        "password",
        "secret",
        "token",
        "key_value",
    }
)


@dataclass(frozen=True)
class BindableTarget:
    path: str
    value_type: str
    label: str


@lru_cache(maxsize=1)
def _transform_registry() -> Mapping[str, Any]:
    registry_path = (
        Path(__file__).resolve().parent.parent
        / "manipola"
        / "transforms_registry.json"
    )
    with registry_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    transforms = payload.get("transforms") or {}
    return transforms if isinstance(transforms, Mapping) else {}


def _node_data(node: Mapping[str, Any]) -> JsonObject:
    merged = dict(node)
    data = node.get("data")
    if isinstance(data, Mapping):
        merged.update(data)
    return merged


def _transform_targets(data: Mapping[str, Any]) -> list[BindableTarget]:
    transform_id = str(data.get("transform_op") or "")
    definition = _transform_registry().get(transform_id) or {}
    output = definition.get("output") or {}
    adds_column = str(output.get("adds_column") or "")
    targets: list[BindableTarget] = []

    for parameter in definition.get("parameters") or []:
        if not isinstance(parameter, Mapping):
            continue
        parameter_id = str(parameter.get("id") or "")
        parameter_type = str(parameter.get("type") or "")
        if not parameter_id:
            continue
        explicit = parameter.get("runtime_bindable")
        schema_affecting = (
            parameter_id == adds_column
            or parameter_id in _SCHEMA_AFFECTING_PARAMETER_IDS
        )
        if parameter_type == "condition_list":
            if explicit is False:
                continue
            conditions = (data.get("config") or {}).get(parameter_id) or []
            for index, condition in enumerate(conditions):
                if not isinstance(condition, Mapping):
                    continue
                for field in ("value", "result"):
                    if field in condition:
                        targets.append(
                            BindableTarget(
                                path=f"config.{parameter_id}.{index}.{field}",
                                value_type="string",
                                label=(
                                    f"{parameter.get('label') or parameter_id} "
                                    f"{index + 1}: {field}"
                                ),
                            )
                        )
            continue
        bindable = (
            bool(explicit)
            if explicit is not None
            else parameter_type in _SCALAR_PARAMETER_TYPES and not schema_affecting
        )
        if not bindable:
            continue
        targets.append(
            BindableTarget(
                path=f"config.{parameter_id}",
                value_type=parameter_type,
                label=str(parameter.get("label") or parameter_id),
            )
        )

    return targets


def bindable_targets(node: Mapping[str, Any]) -> list[BindableTarget]:
    """Return writable runtime parameters for one concrete canvas node."""

    data = _node_data(node)
    node_type = str(data.get("type") or node.get("type") or "")
    targets: list[BindableTarget] = []

    if node_type == "transform":
        targets.extend(_transform_targets(data))

    for index, item in enumerate(data.get("filters") or []):
        if isinstance(item, Mapping) and "value" in item:
            targets.append(
                BindableTarget(
                    path=f"filters.{index}.value",
                    value_type="string",
                    label=f"Filtro {index + 1}: valore",
                )
            )
    for index, item in enumerate(data.get("conditions") or []):
        if not isinstance(item, Mapping):
            continue
        for field in ("value", "result"):
            if field in item:
                targets.append(
                    BindableTarget(
                        path=f"conditions.{index}.{field}",
                        value_type="string",
                        label=f"Condizione {index + 1}: {field}",
                    )
                )
    for index, item in enumerate(data.get("having_conditions") or []):
        if isinstance(item, Mapping) and "value" in item:
            targets.append(
                BindableTarget(
                    path=f"having_conditions.{index}.value",
                    value_type="string",
                    label=f"HAVING {index + 1}: valore",
                )
            )

    static_parameters = data.get("static_parameters") or {}
    if isinstance(static_parameters, Mapping):
        for name in static_parameters:
            targets.append(
                BindableTarget(
                    path=f"static_parameters.{name}",
                    value_type="string",
                    label=f"Parametro statico: {name}",
                )
            )

    for field in ("value", "default_value"):
        if field in data:
            targets.append(
                BindableTarget(path=field, value_type="string", label=field)
            )

    # Preserve order while removing aliases/duplicates.
    return list({target.path: target for target in targets}.values())


def canonical_binding_path(node: Mapping[str, Any], requested_path: str) -> str:
    """Validate a client binding and return its canonical config path."""

    path = str(requested_path or "").strip()
    parts = path.split(".")
    if not path or any(not part for part in parts):
        raise ValueError(f"Campo binding non valido: {requested_path!r}")
    if any(part.lower() in _SENSITIVE_PARTS for part in parts):
        raise ValueError(f"Campo binding protetto: {requested_path!r}")

    targets = {target.path: target for target in bindable_targets(node)}
    if path in targets:
        return path

    # Canvas v2 initially stored transform fields without the config prefix.
    # Resolve that representation during publication, then freeze the canonical
    # path in the executable manifest.
    config_alias = f"config.{path}"
    if config_alias in targets:
        return config_alias
    raise ValueError(f"Campo non configurabile da input runtime: {path!r}")


__all__ = ["BindableTarget", "bindable_targets", "canonical_binding_path"]
