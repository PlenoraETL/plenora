"""Passo fallito di un'esecuzione: UNA regola per dire quale nodo ha fallito.

REGOLA (06/11/2026, esito di Manipola come Arricchisci)
-------------------------------------------------------
Un'eccezione sollevata dentro l'elaborazione di un nodo porta il nodo:
l'esecutore avvolge il lavoro del nodo in :func:`node_step`, che segna
l'eccezione (stesso tipo, stesso messaggio: non la avvolge in un'altra) con
id e tipo del nodo. Vince il nodo più interno: un'eccezione già segnata non
si risegna. Chi trasforma l'eccezione nell'esito (``_failure_step`` di
Manipola, preview) aggiunge :func:`failed_node_fields`:

    {"error_node_id": <id>, "error_node_type": <tipo>}

le stesse chiavi dell'esito di Arricchisci (``arricchisci.canvas_orchestrator``).
Senza nodo (errore fuori dai nodi) le chiavi non ci sono: l'esito resta
``{"success": False, "message": ...}`` come prima. Il worker
(``workers.tasks.pipeline_task``) le porta nel risultato del task.
"""
from __future__ import annotations

from typing import Any

from app.shared.contracts.payloads import JsonObject

ERROR_NODE_ID = "error_node_id"
ERROR_NODE_TYPE = "error_node_type"
FAILED_NODE_KEYS = (ERROR_NODE_ID, ERROR_NODE_TYPE)

_ATTR = "_plenora_failed_node"


def mark_failed_node(exc: BaseException, node_id: Any, node_type: Any) -> BaseException:
    """Segna ``exc`` con il nodo, se non è già segnata (vince il più interno)."""
    if getattr(exc, _ATTR, None) is None and node_id is not None:
        try:
            setattr(exc, _ATTR, (str(node_id), str(node_type) if node_type is not None else None))
        except (AttributeError, TypeError):
            pass  # eccezione senza __dict__: esito senza nodo, come prima
    return exc


class node_step:  # noqa: N801 - si usa come ``with node_step(node):``
    """Elaborazione di un nodo: un'eccezione che la attraversa porta il nodo.

    Context manager senza ``except``: ``__exit__`` segna l'eccezione e la
    lascia propagare intatta (mai soppressa, ``BaseException`` come
    ``KeyboardInterrupt`` non si segna).
    """

    def __init__(self, node: Any) -> None:
        self._node = node

    def __enter__(self) -> "node_step":
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        if isinstance(exc, Exception):
            mark_failed_node(exc, getattr(self._node, "id", None), getattr(self._node, "type", None))
        return False


def failed_node_fields(exc: BaseException | None) -> JsonObject:
    """Chiavi dell'esito per il nodo fallito (``{}`` se l'eccezione non lo porta)."""
    marked = getattr(exc, _ATTR, None) if exc is not None else None
    if not marked:
        return {}
    node_id, node_type = marked
    return {ERROR_NODE_ID: node_id, ERROR_NODE_TYPE: node_type}


__all__ = [
    "ERROR_NODE_ID", "ERROR_NODE_TYPE", "FAILED_NODE_KEYS",
    "failed_node_fields", "mark_failed_node", "node_step",
]
