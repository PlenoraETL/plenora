"""Quale parte del canvas esegue un'esecuzione o un'anteprima.

Regola unica (collaudo live L3, 07/11/2026): si esegue solo cio' che arriva a
un nodo finale. Il nodo finale e' l'Output (``result``); i caricamenti
(Carica, Carica su ArcGIS, Data Source in scrittura) vengono DOPO l'Output e
partono solo se collegati a lui (``shared.execution.carica_dispatcher``),
quindi un ramo che arriva a un caricamento senza passare dall'Output non
arriva a un nodo finale. E' la stessa regola del pannello del frontend
(``utils/canvasGraph.analyzeCanvas``: «non arriva all'Output»).

- ``run_canvas``: esecuzione e anteprima dell'Output. Restano i nodi a monte
  dell'Output, l'Output, cio' che sta a valle dell'Output (i caricamenti) e i
  nodi che non sono rami (``OFF_BRANCH_TYPES``: parametri, input runtime,
  note: valgono anche scollegati). I rami scollegati non si eseguono e non
  bloccano; Valida li segnala come avviso (``canvas_validation``). Senza un
  Output il canvas e' in costruzione e resta com'e' (l'esecutore lo rifiuta
  con il suo messaggio).
- ``preview_canvas``: anteprima di un passo: il passo e cio' che sta a monte.
- ``scoped_canvas``: l'unico ingresso degli esecutori (passo o Output).

Da portare in plenora-data-tools2: la stessa regola nel motore Manipola.
"""

import copy
from collections.abc import Mapping
from typing import Any

from app.core.projects.canvas_models import CanvasState, Edge, Node, Position

#: Il nodo finale di un canvas: i suoi antenati sono cio' che si esegue.
FINAL_NODE_TYPES = frozenset({"result"})

#: Nodi che non sono rami del flusso: restano anche se non arrivano all'Output.
OFF_BRANCH_TYPES = frozenset({
    "staticValues",   # parametri: si leggono anche senza collegamento
    "project_input",  # input runtime: legati ai parametri dei passi, non da archi
    "comment",        # note
})


def _field(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)


def _parts(canvas: Any) -> tuple[list[Any], list[Any]]:
    if isinstance(canvas, Mapping):
        return list(canvas.get("nodes") or []), list(canvas.get("edges") or [])
    return list(getattr(canvas, "nodes", None) or []), list(getattr(canvas, "edges", None) or [])


def _walk(start: set[str], edges: list[Any], *, upstream: bool) -> set[str]:
    seen = set(start)
    pending = list(start)
    while pending:
        current = pending.pop()
        for edge in edges:
            source, target = _field(edge, "source"), _field(edge, "target")
            here, there = (target, source) if upstream else (source, target)
            if here == current and there not in seen:
                seen.add(there)
                pending.append(there)
    return seen


def unreached_node_ids(canvas: Any) -> set[str]:
    """Nodi dei rami che non arrivano all'Output (vuoto senza Output).

    ``canvas``: ``CanvasState`` o il dict del canvas (worker di Arricchisci).
    """
    nodes, edges = _parts(canvas)
    finals = {_field(node, "id") for node in nodes if _field(node, "type") in FINAL_NODE_TYPES}
    if not finals:
        return set()
    reached = _walk(finals, edges, upstream=True) | _walk(finals, edges, upstream=False)
    return {
        _field(node, "id") for node in nodes
        if _field(node, "id") not in reached and _field(node, "type") not in OFF_BRANCH_TYPES
    }


def run_canvas(canvas: Any) -> Any:
    """Il canvas che un'esecuzione esegue: senza i rami che non arrivano all'Output.

    Restituisce lo stesso tipo ricevuto (``CanvasState`` o dict); invariato
    se non c'e' nulla da togliere.
    """
    unreached = unreached_node_ids(canvas)
    if not unreached:
        return canvas
    nodes, edges = _parts(canvas)
    kept_nodes = [node for node in nodes if _field(node, "id") not in unreached]
    kept_edges = [
        edge for edge in edges
        if _field(edge, "source") not in unreached and _field(edge, "target") not in unreached
    ]
    if isinstance(canvas, Mapping):
        return {**canvas, "nodes": kept_nodes, "edges": kept_edges}
    if hasattr(canvas, "model_copy"):
        return canvas.model_copy(update={"nodes": kept_nodes, "edges": kept_edges})
    scoped = copy.copy(canvas)  # oggetti semplici (chiamanti interni, test)
    scoped.nodes, scoped.edges = kept_nodes, kept_edges
    return scoped


def preview_canvas(
    canvas: CanvasState, node_id: str, *, with_result: bool = False,
) -> CanvasState:
    nodes = {node.id: node for node in canvas.nodes}
    if node_id not in nodes:
        raise ValueError(f"Preview: nodo '{node_id}' non trovato nel canvas.")
    ancestors = _walk({node_id}, list(canvas.edges), upstream=True)
    scoped = canvas.model_copy(update={
        "nodes": [node for node in canvas.nodes if node.id in ancestors],
        "edges": [edge for edge in canvas.edges if edge.source in ancestors and edge.target in ancestors],
    })
    if with_result and nodes[node_id].type != "result":
        # A neutral output lets existing SQL/enrichment engines consume the
        # selected step without inheriting downstream output configuration.
        terminal_id = "__preview_result__"
        while terminal_id in nodes:
            terminal_id += "_"
        edge_id = terminal_id
        existing_edge_ids = {edge.id for edge in canvas.edges}
        while edge_id in existing_edge_ids:
            edge_id += "_"
        scoped = scoped.model_copy(update={
            "nodes": [*scoped.nodes, Node(
                id=terminal_id, type="result", position=Position(x=0, y=0),
            )],
            "edges": [*scoped.edges, Edge(
                id=edge_id, source=node_id, target=terminal_id, join_type="output",
            )],
        })
    return scoped


def scoped_canvas(
    canvas: CanvasState, node_id: Any = None, *, with_result: bool = False,
) -> CanvasState:
    """Unico ingresso degli esecutori: anteprima di un passo, oppure l'Output."""
    if node_id is not None:
        return preview_canvas(canvas, str(node_id), with_result=with_result)
    return run_canvas(canvas)
