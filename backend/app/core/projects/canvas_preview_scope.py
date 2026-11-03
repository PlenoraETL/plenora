"""Selezione del sottografo per anteprime di un singolo passo."""

from app.core.projects.canvas_models import CanvasState, Edge, Node, Position


def preview_canvas(
    canvas: CanvasState, node_id: str, *, with_result: bool = False,
) -> CanvasState:
    nodes = {node.id: node for node in canvas.nodes}
    if node_id not in nodes:
        raise ValueError(f"Preview: nodo '{node_id}' non trovato nel canvas.")
    ancestors: set[str] = set()
    pending = [node_id]
    while pending:
        current = pending.pop()
        if current in ancestors:
            continue
        ancestors.add(current)
        pending.extend(edge.source for edge in canvas.edges if edge.target == current)
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
