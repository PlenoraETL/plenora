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
    "RUNTIME_STATIC_PARAMETERS",
    "effective_static_parameters",
    "execution_path",
    "runtime_static_parameters",
    "strip_runtime_bound_fields",
    "with_runtime_static_parameters",
]
