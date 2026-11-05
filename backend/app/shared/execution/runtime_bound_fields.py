"""Campi dei nodi scritti SOLO dalla risoluzione degli input runtime.

REGOLA (Fase 5, input «Utente che esegue», correzione dopo la conferma)
-----------------------------------------------------------------------
Ogni bersaglio che ``eseguibili.binding_capabilities`` offre e' LETTO
dall'esecuzione. Per quasi tutti i bersagli il campo collegato e' lo stesso
che l'esecutore legge (``filters.N.value``, ``config.<param>``, ...). Per i
parametri statici del nodo API no: ``static_parameters`` nel nodo e' una
copia di validazione della connessione (Fase 5, T6) e l'esecuzione legge i
parametri statici dalla connessione attuale. Usarla in esecuzione farebbe
cambiare i progetti esistenti (copie vecchie salvate nei canvas).

Quindi il bersaglio ``static_parameters.<nome>`` si scrive nel campo
dedicato ``runtime_static_parameters.<nome>`` (:data:`EXECUTION_FIELDS`):

- lo scrive solo la risoluzione dei binding (``eseguibili.runtime``), che
  prima lo azzera in ogni nodo (:func:`strip_runtime_bound_fields`); anche la
  preparazione di ogni canvas inviato dal client e il salvataggio lo tolgono;
- l'esecuzione (per riga, generatore, stima) applica questi valori SOPRA i
  parametri statici della connessione, solo per quelle chiavi
  (:func:`runtime_static_parameters`), senza elaborare i segnaposto ``{{...}}``
  (il valore e' quello dell'input, per esempio lo username).

REGOLA del lettore (correzione finale prima del deploy, difesa in
profondita'): un valore di questi campi conta SOLO se la risoluzione dei
binding lo ha scritto nella stessa esecuzione. La risoluzione parte da campi
vuoti, quindi dopo di essa ogni campo presente e' suo: chi la chiama mette nel
``runtime_context`` del contratto il registro :func:`runtime_bound_context`
(``{nodo: {campo: [chiavi]}}``). Il contratto d'esecuzione
(``orchestrator.execution_contract``), quando si costruisce o si rilegge nel
worker, conserva solo le chiavi del registro (:func:`retain_registered`): un
canvas che arriva dal client, salvato o clonato senza passare dalla
risoluzione non ha registro e i campi spariscono. Il worker senza contratto
(``POST /tasks``) li toglie tutti. Il ``runtime_context`` lo costruisce solo
il server (nessuna richiesta lo accetta dal client).
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

RUNTIME_STATIC_PARAMETERS = "runtime_static_parameters"

# Testa del percorso del bersaglio -> campo che l'esecuzione legge.
EXECUTION_FIELDS: Mapping[str, str] = {
    "static_parameters": RUNTIME_STATIC_PARAMETERS,
}
RUNTIME_BOUND_FIELDS = frozenset(EXECUTION_FIELDS.values())
# Chiave del ``runtime_context`` con il registro scritto dalla risoluzione.
RUNTIME_BOUND_REGISTRY = "runtime_bound_fields"


def execution_path(path: str) -> str:
    """Percorso in cui la risoluzione scrive il valore del bersaglio ``path``."""
    head, sep, rest = str(path).partition(".")
    field = EXECUTION_FIELDS.get(head)
    return f"{field}{sep}{rest}" if field else path


def _strip_mapping(container: Any) -> None:
    if isinstance(container, MutableMapping):
        for field in RUNTIME_BOUND_FIELDS:
            container.pop(field, None)


def strip_runtime_bound_fields(canvas: Any) -> Any:
    """Toglie (in place) i campi scritti dalla risoluzione da ogni nodo.

    Accetta un canvas dict o un ``CanvasState``; restituisce lo stesso oggetto.
    """
    nodes = canvas.get("nodes") if isinstance(canvas, Mapping) else getattr(canvas, "nodes", None)
    for node in nodes or []:
        if isinstance(node, MutableMapping):
            _strip_mapping(node)
            _strip_mapping(node.get("data"))
            continue
        _strip_mapping(getattr(node, "data", None))
        _strip_mapping(getattr(node, "__pydantic_extra__", None))
    return canvas


def _node_containers(node: Mapping[str, Any]) -> list[Any]:
    return [node, node.get("data")]


def runtime_bound_registry(canvas: Any) -> dict[str, dict[str, list[str]]]:
    """``{id nodo: {campo: chiavi}}`` dei campi presenti nel canvas risolto."""
    registry: dict[str, dict[str, list[str]]] = {}
    nodes = canvas.get("nodes") if isinstance(canvas, Mapping) else None
    for node in nodes or []:
        if not isinstance(node, Mapping) or not node.get("id"):
            continue
        for field in sorted(RUNTIME_BOUND_FIELDS):
            keys: set[str] = set()
            for container in _node_containers(node):
                values = container.get(field) if isinstance(container, Mapping) else None
                if isinstance(values, Mapping):
                    keys.update(str(key) for key in values)
            if keys:
                registry.setdefault(str(node["id"]), {})[field] = sorted(keys)
    return registry


def runtime_bound_context(canvas: Any) -> dict[str, Any]:
    """Voce del ``runtime_context`` per il canvas appena risolto (vuota se
    nessun binding ha scritto un campo: contratto identico a prima)."""
    registry = runtime_bound_registry(canvas)
    return {RUNTIME_BOUND_REGISTRY: registry} if registry else {}


def _registered_keys(registry: Any, node_id: str, field: str) -> frozenset[str]:
    if not isinstance(registry, Mapping):
        return frozenset()
    fields = registry.get(node_id)
    keys = fields.get(field) if isinstance(fields, Mapping) else None
    if not isinstance(keys, (list, tuple)):
        return frozenset()
    return frozenset(str(key) for key in keys)


def retain_registered(canvas: Any, registry: Any) -> Any:
    """Copia del canvas in cui i campi della risoluzione hanno solo le chiavi
    del registro (nessun registro = campi tolti). Canvas senza questi campi:
    restituito invariato (stesso oggetto)."""
    if not isinstance(canvas, Mapping):
        return canvas
    nodes = canvas.get("nodes")
    if not isinstance(nodes, list):
        return canvas
    changed = False
    new_nodes = []
    for node in nodes:
        if not isinstance(node, Mapping) or not any(
            isinstance(container, Mapping) and field in container
            for container in _node_containers(node) for field in RUNTIME_BOUND_FIELDS
        ):
            new_nodes.append(node)
            continue
        changed = True
        node = dict(node)
        data = node.get("data")
        if isinstance(data, Mapping):
            node["data"] = data = dict(data)
        for container in (node, data):
            if not isinstance(container, MutableMapping):
                continue
            for field in RUNTIME_BOUND_FIELDS:
                if field not in container:
                    continue
                allowed = _registered_keys(registry, str(node.get("id") or ""), field)
                values = container.pop(field)
                if isinstance(values, Mapping):
                    kept = {key: value for key, value in values.items() if str(key) in allowed}
                    if kept:
                        container[field] = kept
        new_nodes.append(node)
    return {**canvas, "nodes": new_nodes} if changed else canvas


def runtime_static_parameters(node_data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Valori dei parametri statici scritti dagli input runtime nel nodo API
    (``node_data`` = campi del nodo uniti al suo ``data``)."""
    if not isinstance(node_data, Mapping):
        return {}
    values = node_data.get(RUNTIME_STATIC_PARAMETERS)
    data = node_data.get("data")
    if not values and isinstance(data, Mapping):
        values = data.get(RUNTIME_STATIC_PARAMETERS)
    return dict(values) if isinstance(values, Mapping) else {}


def with_runtime_static_parameters(api_config: Mapping[str, Any], node_data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Configurazione API per l'esecuzione di un nodo: quella della
    connessione + i valori scritti dagli input runtime nel nodo (che contano
    piu' dei parametri statici della connessione per quelle chiavi)."""
    return {**dict(api_config or {}), RUNTIME_STATIC_PARAMETERS: runtime_static_parameters(node_data)}


def effective_static_parameters(api_config: Mapping[str, Any]) -> dict[str, Any]:
    """Parametri statici inviati: connessione, poi input runtime."""
    params = dict(api_config.get("static_parameters") or {})
    params.update(api_config.get(RUNTIME_STATIC_PARAMETERS) or {})
    return params


__all__ = [
    "EXECUTION_FIELDS",
    "RUNTIME_BOUND_FIELDS",
    "RUNTIME_BOUND_REGISTRY",
    "RUNTIME_STATIC_PARAMETERS",
    "effective_static_parameters",
    "execution_path",
    "retain_registered",
    "runtime_bound_context",
    "runtime_bound_registry",
    "runtime_static_parameters",
    "strip_runtime_bound_fields",
    "with_runtime_static_parameters",
]
