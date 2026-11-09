"""Orchestratore orizzontale di validazione canvas.

``validate_canvas`` produce un ``ValidationReport`` tipizzato. I check
**strutturali canvas-intrinseci** coprono cicli (kahn ``on_cycle='raise'``), edge
danglanti (verso nodi inesistenti), nodi orfani (processing senza input).

Non importa i motori (estrai/manipola/...): la validazione per-nodo (schema,
formula, mapping) arriva via ``validator_for``. L'authz dei
ref cross-tenant (``find_inaccessible_canvas_refs``) resta al boundary del router
(serve lookup DB-backed di visibilità): l'engine resta puro sul canvas.
"""

from __future__ import annotations
from dataclasses import replace
from app.core.arricchisci.canvas_orchestrator import _merge_node_data
from app.core.arricchisci.enrichment_service import build_enrichment_executor
from app.core.arricchisci.request_preparation import prepare_api_request
from app.core.eseguibili.manifest import compile_manifest
from app.core.estrai.query_generator import QueryGenerator
from app.core.manipola.node_validator import ManipolaNodeValidator
from app.core.manipola.preparation import empty_dataframe_from_column_metadata

from sqlalchemy.exc import SQLAlchemyError
from psycopg import Error as PsycopgError
from app.utils.logger import get_logger

from app.core.manipola.transform_ordering import incoming_edge_sort_key_for_nodes
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from app.core.projects.canvas_access import ORPHAN_STEP_MESSAGE, find_orphan_processing_nodes
from app.core.projects.canvas_preview_scope import FINAL_NODE_TYPES, run_canvas, unreached_node_ids
from app.core.projects.schema_inference import SchemaInferenceService
from app.core.projects.canvas_schema_preparation import infer_canvas_columns
from app.core.projects.canvas_models import CanvasState
from app.core.projects import schema_execution_sources
from app.shared.execution.api_mapping import mapping_destination
from app.core.arricchisci.enrichment_service import resolve_enrichment_output_mapping
from app.shared.execution.graph import GraphCycleError, kahn_topological_order
from app.shared.execution.schema_types import InferredSchema
from app.shared.execution.runtime_input_kinds import project_input_role
from app.shared.execution.validation_protocol import validator_for
from app.shared.execution.validation_types import Issue, ValidationReport


def _node_config(node: Any, keys: tuple[str, ...]) -> Mapping[str, Any]:
    data = getattr(node, "data", None) or {}
    config: dict[str, Any] = dict(data) if isinstance(data, Mapping) else {}
    extra = getattr(node, "model_extra", None) or getattr(node, "__pydantic_extra__", None) or {}
    if isinstance(extra, Mapping):
        config.update({k: v for k, v in extra.items() if v is not None})
    for key in keys:
        attrs = getattr(node, "__dict__", {})
        if isinstance(attrs, Mapping) and key in attrs:
            value = attrs[key]
        else:
            continue
        if value is not None:
            config[key] = value
    return config


class _StaticCanvasService:
    """Adapter minimo: espone il canvas PASSATO come ``get_project_canvas``.

    Cruciale: a PUT si valida il canvas in arrivo (non ancora salvato), quindi
    l'inferenza schema deve girare su QUESTO canvas, non su quello persistito.
    """

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas

    def get_project_canvas(self, _project_id: str) -> Any:
        return self._canvas


#: Messaggio di un passo in un ramo che non arriva all'Output (collaudo live L3).
UNREACHED_MESSAGE = "Questo passo non arriva all'Output: non viene eseguito e i suoi dati non finiscono nel risultato."
#: Output senza ingressi: un avviso solo (residui collaudo live M4).
OUTPUT_UNCONNECTED_MESSAGE = "L'Output non è collegato: nessun passo viene eseguito finché non colleghi l'ultimo passo all'Output."


def validate_canvas(
    canvas: Any, *, project_id: str = "", project_type: str | None = None,
    available_api_connection_ids: set | None = None,
    execution_context: Mapping | None = None,
) -> ValidationReport:
    """Valida un canvas. Ritorna un ``ValidationReport``.

    Regola dei rami scollegati (collaudo live L3, la stessa dell'esecuzione:
    ``canvas_preview_scope.run_canvas``): i passi che non arrivano all'Output
    non si eseguono, quindi non bloccano. Ogni loro problema diventa un
    avviso e ognuno ha l'avviso ``structure.unreached`` (salvo una sorgente
    gia' segnalata come isolata).
    """
    issues = _canvas_issues(
        canvas, project_id=project_id, project_type=project_type,
        available_api_connection_ids=available_api_connection_ids,
        execution_context=execution_context,
    )
    unreached = unreached_node_ids(canvas) if getattr(canvas, "nodes", None) is not None else set()
    if unreached:
        isolated = {i.node_id for i in issues if i.code == "SOURCE_ISOLATED"}
        issues = [
            replace(i, severity="warning") if i.node_id in unreached and i.severity == "error" else i
            for i in issues
        ]
        # Residui collaudo live M4: con l'Output senza ingressi nessun passo
        # lo raggiunge; un solo avviso sull'Output, non uno per passo.
        finals = [_field(n, "id") for n in canvas.nodes if _field(n, "type") in FINAL_NODE_TYPES]
        targets = {_field(e, "target") for e in (getattr(canvas, "edges", None) or [])}
        if finals and not targets.intersection(finals):
            issues += [Issue(node_id=str(final_id), severity="warning", code="structure.output_unconnected",
                             message=OUTPUT_UNCONNECTED_MESSAGE, suggestion=None) for final_id in finals]
        else:
            issues += [
                Issue(node_id=str(node_id), severity="warning", code="structure.unreached",
                      message=UNREACHED_MESSAGE, suggestion=None)
                for node_id in sorted(unreached) if node_id not in isolated
            ]
    return ValidationReport(issues=tuple(issues))


def _canvas_issues(
    canvas: Any, *, project_id: str = "", project_type: str | None = None,
    available_api_connection_ids: set | None = None,
    execution_context: Mapping | None = None,
) -> List[Issue]:
    """Tutti i problemi del canvas, prima della regola dei rami scollegati.

    Strutturale (sempre) + per-nodo via i ``validate_node`` registrati al
    composition-root, scoperti con ``validator_for(node.type)`` — nessun
    import diretto dei motori. Ogni validatore riceve l'``InferredSchema`` upstream
    (colonne + status) per decidere error-vs-warning (no falso-blocco).

    ``project_type`` (estrai/manipola/arricchisci) abilita le regole
    *type-specific* senza falsi positivi (es. in estrai ogni edge inter-tabella è
    un join; in manipola è data-flow). None → le regole type-specific sono saltate.

    ``available_api_connection_ids`` (DB-dependent, iniettato dal boundary DB-aware
    — l'endpoint /validate): se fornito, abilita ARRICCHISCI_API_CONNECTION_STALE
    per gli api_enrich il cui ``api_id`` non è tra le connessioni esistenti. None →
    regola saltata (es. dal gate orizzontale, che resta disaccoppiato da arricchisci;
    il runtime fallisce comunque in chiaro su api_id mancante).
    """
    issues: List[Issue] = []
    nodes = list(getattr(canvas, "nodes", None) or [])
    edges = list(getattr(canvas, "edges", None) or [])
    node_ids = {n.id for n in nodes}
    if project_type in {"estrai", "manipola", "arricchisci"} and nodes:
        try:
            compile_manifest(for_project=True, source_project_id=project_id, project_type=project_type,
                             canvas=canvas.model_dump() if hasattr(canvas, "model_dump") else {"nodes": [vars(n) for n in nodes], "edges": [vars(e) for e in edges]})
        except ValueError as exc:
            issues.append(Issue(node_id="", severity="error", code="runtime_inputs.invalid", message=str(exc)))
    if project_type == "estrai" and any(getattr(n, "table_name", None) for n in nodes):
        try:
            connection_id = (execution_context or {}).get("default_connection_id")
            if not connection_id:
                raise ValueError("Connessione SQL non risolta")
            sql_options = schema_execution_sources.load_sql_options(connection_id)
        except (SQLAlchemyError, PsycopgError, OSError, ValueError, RuntimeError, TypeError):
            sql_options = None
            issues.append(Issue(node_id="", severity="warning", code="sql.not_guaranteed",
                                message="Compilazione SQL non garantibile: connessione/provider non leggibile"))
        for result in (n for n in nodes if n.type == "result"):
            if sql_options is None:
                continue
            try:
                QueryGenerator().generate_sql(nodes, edges, result.id, **sql_options)
            except (ValueError, TypeError, KeyError) as exc:
                issues.append(Issue(node_id=result.id, severity="error", code="sql.invalid", message=str(exc)))

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
            message=ORPHAN_STEP_MESSAGE,
            suggestion=None,
        ))

    # 4. Strutturali canvas-level (porting fedele dei RULE_ID FE).
    issues.extend(_structural_extra_issues(nodes, edges))
    issues.extend(_type_specific_issues(nodes, edges, project_type, available_api_connection_ids))
    issues.extend(_source_node_issues(nodes, edges))
    issues.extend(_column_name_conflict_issues(nodes))
    issues.extend(_result_geo_issue(nodes, edges, canvas=canvas, project_id=project_id, project_type=project_type))

    # 5. Join sugli edge (config + risoluzione colonne di join).
    issues.extend(_edge_join_issues(canvas, nodes, edges, node_ids, project_id, project_type))

    # 6. Validazione per-nodo via registry (#D): schema/colonne/regole di motore.
    issues.extend(_validate_nodes(canvas, nodes, edges, node_ids, project_id))

    return issues


# Edge verso questi target non sono join (sono output verso sink).
_JOIN_SKIP_TARGET_TYPES = frozenset({"result", "carica", "carica_arcgis", "datasource_node"})


# Sorgenti non supportate da ESTRAI (SQL-only): porting fedele del FE.
_ESTRAI_UNSUPPORTED_SOURCE_TYPES = frozenset({"staticValues", "datasource_node", "project_input"})

# Tipi-nodo sorgente (per ISOLATED); sottoinsieme con colonne dichiarate (NO_VISIBLE).
_SOURCE_NODE_TYPES = frozenset({
    "source", "feature_source", "project_source", "file_source",
    "staticValues", "datasource_node", "project_input",
})
_DECLARED_COLUMN_SOURCE_TYPES = frozenset({
    "source", "feature_source", "project_source", "file_source",
    "project_input",
})
_GEO_OUTPUT_FORMATS = frozenset({"geojson", "gpkg", "shp"})


def _raw_columns(node) -> list:
    cols = getattr(node, "columns", None)
    if not isinstance(cols, list):
        data = getattr(node, "data", None)
        cols = data.get("columns") if isinstance(data, dict) else None
    return cols if isinstance(cols, list) else []


def _visible_columns(node) -> list:
    """[(name, type)] delle colonne VISIBILI (tollera Column-obj e dict)."""
    out = []
    for c in _raw_columns(node):
        if isinstance(c, dict):
            if c.get("visible", True) is False:
                continue
            out.append((c.get("alias") or c.get("name") or "", str(c.get("type") or "")))
        else:
            if getattr(c, "visible", True) is False:
                continue
            out.append((getattr(c, "alias", None) or getattr(c, "name", "") or "", str(getattr(c, "type", None) or "")))
    return out


def _source_node_issues(nodes: list, edges: list) -> List[Issue]:
    """SOURCE_ISOLATED (warning) + SOURCE_NO_VISIBLE_COLUMNS (error). No inferenza."""
    out: List[Issue] = []
    touched = {getattr(e, "source", None) for e in edges} | {getattr(e, "target", None) for e in edges}
    for n in nodes:
        ntype = getattr(n, "type", None)
        if ntype not in _SOURCE_NODE_TYPES:
            continue
        if ntype == "project_input" and project_input_role(n) != "source":
            continue
        if n.id not in touched:
            out.append(Issue(
                node_id=str(n.id), severity="warning", code="SOURCE_ISOLATED",
                message="Sorgente non collegata ad alcun nodo — verrà ignorata durante l'esecuzione.",
                suggestion=None,
            ))
        if ntype in _DECLARED_COLUMN_SOURCE_TYPES and _raw_columns(n) and not _visible_columns(n):
            out.append(Issue(
                node_id=str(n.id), severity="error", code="SOURCE_NO_VISIBLE_COLUMNS",
                message="Tutte le colonne sono state deselezionate. Seleziona almeno una colonna.",
                suggestion=None,
            ))
    return out


def _column_name_conflict_issues(nodes: list) -> List[Issue]:
    """COLUMN_NAME_CONFLICT (warning): stesso nome-colonna finale in più sorgenti."""
    by_name: Dict[str, set] = {}
    for n in nodes:
        if getattr(n, "type", None) not in _DECLARED_COLUMN_SOURCE_TYPES:
            continue
        for name, _t in _visible_columns(n):
            if name:
                by_name.setdefault(name, set()).add(n.id)
    out: List[Issue] = []
    for name, srcs in by_name.items():
        if len(srcs) > 1:
            out.append(Issue(
                node_id="", severity="warning", code="COLUMN_NAME_CONFLICT",
                message=f'Conflitto nome colonna: "{name}" appare in più sorgenti. Aggiungi un alias per disambiguare.',
                suggestion={"column": name, "nodes": sorted(str(s) for s in srcs)},
            ))
    return out


def _result_geo_issue(nodes: list, edges: list, *, canvas: Any = None, project_id: str = "", project_type: str | None = None) -> List[Issue]:
    """RESULT_GEO_NO_GEOMETRY: format geo ma nessuna colonna geometry nel percorso."""
    nodes_by_id = {n.id: n for n in nodes}
    results = [n for n in nodes if getattr(n, "type", None) == "result"]
    inferred = infer_canvas_columns(canvas or CanvasState(nodes=nodes, edges=edges),
                                    project_id=project_id, project_type=project_type) if any(
        str((getattr(r, "data", None) or {}).get("format") or "").lower() in _GEO_OUTPUT_FORMATS for r in results
    ) else {}
    out: List[Issue] = []
    for r in results:
        fmt = str(((getattr(r, "data", None) or {}).get("format") or "")).lower()
        if fmt not in _GEO_OUTPUT_FORMATS:
            continue
        seen = _backward_reachable(r.id, edges)
        has_geom = any(
            str(t).lower().startswith("geometry")
            for nid in seen
            for _name, t in ([(c["name"], c.get("type", "")) for c in inferred.get(nid, [])]
                             if getattr(nodes_by_id.get(nid), "type", None) not in _SOURCE_NODE_TYPES
                             else _visible_columns(nodes_by_id.get(nid)))
            if nodes_by_id.get(nid) is not None
        )
        if not has_geom:
            out.append(Issue(
                node_id=str(r.id), severity="error", code="RESULT_GEO_NO_GEOMETRY",
                message=f'Formato "{fmt.upper()}" richiede una colonna geometry, ma nessuna è disponibile nel percorso.',
                suggestion={"format": fmt},
            ))
    return out


def _backward_reachable(start_id, edges: list) -> set:
    """Set di nodi backward-reachable da ``start_id`` (risalendo gli edge)."""
    inputs_by_target: Dict[str, List[str]] = {}
    for e in edges:
        inputs_by_target.setdefault(getattr(e, "target", None), []).append(getattr(e, "source", None))
    seen, stack = set(), [start_id]
    while stack:
        for src in inputs_by_target.get(stack.pop(), []):
            if src and src not in seen:
                seen.add(src)
                stack.append(src)
    return seen


def _arricchisci_node_needs_source(node) -> bool:
    """True se l'api_enrich richiede una sorgente dati a monte (parità FE
    ``nodeNeedsSource``): ha un parametro ``required``, ``mode != 'fixed'``, non in
    ``static_parameters``. Gli enricher-generatori (nessun param così) NON la
    richiedono → non devono triggerare ARRICCHISCI_NO_SOURCES.
    """
    data = getattr(node, "data", None) or {}
    params = data.get("parameters")
    if not isinstance(params, list):
        return False
    static_params = data.get("static_parameters")
    static_params = static_params if isinstance(static_params, dict) else {}
    return any(
        isinstance(p, dict)
        and p.get("required")
        and p.get("mode") != "fixed"
        and p.get("name") not in static_params
        for p in params
    )


def _type_specific_issues(
    nodes: list, edges: list, project_type: str | None,
    available_api_connection_ids: set | None = None,
) -> List[Issue]:
    """Regole gated dal project_type (estrai/arricchisci/manipola)."""
    out: List[Issue] = []
    if project_type == "estrai":
        # SOURCE_UNSUPPORTED solo se il nodo agisce da SORGENTE (ha un edge uscente
        # che alimenta la pipeline). Un datasource_node usato come TARGET di scrittura
        # (es. CARICA su FTP/file: edge result→datasource, nessun uscente) NON va
        # flaggato (falso-positivo scoperto via E2E v2).
        _estrai_outgoing = {getattr(e, "source", None) for e in edges}
        for n in nodes:
            ntype = getattr(n, "type", None)
            scalar_project_input = (
                ntype == "project_input"
                and project_input_role(n) != "source"
            )
            if (
                ntype in _ESTRAI_UNSUPPORTED_SOURCE_TYPES
                and not scalar_project_input
                and n.id in _estrai_outgoing
            ):
                out.append(Issue(
                    node_id=str(n.id), severity="error", code="SOURCE_UNSUPPORTED",
                    message="ESTRAI supporta solo tabelle database e layer ArcGIS come sorgenti.",
                    suggestion={"node_type": n.type},
                ))
    elif project_type == "arricchisci":
        enrich_nodes = [n for n in nodes if getattr(n, "type", None) == "api_enrich"]
        if not enrich_nodes:
            out.append(Issue(node_id="", severity="error", code="ARRICCHISCI_NO_ENRICHER",
                             message="Aggiungi almeno un nodo API Enrich.", suggestion=None))
        # ARRICCHISCI_NO_SOURCES solo se MANCA una sorgente E almeno un enricher la
        # RICHIEDE (porting fedele FE: gli enricher-generatori producono dati senza
        # sorgente → non vanno bloccati). Senza questa guardia si bloccavano ~118
        # pipeline generator valide (scoperto via E2E v2).
        has_sources = any(
            getattr(n, "type", None) in _SOURCE_NODE_TYPES
            and not (
                getattr(n, "type", None) == "project_input"
                and project_input_role(n) != "source"
            )
            for n in nodes
        )
        if not has_sources and any(_arricchisci_node_needs_source(n) for n in enrich_nodes):
            out.append(Issue(node_id="", severity="error", code="ARRICCHISCI_NO_SOURCES",
                             message='Nessuna sorgente dati. Aggiungi un nodo "Progetto", "File" o "Valori Statici".',
                             suggestion=None))
        # ARRICCHISCI_NODE_ISOLATED (porting fedele FE): nodo source/api_enrich/result
        # non toccato da alcun edge → isolato. Error per api_enrich/result (rompono il
        # flusso), warning per le sorgenti (potrebbero essere in costruzione).
        connected = set()
        for e in edges:
            connected.add(getattr(e, "source", None))
            connected.add(getattr(e, "target", None))
        for n in nodes:
            ntype = getattr(n, "type", None)
            if ntype not in _SOURCE_NODE_TYPES and ntype not in ("api_enrich", "result"):
                continue
            if n.id in connected:
                continue
            data = getattr(n, "data", None) or {}
            label = data.get("label") or data.get("api_name") or ntype
            sev = "error" if ntype in ("api_enrich", "result") else "warning"
            out.append(Issue(node_id=str(n.id), severity=sev, code="ARRICCHISCI_NODE_ISOLATED",
                             message=f'Nodo "{label}" non collegato ad altri nodi.',
                             suggestion=None))
        # ARRICCHISCI_API_CONNECTION_STALE (DB-dependent): solo se la lista delle
        # connessioni esistenti è iniettata (endpoint /validate). api_id referenziato
        # ma non più esistente → connessione eliminata.
        if available_api_connection_ids is not None:
            for n in nodes:
                if getattr(n, "type", None) != "api_enrich":
                    continue
                data = _node_config(n, ("api_id", "label", "api_name"))
                api_id = data.get("api_id")
                if api_id and api_id not in available_api_connection_ids:
                    label = data.get("label") or data.get("api_name") or n.id
                    out.append(Issue(
                        node_id=str(n.id), severity="error",
                        code="ARRICCHISCI_API_CONNECTION_STALE",
                        message=f'Nodo "{label}": la connessione API è stata eliminata. Seleziona una nuova connessione.',
                        suggestion={"api_id": api_id},
                    ))
    elif project_type == "manipola":
        # RESULT_NO_SOURCE: nessuna sorgente nel percorso verso il result.
        for r in (n for n in nodes if getattr(n, "type", None) == "result"):
            reachable = _backward_reachable(r.id, edges)
            by_id = {n.id: n for n in nodes}
            if not any(getattr(by_id.get(nid), "type", None) in _SOURCE_NODE_TYPES for nid in reachable):
                out.append(Issue(node_id=str(r.id), severity="error", code="RESULT_NO_SOURCE",
                                 message="Nessuna sorgente dati collegata al percorso di output.",
                                 suggestion=None))
    return out


def _field(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _edge_join_issues(
    canvas: Any, nodes: list, edges: list, node_ids: set, project_id: str,
    project_type: str | None = None,
) -> List[Issue]:
    """Validazione join sugli edge (porting fedele dei RULE_ID FE).

    Solo regole **false-positive-free** senza conoscere il tipo di progetto:
    - JOIN_SPATIAL_NO_GEOM (error): edge ``is_spatial`` (marker esplicito) senza
      colonne geometry.
    - JOIN_COLUMN_NOT_FOUND (warning): edge con ``source_column``/``target_column``
      VALORIZZATE (= join configurato) i cui ref non risolvono contro lo schema
      inferito. Onora #A (schema indeterminate → skip).

    NON emette JOIN_NOT_CONFIGURED: un edge con colonne join vuote è
    indistinguibile (senza il project-type) tra "join non configurato" (estrai) e
    "data-flow" (manipola) → la flaggerebbe come falso positivo. È estrai-specifica
    e va gated dal tipo di progetto (deferita). Campi join = attributi top-level
    dell'Edge (il serializer FE li solleva da edge.data).
    """
    nodes_by_id = {n.id: n for n in nodes}
    join_edges = [
        e for e in edges
        if getattr(e, "source", None) in node_ids and getattr(e, "target", None) in node_ids
        and getattr(nodes_by_id.get(e.target), "type", None) not in _JOIN_SKIP_TARGET_TYPES
        # un edge che PARTE da un result è output/scrittura, mai un join
        and getattr(nodes_by_id.get(getattr(e, "source", None)), "type", None) != "result"
    ]
    if not join_edges:
        return []

    service = SchemaInferenceService(_StaticCanvasService(canvas))
    cache: Dict[str, tuple] = {}

    def _join_columns(node_id: str) -> tuple:
        """Colonne che il SQL puo' usare nel join: per una tabella TUTTE le
        colonne fisiche del nodo (anche quelle non scelte per l'uscita, come
        l'esecuzione); per gli altri nodi lo schema inferito."""
        if node_id not in cache:
            node = nodes_by_id.get(node_id)
            if getattr(node, "type", None) in _SOURCE_NODE_TYPES and getattr(node, "columns", None):
                names = {str(value) for c in node.columns
                         for value in (_field(c, "name"), _field(c, "id")) if value}
                cache[node_id] = (names, "certain")
            else:
                typed = service.infer_node_schema_typed(project_id, node_id)
                names = {str(value) for c in typed.columns if isinstance(c, dict)
                         for value in (c.get("name"), c.get("id")) if value}
                cache[node_id] = (names, typed.names_status)
        return cache[node_id]

    out: List[Issue] = []
    for e in join_edges:
        eid = getattr(e, "id", "")
        if getattr(e, "is_spatial", False):
            if not getattr(e, "source_geom_column", None) or not getattr(e, "target_geom_column", None):
                out.append(Issue(
                    node_id=str(e.target), severity="error", code="JOIN_SPATIAL_NO_GEOM",
                    message="Join spaziale non configurato: mancano le colonne geometry.",
                    suggestion={"edge_id": eid},
                ))
            continue
        sc = getattr(e, "source_column", None)
        tc = getattr(e, "target_column", None)
        if not sc or not tc:
            # In ESTRAI ogni edge inter-tabella è un join → colonne mancanti =
            # join non configurato (error). In manipola è data-flow → skip
            # (senza project-type, non flaggare: sarebbe un falso positivo).
            if project_type == "estrai":
                out.append(Issue(
                    node_id=str(e.target), severity="error", code="JOIN_NOT_CONFIGURED",
                    message="Join non configurato: mancano le colonne di collegamento.",
                    suggestion={"edge_id": eid},
                ))
            continue
        # Le colonne di collegamento sull'arco le legge SOLO il SQL di Estrai
        # (estrai/query_joins.py: alias.colonna fisica). Negli altri progetti
        # l'esecuzione non le usa: nessun controllo (erano campi residui).
        if project_type != "estrai":
            continue
        for node_id, column, side in ((e.source, sc, "sorgente"), (e.target, tc, "target")):
            names, status = _join_columns(node_id)
            if status != "indeterminate" and column not in names:
                out.append(Issue(
                    node_id=str(node_id), severity="warning", code="JOIN_COLUMN_NOT_FOUND",
                    message=f"Join: colonna '{column}' non trovata nel nodo {side}.",
                    suggestion={"column": column, "edge_id": eid},
                ))
    return out


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
        validator = validator_for(node.type)
        if isinstance(validator, ManipolaNodeValidator):
            incoming = sorted([e for e in edges if e.target == node.id], key=lambda e: incoming_edge_sort_key_for_nodes(e, {n.id: n for n in nodes}))
            typed_inputs = [service.infer_node_schema_typed(project_id, e.source) for e in incoming]
            out.extend(validator.validate_node(node, upstream=upstream, inputs=typed_inputs))
        else:
            out.extend(validator.validate_node(node, upstream=upstream))
        if node.type == "api_enrich":
            data = _node_config(node, ("api_id", "label", "api_name", "output_mapping"))
            try:
                api = schema_execution_sources.load_api_schema_config(data.get("api_id")) or {}
            except (SQLAlchemyError, PsycopgError, OSError, ValueError, RuntimeError) as exc:
                get_logger(__name__).warning(
                    "[GESTITO] Lettura API opzionale fallita per nodo %s: %s",
                    node.id, type(exc).__name__, exc_info=True,
                )
                continue
            if api:
                try:
                    current_data = _merge_node_data(node.model_dump() if hasattr(node, "model_dump") else vars(node))
                    # Stessa funzione dei controlli per riga del runtime (che
                    # produce RowError): qui la configurazione è un errore.
                    prepare_api_request(api)
                    executor = build_enrichment_executor(current_data, api)
                    validation = executor.validate_input(empty_dataframe_from_column_metadata(upstream.columns))
                    for message in validation.warnings if not validation.valid else []:
                        out.append(Issue(node_id=node.id, severity="warning" if upstream.names_status == "indeterminate" else "error",
                                         code="api.preparation.invalid", message=message))
                except (ValueError, TypeError, KeyError) as exc:
                    out.append(Issue(node_id=node.id, severity="error", code="api.preparation.invalid", message=str(exc)))
            generator = api.get("api_type") == "generator" or not inputs_by_target.get(node.id)
            mapping = (api.get("output_mapping") or data.get("output_mapping") or []) if generator else resolve_enrichment_output_mapping(data, api)
            for item in mapping:
                if "column" in item and mapping_destination(item, phase="explicit") in ("", None):
                    path = mapping_destination(item, phase="source")
                    label = data.get("label") or data.get("api_name") or node.id
                    out.append(Issue(node_id=node.id, severity="warning",
                        code="ARRICCHISCI_EMPTY_MAPPING_DESTINATION",
                        message=f'Nodo "{label}": destinazione API vuota o nulla per il path "{path}".',
                        suggestion=None))
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
        if t.names_status == "indeterminate":
            status = "indeterminate"
    types_status = "indeterminate" if any(t.types_status == "indeterminate" for t in typed) else "certain"
    order_status = "indeterminate" if any(t.order_status == "indeterminate" for t in typed) else "certain"
    combined = "indeterminate" if "indeterminate" in (status, types_status, order_status) else "certain"
    return InferredSchema(columns=merged_cols, status=combined, names_status=status,
                          types_status=types_status, order_status=order_status)


# (canvas, project_type) | None se non accessibile/inesistente.
CanvasLoader = Callable[[str], Optional[Tuple[Any, Optional[str]]]]


def validate_project_chain(
    project_id: str, load: CanvasLoader, *, _seen: Optional[set] = None,
    available_api_connection_ids: set | None = None,
    load_execution_context: Callable | None = None,
) -> ValidationReport:
    """Valida un progetto + ricorsivamente i progetti a monte (project_source).

    Validare dalla coda certifica l'intera catena (§12.3): per ogni
    ``project_source`` ricorre nel progetto sorgente e aggrega gli Issue. ``load``
    risolve ``project_id -> (canvas, project_type)`` (o ``None`` se non
    accessibile: l'authz/visibilità è responsabilità del loader del chiamante).
    Cycle-guard via ``_seen`` (catene cross-progetto cicliche → stop).

    ``available_api_connection_ids`` (group-scoped, iniettato dall'endpoint
    /validate) abilita ARRICCHISCI_API_CONNECTION_STALE su tutta la catena.
    """
    seen = _seen if _seen is not None else set()
    if project_id in seen:
        return ValidationReport()
    seen.add(project_id)

    loaded = load(project_id)
    if loaded is None:
        return ValidationReport()
    canvas, project_type = loaded

    issues = list(validate_canvas(
        canvas, project_id=project_id, project_type=project_type,
        available_api_connection_ids=available_api_connection_ids,
        execution_context=load_execution_context(project_id) if load_execution_context else None,
    ).issues)

    # Solo le sorgenti che l'esecuzione legge (regola dei rami scollegati).
    for node in (getattr(run_canvas(canvas), "nodes", None) or []):
        if getattr(node, "type", None) != "project_source":
            continue
        data = getattr(node, "data", None) or {}
        src_pid = getattr(node, "source_project_id", None) or data.get("source_project_id")
        if src_pid:
            issues.extend(validate_project_chain(
                src_pid, load, _seen=seen,
                available_api_connection_ids=available_api_connection_ids,
                load_execution_context=load_execution_context,
            ).issues)

    return ValidationReport(issues=tuple(issues))


__all__ = ["validate_canvas", "validate_project_chain", "CanvasLoader"]
