"""Selezione del sottografo per anteprime di un singolo passo."""

from app.core.projects.canvas_models import CanvasState


def preview_canvas(canvas: CanvasState, node_id: str) -> CanvasState:
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
    return canvas.model_copy(update={
        "nodes": [node for node in canvas.nodes if node.id in ancestors],
        "edges": [edge for edge in canvas.edges if edge.source in ancestors and edge.target in ancestors],
    })
