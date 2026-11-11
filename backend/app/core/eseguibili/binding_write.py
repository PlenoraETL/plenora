"""Scrittura di un valore nel campo collegato di un nodo (binding ``node_field``).

L'UNICA scrittura dei binding scalari: la usa la risoluzione degli input
(``runtime.resolve_runtime_inputs``), l'anteprima della query (segnaposto,
``projects.query_preview``) e la verifica dell'effetto degli input «Utente
che esegue» (``identity_effect``), cosi' le tre scrivono nello stesso punto
del nodo, con lo stesso segno ``value_literal``.
"""

from __future__ import annotations

from typing import Any

from app.shared.execution.runtime_bound_fields import (
    RUNTIME_BOUND_FIELDS,
    execution_path,
    literal_marker_path,
)


def _set_existing_path(
    container: dict[str, Any],
    path: str,
    value: Any,
    *,
    allow_missing_leaf: bool = False,
) -> None:
    parts = path.split(".")
    current: Any = container
    for part in parts[:-1]:
        if isinstance(current, list):
            index = int(part)
            if index >= len(current):
                raise ValueError(f"Indice binding fuori range: {path}")
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                raise ValueError(f"Campo binding inesistente: {path}")
            current = current[part]
        else:
            raise ValueError(f"Campo binding non attraversabile: {path}")
    leaf = parts[-1]
    if isinstance(current, list):
        index = int(leaf)
        if index >= len(current):
            raise ValueError(f"Indice binding fuori range: {path}")
        current[index] = value
    elif isinstance(current, dict):
        if leaf not in current and not allow_missing_leaf:
            raise ValueError(f"Campo binding inesistente: {path}")
        current[leaf] = value
    else:
        raise ValueError(f"Campo binding non scrivibile: {path}")


# Contenitori che la risoluzione crea se mancano: ``config`` dei passi e i
# campi scritti solo dalla risoluzione (runtime_bound_fields).
_CREATABLE_HEADS = frozenset({"config"}) | RUNTIME_BOUND_FIELDS


def _apply_scalar_binding(node: dict[str, Any], path: str, value: Any, *, literal: bool = False) -> None:
    """``literal``: valore impostato dal server («Utente che esegue»). Dove il
    lettore del bersaglio interpreterebbe il testo (funzioni SQL, liste con la
    virgola, ``BETWEEN`` nei filtri di Estrai) si scrive anche il segno
    ``literal_marker_path``: il valore entra sempre come dato (parametro SQL)."""
    marker = literal_marker_path(path) if literal else None
    if marker:
        _apply_scalar_binding(node, marker, True)
    # Il bersaglio offerto (binding_capabilities) puo' essere letto
    # dall'esecuzione in un campo dedicato: si scrive li'.
    path = execution_path(path)
    data = node.get("data")
    applied = False
    if isinstance(data, dict) and path.split(".", 1)[0] in data:
        _set_existing_path(data, path, value, allow_missing_leaf=True)
        applied = True
    if path.split(".", 1)[0] in node:
        _set_existing_path(node, path, value, allow_missing_leaf=True)
        applied = True
    head = path.split(".", 1)[0]
    if not applied and head in _CREATABLE_HEADS:
        container = data if isinstance(data, dict) else node
        container[head] = {}
        _set_existing_path(container, path, value, allow_missing_leaf=True)
        applied = True
    if not applied:
        raise ValueError(f"Nodo {node.get('id')} senza campo binding {path}")


__all__ = ["_apply_scalar_binding"]
