"""``depends_on`` dei parametri dei passi (registry delle trasformazioni).

Regola unica per l'esecuzione (``manipola.plugin_executor._prepara_kwargs``:
un parametro con la dipendenza non soddisfatta NON arriva alla funzione) e
per i bersagli collegabili (``eseguibili.binding_capabilities``: un
parametro che l'esecuzione non legge non si offre). Stessa regola del
frontend (``DynamicConfig``: il campo e' nascosto).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def dependency_satisfied(parameter: Mapping[str, Any], parameters: Iterable[Mapping[str, Any]],
                         config: Mapping[str, Any]) -> bool:
    dependency = parameter.get("depends_on")
    if not dependency:
        return True
    dep_id = dependency["param"]
    expected = dependency["value"]
    if dep_id in config:
        value = config[dep_id]
    else:
        definition = next((p for p in parameters if p.get("id") == dep_id), None)
        value = definition.get("default") if definition else None
    if isinstance(expected, list):
        return value in expected
    return value == expected


__all__ = ["dependency_satisfied"]
