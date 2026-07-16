"""Contract — validate_canvas, check strutturali canvas-intrinseci (CanvasVal 1.5).

Cicli (kahn on_cycle='raise'), edge danglanti, nodi orfani → ValidationReport
tipizzato. Niente DB, niente import dei motori (i validate_node arrivano in 1.6).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _no_node_validators():
    # Strutturale-only deterministico: nessun validate_node registrato.
    from app.shared.execution import validation_protocol as vp
    vp.clear_node_validators()
    yield
    vp.clear_node_validators()


def _node(node_id, ntype):
    n = MagicMock()
    n.id = node_id
    n.type = ntype
    n.data = {}
    return n


def _edge(source, target, **join):
    e = MagicMock()
    e.source = source
    e.target = target
    # default del vero Edge backend (campi join falsy → non-join/data-flow)
    e.id = join.get("id", f"{source}->{target}")
    e.source_column = join.get("source_column", "")
    e.target_column = join.get("target_column", "")
    e.is_spatial = join.get("is_spatial", False)
    e.source_geom_column = join.get("source_geom_column", None)
    e.target_geom_column = join.get("target_geom_column", None)
    return e


def _canvas(nodes, edges):
    c = MagicMock()
    c.nodes = nodes
    c.edges = edges
    return c


def _validate(canvas, project_type=None):
    from app.core.projects.canvas_validation import validate_canvas
    return validate_canvas(canvas, project_type=project_type)


def _codes(report):
    return sorted(i.code for i in report.issues)


def test_empty_canvas_is_error():
    report = _validate(_canvas([], []))
    assert _codes(report) == ["CANVAS_EMPTY"]
    assert report.has_errors is True


def test_valid_dag_has_no_issues():
    # file_source -> transform -> result (lineare, niente cicli/orfani)
    nodes = [_node("s", "file_source"), _node("t", "transform"), _node("r", "result")]
    edges = [_edge("s", "t"), _edge("t", "r")]
    report = _validate(_canvas(nodes, edges))
    assert report.issues == ()


def test_cycle_is_error():
    nodes = [_node("a", "transform"), _node("b", "transform")]
    edges = [_edge("a", "b"), _edge("b", "a")]
    report = _validate(_canvas(nodes, edges))
    assert "lineage.cycle" in _codes(report)
    assert report.has_errors is True


def test_dangling_edge_is_error():
    nodes = [_node("s", "file_source")]
    edges = [_edge("s", "ghost")]  # 'ghost' non esiste
    report = _validate(_canvas(nodes, edges))
    assert "structure.dangling_edge" in _codes(report)
    assert report.has_errors is True


def test_orphan_processing_node_is_error():
    # transform senza edge entrante = orfano (richiede input)
    nodes = [_node("s", "file_source"), _node("t", "transform")]
    edges = []  # nessun edge -> 't' orfano
    report = _validate(_canvas(nodes, edges))
    assert "structure.orphan" in _codes(report)


def test_source_node_alone_is_not_orphan():
    # un source-like senza incoming non è orfano (è una sorgente)
    report = _validate(_canvas([_node("s", "file_source")], []))
    assert "structure.orphan" not in _codes(report)


@pytest.mark.parametrize("input_kind", ["file", "text", "number", "boolean"])
def test_project_input_without_incoming_data_edge_is_not_orphan(input_kind):
    node = _node("runtime-input", "project_input")
    node.input_kind = input_kind
    node.bindings = (
        []
        if input_kind == "file"
        else [{"target_node_id": "filter-1", "target_field": "config.value"}]
    )

    report = _validate(_canvas([node], []), project_type="manipola")

    assert "structure.orphan" not in _codes(report)


def test_result_node_missing_is_error():
    # canvas non vuoto ma senza nodo result
    report = _validate(_canvas([_node("s", "file_source")], []))
    assert "RESULT_NODE_MISSING" in _codes(report)


def test_result_node_duplicated_is_error():
    nodes = [_node("s", "file_source"), _node("r1", "result"), _node("r2", "result")]
    report = _validate(_canvas(nodes, [_edge("s", "r1"), _edge("s", "r2")]))
    assert "RESULT_NODE_DUPLICATED" in _codes(report)


def test_carica_duplicated_is_error():
    nodes = [_node("r", "result"), _node("c1", "carica"), _node("c2", "carica")]
    report = _validate(_canvas(nodes, [_edge("r", "c1"), _edge("r", "c2")]))
    assert "CARICA_DUPLICATED" in _codes(report)


def test_result_not_connected_is_error():
    nodes = [_node("s", "file_source"), _node("r", "result")]
    report = _validate(_canvas(nodes, []))  # result senza edge entrante
    assert "RESULT_NOT_CONNECTED" in _codes(report)


# --- join sugli edge (false-positive-free: solo join configurati / spatial) ---


def test_dataflow_edge_not_flagged_as_join():
    # edge senza colonne join (data-flow manipola) → NESSUN flag join (no falso positivo)
    nodes = [_node("s", "file_source"), _node("t", "transform"), _node("r", "result")]
    edges = [_edge("s", "t"), _edge("t", "r")]
    report = _validate(_canvas(nodes, edges))
    assert "JOIN_NOT_CONFIGURED" not in _codes(report)
    assert "JOIN_COLUMN_NOT_FOUND" not in _codes(report)


def test_join_spatial_no_geom_is_error():
    nodes = [_node("a", "file_source"), _node("b", "file_source"), _node("r", "result")]
    edges = [_edge("a", "b", is_spatial=True), _edge("b", "r")]  # spatial senza geom
    report = _validate(_canvas(nodes, edges))
    assert "JOIN_SPATIAL_NO_GEOM" in _codes(report)


def test_join_column_not_found_is_warning():
    s = _node("s", "file_source")
    s.data = {"columns": [{"name": "a", "visible": True}]}
    t = _node("t", "transform")
    r = _node("r", "result")
    edges = [_edge("s", "t", source_column="ghost", target_column="a"), _edge("t", "r")]
    report = _validate(_canvas([s, t, r], edges))
    codes = [(i.code, i.severity) for i in report.issues]
    assert ("JOIN_COLUMN_NOT_FOUND", "warning") in codes


# --- regole estrai-specific (gated dal project_type) ---


def test_join_not_configured_only_for_estrai():
    nodes = [_node("a", "source"), _node("b", "source"), _node("r", "result")]
    edges = [_edge("a", "b"), _edge("b", "r")]  # a→b edge senza colonne join
    # estrai: edge inter-tabella non configurato = errore
    assert "JOIN_NOT_CONFIGURED" in _codes(_validate(_canvas(nodes, edges), project_type="estrai"))
    # manipola / nessun tipo: data-flow → NON flaggato (no falso positivo)
    assert "JOIN_NOT_CONFIGURED" not in _codes(_validate(_canvas(nodes, edges), project_type="manipola"))
    assert "JOIN_NOT_CONFIGURED" not in _codes(_validate(_canvas(nodes, edges)))


def test_source_unsupported_only_for_estrai():
    nodes = [_node("s", "staticValues"), _node("r", "result")]
    edges = [_edge("s", "r")]
    assert "SOURCE_UNSUPPORTED" in _codes(_validate(_canvas(nodes, edges), project_type="estrai"))
    assert "SOURCE_UNSUPPORTED" not in _codes(_validate(_canvas(nodes, edges), project_type="manipola"))


# --- sorgenti + result-geo (universali, no inferenza) ---


def test_source_isolated_is_warning():
    s = _node("s", "file_source")  # nessun edge
    report = _validate(_canvas([s, _node("r", "result")], []))
    assert "SOURCE_ISOLATED" in _codes(report)


def test_source_no_visible_columns_is_error():
    s = _node("s", "file_source")
    s.data = {"columns": [{"name": "a", "visible": False}]}  # ha colonne ma tutte nascoste
    report = _validate(_canvas([s, _node("r", "result")], [_edge("s", "r")]))
    assert "SOURCE_NO_VISIBLE_COLUMNS" in _codes(report)


def test_result_geo_no_geometry_is_error():
    s = _node("s", "file_source")
    s.data = {"columns": [{"name": "a", "type": "str", "visible": True}]}
    r = _node("r", "result"); r.data = {"format": "geojson"}
    report = _validate(_canvas([s, r], [_edge("s", "r")]))
    assert "RESULT_GEO_NO_GEOMETRY" in _codes(report)


def test_result_geo_with_geometry_ok():
    s = _node("s", "file_source")
    s.data = {"columns": [{"name": "geom", "type": "geometry:point", "visible": True}]}
    r = _node("r", "result"); r.data = {"format": "geojson"}
    report = _validate(_canvas([s, r], [_edge("s", "r")]))
    assert "RESULT_GEO_NO_GEOMETRY" not in _codes(report)


def test_arricchisci_no_enricher():
    codes = _codes(_validate(_canvas([_node("r", "result")], []), project_type="arricchisci"))
    assert "ARRICCHISCI_NO_ENRICHER" in codes


def test_arricchisci_no_sources_only_when_enricher_needs_one():
    # enricher con param required non-fixed → richiede sorgente → NO_SOURCES scatta
    e = _node("e", "api_enrich")
    e.data = {"parameters": [{"name": "addr", "required": True, "mode": "mapped"}]}
    codes = _codes(_validate(_canvas([e, _node("r", "result")], [_edge("e", "r")]), project_type="arricchisci"))
    assert "ARRICCHISCI_NO_SOURCES" in codes


def test_arricchisci_generator_enricher_no_false_no_sources():
    # enricher GENERATORE (nessun param required) → non richiede sorgente →
    # ARRICCHISCI_NO_SOURCES NON deve scattare (parità FE, fix E2E v2)
    e = _node("e", "api_enrich")
    e.data = {"parameters": [{"name": "opt", "required": False, "mode": "mapped"}]}
    codes = _codes(_validate(_canvas([e, _node("r", "result")], [_edge("e", "r")]), project_type="arricchisci"))
    assert "ARRICCHISCI_NO_SOURCES" not in codes


def test_arricchisci_complete_no_structural_error():
    nodes = [_node("s", "file_source"), _node("e", "api_enrich"), _node("r", "result")]
    edges = [_edge("s", "e"), _edge("e", "r")]
    codes = _codes(_validate(_canvas(nodes, edges), project_type="arricchisci"))
    assert "ARRICCHISCI_NO_ENRICHER" not in codes
    assert "ARRICCHISCI_NO_SOURCES" not in codes
    assert "ARRICCHISCI_NODE_ISOLATED" not in codes  # tutto collegato


def test_arricchisci_node_isolated_error_for_enricher_warning_for_source():
    # 'e' (api_enrich) e 'iso' (source) non toccati da alcun edge → isolati.
    # api_enrich/result = error (rompono il flusso); source = warning.
    nodes = [_node("s", "file_source"), _node("e", "api_enrich"),
             _node("r", "result"), _node("iso", "file_source")]
    edges = [_edge("s", "r")]
    report = _validate(_canvas(nodes, edges), project_type="arricchisci")
    iso = {i.node_id: i for i in report.issues if i.code == "ARRICCHISCI_NODE_ISOLATED"}
    assert iso["e"].severity == "error"
    assert iso["iso"].severity == "warning"
    assert "s" not in iso and "r" not in iso  # collegati → non isolati


def test_arricchisci_node_isolated_only_for_arricchisci():
    nodes = [_node("s", "file_source"), _node("e", "api_enrich"), _node("r", "result")]
    edges = [_edge("s", "r")]  # 'e' isolato
    assert "ARRICCHISCI_NODE_ISOLATED" not in _codes(_validate(_canvas(nodes, edges), project_type="manipola"))


def _arricchisci_canvas_with_api(api_id):
    s = _node("s", "file_source"); e = _node("e", "api_enrich"); r = _node("r", "result")
    e.data = {"api_id": api_id}
    return _canvas([s, e, r], [_edge("s", "e"), _edge("e", "r")])


def test_api_connection_stale_when_id_not_in_available():
    from app.core.projects.canvas_validation import validate_canvas
    report = validate_canvas(
        _arricchisci_canvas_with_api("gone"), project_type="arricchisci",
        available_api_connection_ids={"exists"},
    )
    assert "ARRICCHISCI_API_CONNECTION_STALE" in [i.code for i in report.issues]


def test_api_connection_not_stale_when_id_present():
    from app.core.projects.canvas_validation import validate_canvas
    report = validate_canvas(
        _arricchisci_canvas_with_api("good"), project_type="arricchisci",
        available_api_connection_ids={"good"},
    )
    assert "ARRICCHISCI_API_CONNECTION_STALE" not in [i.code for i in report.issues]


def test_api_connection_stale_skipped_when_ids_not_injected():
    # default None (path gate orizzontale) → STALE saltato (resta disaccoppiato dal DB)
    from app.core.projects.canvas_validation import validate_canvas
    report = validate_canvas(_arricchisci_canvas_with_api("gone"), project_type="arricchisci")
    assert "ARRICCHISCI_API_CONNECTION_STALE" not in [i.code for i in report.issues]


def test_column_name_conflict_is_warning():
    s1 = _node("s1", "file_source"); s1.data = {"columns": [{"name": "id", "visible": True}]}
    s2 = _node("s2", "file_source"); s2.data = {"columns": [{"name": "id", "visible": True}]}
    r = _node("r", "result")
    report = _validate(_canvas([s1, s2, r], [_edge("s1", "r"), _edge("s2", "r")]))
    codes = [(i.code, i.severity) for i in report.issues]
    assert ("COLUMN_NAME_CONFLICT", "warning") in codes


def test_no_column_conflict_distinct_names():
    s1 = _node("s1", "file_source"); s1.data = {"columns": [{"name": "a", "visible": True}]}
    s2 = _node("s2", "file_source"); s2.data = {"columns": [{"name": "b", "visible": True}]}
    r = _node("r", "result")
    report = _validate(_canvas([s1, s2, r], [_edge("s1", "r"), _edge("s2", "r")]))
    assert "COLUMN_NAME_CONFLICT" not in _codes(report)


def test_result_no_source_manipola():
    nodes = [_node("t", "transform"), _node("r", "result")]
    edges = [_edge("t", "r")]  # nessun source nel percorso
    assert "RESULT_NO_SOURCE" in _codes(_validate(_canvas(nodes, edges), project_type="manipola"))


def test_result_with_source_manipola_ok():
    nodes = [_node("s", "file_source"), _node("t", "transform"), _node("r", "result")]
    edges = [_edge("s", "t"), _edge("t", "r")]
    assert "RESULT_NO_SOURCE" not in _codes(_validate(_canvas(nodes, edges), project_type="manipola"))
