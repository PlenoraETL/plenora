"""Orchestratore orizzontale di validazione canvas (Epic Canvas Validation).

``validate_canvas`` produce un ``ValidationReport`` tipizzato. Fase 1.5: i check
**strutturali canvas-intrinseci** — cicli (kahn ``on_cycle='raise'``), edge
danglanti (verso nodi inesistenti), nodi orfani (processing senza input).

Non importa i motori (estrai/manipola/...): la validazione per-nodo (schema,
formula, mapping) arriva in 1.6 via ``validator_for`` (registry, #D). L'authz dei
ref cross-tenant (``find_inaccessible_canvas_refs``) resta al boundary del router
(serve lookup DB-backed di visibilità): l'engine resta puro sul canvas.
"""

from __future__ import annotations

from typing import Any, List

from app.core.projects.canvas_access import find_orphan_processing_nodes
from app.shared.execution.graph import GraphCycleError, kahn_topological_order
from app.shared.execution.validation_types import Issue, ValidationReport


def validate_canvas(canvas: Any) -> ValidationReport:
    """Valida la struttura di un canvas. Ritorna un ``ValidationReport``."""
    issues: List[Issue] = []
    nodes = list(getattr(canvas, "nodes", None) or [])
    edges = list(getattr(canvas, "edges", None) or [])
    node_ids = {n.id for n in nodes}

    # 1. Edge danglanti: source/target che non corrispondono a un nodo.
    for edge in edges:
        for endpoint in ("source", "target"):
            ref = getattr(edge, endpoint, None)
            if ref and ref not in node_ids:
                issues.append(Issue(
                    node_id=str(ref), severity="error",
                    code="structure.dangling_edge",
                    message=f"Edge {endpoint} '{ref}' non corrisponde ad alcun nodo del canvas",
                    suggestion={"endpoint": endpoint},
                ))

    # 2. Cicli: kahn sui soli edge fra nodi esistenti (i danglanti già segnalati).
    valid_edges = [
        (e.source, e.target) for e in edges
        if getattr(e, "source", None) in node_ids and getattr(e, "target", None) in node_ids
    ]
    try:
        kahn_topological_order(node_ids, valid_edges, on_cycle="raise")
    except GraphCycleError as exc:
        issues.append(Issue(
            node_id="", severity="error", code="lineage.cycle",
            message=f"Il canvas contiene un ciclo: {exc}",
            suggestion=None,
        ))

    # 3. Nodi orfani: processing-node senza edge entrante (i source-like sono ok).
    for orphan_id in find_orphan_processing_nodes(canvas):
        issues.append(Issue(
            node_id=str(orphan_id), severity="error", code="structure.orphan",
            message=f"Nodo '{orphan_id}' richiede almeno un edge entrante",
            suggestion=None,
        ))

    return ValidationReport(issues=tuple(issues))


__all__ = ["validate_canvas"]
