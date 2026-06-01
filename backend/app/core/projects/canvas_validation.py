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

from typing import Any, Dict, List

from app.core.projects.canvas_access import find_orphan_processing_nodes
from app.core.projects.schema_inference import SchemaInferenceService
from app.shared.execution.graph import GraphCycleError, kahn_topological_order
from app.shared.execution.schema_types import InferredSchema
from app.shared.execution.validation_protocol import validator_for
from app.shared.execution.validation_types import Issue, ValidationReport


class _StaticCanvasService:
    """Adapter minimo: espone il canvas PASSATO come ``get_project_canvas``.

    Cruciale: a PUT si valida il canvas in arrivo (non ancora salvato), quindi
    l'inferenza schema deve girare su QUESTO canvas, non su quello persistito.
    """

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas

    def get_project_canvas(self, _project_id: str) -> Any:
        return self._canvas


def validate_canvas(canvas: Any, *, project_id: str = "") -> ValidationReport:
    """Valida un canvas. Ritorna un ``ValidationReport``.

    Strutturale (sempre) + per-nodo via i ``validate_node`` registrati al
    composition-root (#D), scoperti con ``validator_for(node.type)`` — nessun
    import diretto dei motori. Ogni validatore riceve l'``InferredSchema`` upstream
    (colonne + status) per decidere error-vs-warning (no falso-blocco).
    """
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

    # 4. Strutturali canvas-level (porting fedele dei RULE_ID FE).
    issues.extend(_structural_extra_issues(nodes, edges))

    # 5. Validazione per-nodo via registry (#D): schema/colonne/regole di motore.
    issues.extend(_validate_nodes(canvas, nodes, edges, node_ids, project_id))

    return ValidationReport(issues=tuple(issues))


def _structural_extra_issues(nodes: list, edges: list) -> List[Issue]:
    """Invarianti canvas-level: canvas vuoto, nodo result/carica unico e collegato."""
    out: List[Issue] = []
    if not nodes:
        return [Issue(node_id="", severity="error", code="CANVAS_EMPTY",
                      message="Canvas vuoto. Aggiungi almeno una sorgente e un nodo Output.",
                      suggestion=None)]

    result_nodes = [n for n in nodes if getattr(n, "type", None) == "result"]
    if not result_nodes:
        out.append(Issue(node_id="", severity="error", code="RESULT_NODE_MISSING",
                         message='Nodo Output mancante. Aggiungi un nodo "Output".',
                         suggestion=None))
    elif len(result_nodes) > 1:
        out.append(Issue(node_id="", severity="error", code="RESULT_NODE_DUPLICATED",
                         message=f"Troppi nodi Output ({len(result_nodes)}). Può essercene solo uno.",
                         suggestion=None))

    carica_nodes = [n for n in nodes if getattr(n, "type", None) == "carica"]
    if len(carica_nodes) > 1:
        out.append(Issue(node_id="", severity="error", code="CARICA_DUPLICATED",
                         message=f"Troppi nodi Carica ({len(carica_nodes)}). Può essercene solo uno.",
                         suggestion=None))

    # RESULT_NOT_CONNECTED: ogni result senza edge entrante.
    targets = {getattr(e, "target", None) for e in edges}
    for rn in result_nodes:
        if rn.id not in targets:
            out.append(Issue(node_id=str(rn.id), severity="error", code="RESULT_NOT_CONNECTED",
                             message="Nodo Output non connesso. Collega almeno una sorgente all'output.",
                             suggestion=None))
    return out


def _validate_nodes(
    canvas: Any, nodes: list, edges: list, node_ids: set, project_id: str,
) -> List[Issue]:
    """Dispatch ai ``validate_node`` registrati, passando l'upstream tipizzato."""
    relevant = [n for n in nodes if validator_for(n.type) is not None]
    if not relevant:
        return []  # nessun motore registrato → niente inferenza (fast path)

    service = SchemaInferenceService(_StaticCanvasService(canvas))
    inputs_by_target: Dict[str, List[str]] = {}
    for e in edges:
        s, t = getattr(e, "source", None), getattr(e, "target", None)
        if s in node_ids and t in node_ids:
            inputs_by_target.setdefault(t, []).append(s)

    out: List[Issue] = []
    for node in relevant:
        upstream = _upstream_for_node(
            inputs_by_target.get(node.id, []), service, project_id,
        )
        out.extend(validator_for(node.type).validate_node(node, upstream=upstream))
    return out


def _upstream_for_node(
    input_ids: List[str], service: SchemaInferenceService, project_id: str,
) -> InferredSchema:
    """Merge degli schemi tipizzati dei nodi in input.

    Una sola input → il suo InferredSchema. Multi-input → unione colonne +
    status ``indeterminate`` se QUALSIASI input è indeterminate (no falso-blocco
    a valle). Nessun input → vuoto certo.
    """
    if not input_ids:
        return InferredSchema(columns=[], status="certain")
    typed = [service.infer_node_schema_typed(project_id, sid) for sid in input_ids]
    if len(typed) == 1:
        return typed[0]
    merged_cols: list = []
    status = "certain"
    for t in typed:
        merged_cols.extend(t.columns)
        if t.status == "indeterminate":
            status = "indeterminate"
    return InferredSchema(columns=merged_cols, status=status)


__all__ = ["validate_canvas"]
