"""Contract — validate_project_chain (Epic CanvasVal §12.3): ricorsione cross-progetto.

Validare dalla coda certifica la catena (aggrega gli Issue dei progetti a monte
via project_source). Cycle-guard + upstream inaccessibile (loader None) → skip.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.projects.canvas_validation import validate_project_chain


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _clean_registry():
    from app.shared.execution import validation_protocol as vp
    vp.clear_node_validators()
    yield
    vp.clear_node_validators()


def _node(nid, ntype, source_project_id=None):
    n = MagicMock(); n.id = nid; n.type = ntype; n.data = {}
    n.source_project_id = source_project_id
    return n


def _canvas(nodes):
    c = MagicMock(); c.nodes = nodes; c.edges = []
    return c


def _loader(projects):
    return lambda pid: projects.get(pid)


def _codes(report):
    return [i.code for i in report.issues]


def test_single_project_no_upstream():
    # progetto valido-strutturale (result connesso) → nessun errore proprio
    canvas = _canvas([_node("s", "file_source"), _node("r", "result")])
    canvas.edges = [MagicMock(source="s", target="r", source_column="", target_column="",
                              is_spatial=False, source_geom_column=None, target_geom_column=None, id="e")]
    report = validate_project_chain("A", _loader({"A": (canvas, "manipola")}))
    assert "CANVAS_EMPTY" not in _codes(report)


def test_chain_aggregates_upstream_errors():
    # A ha un project_source → B; B è vuoto (CANVAS_EMPTY). Validare A trova l'errore di B.
    a_canvas = _canvas([_node("ps", "project_source", source_project_id="B"), _node("r", "result")])
    # Collaudo live L3: si segue solo una sorgente che arriva all'Output.
    a_canvas.edges = [MagicMock(source="ps", target="r", source_column="", target_column="",
                                is_spatial=False, source_geom_column=None, target_geom_column=None, id="e")]
    b_canvas = _canvas([])  # vuoto → CANVAS_EMPTY
    report = validate_project_chain("A", _loader({
        "A": (a_canvas, "manipola"), "B": (b_canvas, "manipola"),
    }))
    assert "CANVAS_EMPTY" in _codes(report)  # errore di B aggregato


def test_cycle_does_not_hang():
    # A→B→A (project_source reciproci) → cycle-guard, termina
    a = _canvas([_node("psa", "project_source", source_project_id="B")])
    b = _canvas([_node("psb", "project_source", source_project_id="A")])
    report = validate_project_chain("A", _loader({"A": (a, "manipola"), "B": (b, "manipola")}))
    assert isinstance(_codes(report), list)  # ritorna (non hang)


def test_inaccessible_upstream_skipped():
    # B non accessibile (loader None) → solo gli issue di A, niente crash
    a = _canvas([_node("ps", "project_source", source_project_id="B"), _node("r", "result")])
    report = validate_project_chain("A", _loader({"A": (a, "manipola")}))  # B assente
    assert isinstance(_codes(report), list)
