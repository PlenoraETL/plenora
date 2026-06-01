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


def _edge(source, target):
    e = MagicMock()
    e.source = source
    e.target = target
    return e


def _canvas(nodes, edges):
    c = MagicMock()
    c.nodes = nodes
    c.edges = edges
    return c


def _validate(canvas):
    from app.core.projects.canvas_validation import validate_canvas
    return validate_canvas(canvas)


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
