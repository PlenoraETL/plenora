"""Contract — gate a esecuzione (Epic Canvas Validation §12), dietro feature-flag.

_canvas_validation_gate blocca il run (PipelineArtifact failure) se il flag è ON
e il canvas ha errori; None altrimenti. Default flag OFF → nessun cambio.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _clean_registry():
    from app.shared.execution import validation_protocol as vp
    vp.clear_node_validators()
    yield
    vp.clear_node_validators()


def _node(node_id, ntype):
    n = MagicMock(); n.id = node_id; n.type = ntype; n.data = {}
    return n


def _edge(source, target):
    e = MagicMock(); e.source = source; e.target = target
    e.id = f"{source}->{target}"
    e.source_column = ""; e.target_column = ""; e.is_spatial = False
    e.source_geom_column = None; e.target_geom_column = None
    return e


def _canvas(nodes, edges=None):
    c = MagicMock(); c.nodes = nodes; c.edges = edges or []
    return c


def _gate(project_type, canvas, *, flag, monkeypatch):
    from app.core.orchestrator import service as svc
    monkeypatch.setattr(svc.settings, "get", lambda k, d=None: flag if k == "CANVAS_VALIDATION_GATE_ENABLED" else d)
    load = lambda pid: (canvas, project_type) if pid == "p" else None
    return svc._canvas_validation_gate("p", load)


def test_gate_off_never_blocks(monkeypatch):
    # canvas vuoto (sarebbe invalido) ma flag OFF → nessun blocco
    assert _gate("manipola", _canvas([]), flag=False, monkeypatch=monkeypatch) is None


def test_gate_on_blocks_invalid_canvas(monkeypatch):
    result = _gate("manipola", _canvas([]), flag=True, monkeypatch=monkeypatch)  # empty → CANVAS_EMPTY
    assert result is not None
    assert result["success"] is False


def test_gate_on_passes_valid_canvas(monkeypatch):
    canvas = _canvas([_node("s", "file_source"), _node("r", "result")], [_edge("s", "r")])
    assert _gate("manipola", canvas, flag=True, monkeypatch=monkeypatch) is None
