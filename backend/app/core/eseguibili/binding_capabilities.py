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
from app.shared.execution.parameter_dependencies import dependency_satisfied

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


_VALUELESS_OPERATORS = frozenset({"IS NULL", "IS NOT NULL"})


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

    parameters = [p for p in definition.get("parameters") or [] if isinstance(p, Mapping)]
    config = data.get("config") if isinstance(data.get("config"), Mapping) else {}
    for parameter in parameters:
        parameter_id = str(parameter.get("id") or "")
        parameter_type = str(parameter.get("type") or "")
        if not parameter_id:
            continue
        # L'esecuzione non legge un parametro con la dipendenza non
        # soddisfatta: non si offre (regola unica: parameter_dependencies).
        if not dependency_satisfied(parameter, parameters, config):
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


def _filter_targets(data: Mapping[str, Any]) -> list[BindableTarget]:
    """Tabella di Estrai: valore dei filtri WHERE (``query_filters``). Gli
    operatori senza valore (``IS NULL``/``IS NOT NULL``) non lo leggono."""
    return [
        BindableTarget(path=f"filters.{index}.value", value_type="string",
                       label=f"Filtro {index + 1}: valore")
        for index, item in enumerate(data.get("filters") or [])
        if isinstance(item, Mapping) and "value" in item
        and str(item.get("operator") or "").upper() not in _VALUELESS_OPERATORS
    ]


def _having_targets(data: Mapping[str, Any]) -> list[BindableTarget]:
    """Risultato di Estrai: valori HAVING, letti solo con HAVING e GROUP BY
    attivi (``query_projection.generate_having``)."""
    if not (data.get("having_enabled") and data.get("group_by_enabled")):
        return []
    return [
        BindableTarget(path=f"having_conditions.{index}.value", value_type="string",
                       label=f"HAVING {index + 1}: valore")
        for index, item in enumerate(data.get("having_conditions") or [])
        if isinstance(item, Mapping) and "value" in item
    ]


def _static_parameter_targets(data: Mapping[str, Any]) -> list[BindableTarget]:
    """Nodo API: parametri statici. La risoluzione li scrive nel campo che
    l'esecuzione legge (``shared.execution.runtime_bound_fields``)."""
    static_parameters = data.get("static_parameters") or {}
    if not isinstance(static_parameters, Mapping):
        return []
    return [
        BindableTarget(path=f"static_parameters.{name}", value_type="string",
                       label=f"Parametro statico: {name}")
        for name in static_parameters
        if isinstance(name, str) and name and "." not in name
    ]


# REGOLA (Fase 5, input «Utente che esegue», correzione dopo la conferma):
# ogni bersaglio offerto e' LETTO dall'esecuzione del suo tipo di nodo. Un
# tipo di nodo offre solo le famiglie della sua riga; una famiglia senza un
# esecutore che la legge non si offre (prima: ``conditions`` al primo livello,
# ``value``/``default_value`` e ``filters`` su qualunque nodo, che nessun
# esecutore leggeva). Test di enumerazione:
# tests2/eseguibili/unit/test_runtime_binding_targets_reach_execution.py.
NODE_TARGET_FAMILIES: Mapping[str, tuple[Any, ...]] = {
    "transform": (_transform_targets,),
    "source": (_filter_targets,),
    "result": (_having_targets,),
    "api_enrich": (_static_parameter_targets,),
}


def bindable_targets(node: Mapping[str, Any]) -> list[BindableTarget]:
    """Return writable runtime parameters for one concrete canvas node."""

    data = _node_data(node)
    node_type = str(data.get("type") or node.get("type") or "")
    targets: list[BindableTarget] = []
    for family in NODE_TARGET_FAMILIES.get(node_type, ()):
        targets.extend(family(data))
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


__all__ = ["NODE_TARGET_FAMILIES", "BindableTarget", "bindable_targets", "canonical_binding_path"]
