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
- ``executed_canvas``: la stessa scelta (Output, oppure il passo e cio' che
  sta a monte) per chi deve sapere cosa un'esecuzione LEGGERA', non per
  eseguirla: la chiusura dei progetti a monte con «Utente che esegue»
  (``projects.upstream_identity``, correzioni minori 11/11/2026, A5) legge i
  nodi ``project_source`` SOLO da qui, cosi' un ramo scollegato non blocca.

- ``flow_node_ids``: i nodi che entrano davvero nel risultato (senza i nodi
  fuori dal flusso scollegati), per la verifica degli input «Utente che
  esegue» (``projects.identity_effect``).

JOIN (filtro «Utente che esegue» ignorato, 11/11/2026): un arco fra due
tabelle SQL di Estrai e' una JOIN e vale nei due versi (:func:`is_join_edge`):
una tabella collegata alla radice «in avanti» arriva all'Output come una
collegata «all'indietro». Prima restava fuori dalla query, con i suoi filtri.

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


#: Tabelle SQL di Estrai: un arco fra due di loro e' una JOIN (relazione
#: simmetrica), non un flusso di dati con una direzione.
SQL_TABLE_TYPES = frozenset({"source"})


def is_join_edge(edge: Any, types_by_id: Mapping[str, Any]) -> bool:
    """REGOLA UNICA DELLE JOIN (filtro «Utente che esegue» ignorato,
    11/11/2026): un arco fra due tabelle SQL di Estrai e' una JOIN e vale nei
    due versi. L'utente lo disegna dalla tabella radice verso quella
    collegata (radice -> collegata -> nessun arco verso l'Output): la tabella
    collegata entra comunque nella query, con le sue colonne, i suoi filtri e
    la JOIN. La usano l'ambito dell'esecuzione (:func:`unreached_node_ids`,
    :func:`run_canvas`, :func:`flow_node_ids`) e il costruttore SQL
    (``estrai.query_generator.query_tables``)."""
    return (
        types_by_id.get(_field(edge, "source")) in SQL_TABLE_TYPES
        and types_by_id.get(_field(edge, "target")) in SQL_TABLE_TYPES
    )


def _types_by_id(nodes: list[Any]) -> Mapping[Any, Any]:
    return {_field(node, "id"): _field(node, "type") for node in nodes}


def _walk(
    start: set[str], edges: list[Any], *, upstream: bool,
    types_by_id: Mapping[str, Any] | None = None,
) -> set[str]:
    """Visita del grafo. Con ``types_by_id`` le JOIN (:func:`is_join_edge`)
    si percorrono nei due versi."""
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
            elif (
                types_by_id is not None and there == current and here not in seen
                and is_join_edge(edge, types_by_id)
            ):
                seen.add(here)
                pending.append(here)
    return seen


def _output_flow(nodes: list[Any], edges: list[Any]) -> tuple[set[Any], set[Any]]:
    """``(finali, nodi a monte dei finali)``: le JOIN valgono nei due versi."""
    finals = {_field(node, "id") for node in nodes if _field(node, "type") in FINAL_NODE_TYPES}
    if not finals:
        return set(), set()
    return finals, _walk(finals, edges, upstream=True, types_by_id=_types_by_id(nodes))


def unreached_node_ids(canvas: Any) -> set[str]:
    """Nodi dei rami che non arrivano all'Output (vuoto senza Output).

    ``canvas``: ``CanvasState`` o il dict del canvas (worker di Arricchisci).
    Una tabella collegata da una JOIN a una tabella che arriva all'Output
    arriva all'Output (:func:`is_join_edge`).
    """
    nodes, edges = _parts(canvas)
    finals, upstream = _output_flow(nodes, edges)
    if not finals:
        return set()
    reached = upstream | _walk(finals, edges, upstream=False)
    return {
        _field(node, "id") for node in nodes
        if _field(node, "id") not in reached and _field(node, "type") not in OFF_BRANCH_TYPES
    }


def flow_node_ids(canvas: Any, node_id: Any = None) -> set[Any]:
    """I nodi i cui dati o parametri entrano davvero nel risultato
    dell'esecuzione (``node_id`` None: l'Output e i nodi a monte, con le JOIN
    nei due versi) o dell'anteprima di un passo (il passo e i suoi antenati,
    come :func:`preview_canvas`). A differenza di :func:`run_canvas` non
    tiene i nodi fuori dal flusso (``OFF_BRANCH_TYPES``) scollegati: un nodo
    che non arriva al risultato non ha effetto. Lo usa la verifica degli
    input «Utente che esegue» (``projects.identity_effect``)."""
    nodes, edges = _parts(canvas)
    if node_id is None:
        finals, upstream = _output_flow(nodes, edges)
        return finals | upstream
    if not any(_field(node, "id") == str(node_id) for node in nodes):
        return set()
    return _step_ancestors(nodes, edges, str(node_id))


def _step_ancestors(nodes: list[Any], edges: list[Any], node_id: str) -> set[Any]:
    """Il passo e i suoi antenati. Se il passo e' l'Output, le JOIN valgono
    nei due versi (la stessa query dell'esecuzione); per una tabella o un
    passo resta la visita di prima (i soli antenati)."""
    types_by_id = _types_by_id(nodes)
    if types_by_id.get(node_id) in FINAL_NODE_TYPES:
        return _walk({node_id}, edges, upstream=True, types_by_id=types_by_id)
    return _walk({node_id}, edges, upstream=True)


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


def executed_canvas(canvas: Any, node_id: Any = None) -> Any:
    """La parte del canvas che un'esecuzione (``node_id`` None: l'Output, con
    :func:`run_canvas`) o l'anteprima di un passo (``node_id``: il passo e i
    suoi antenati, come :func:`preview_canvas`) esegue. Accetta e restituisce
    ``CanvasState`` o dict; un passo che non c'e' lascia il canvas com'e'
    (l'anteprima lo rifiuta col suo messaggio)."""
    if node_id is None:
        return run_canvas(canvas)
    nodes, edges = _parts(canvas)
    if not any(_field(node, "id") == str(node_id) for node in nodes):
        return canvas
    ancestors = _step_ancestors(nodes, edges, str(node_id))
    kept_nodes = [node for node in nodes if _field(node, "id") in ancestors]
    kept_edges = [
        edge for edge in edges
        if _field(edge, "source") in ancestors and _field(edge, "target") in ancestors
    ]
    if isinstance(canvas, Mapping):
        return {**canvas, "nodes": kept_nodes, "edges": kept_edges}
    if hasattr(canvas, "model_copy"):
        return canvas.model_copy(update={"nodes": kept_nodes, "edges": kept_edges})
    scoped = copy.copy(canvas)
    scoped.nodes, scoped.edges = kept_nodes, kept_edges
    return scoped


def preview_canvas(
    canvas: CanvasState, node_id: str, *, with_result: bool = False,
) -> CanvasState:
    nodes = {node.id: node for node in canvas.nodes}
    if node_id not in nodes:
        raise ValueError(f"Preview: nodo '{node_id}' non trovato nel canvas.")
    ancestors = _step_ancestors(list(canvas.nodes), list(canvas.edges), node_id)
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
