"""Contract — wiring per-nodo di validate_canvas (Epic CanvasVal 1.6a).

Prova l'architettura del dispatch via registry (#D), SENZA i validatori veri:
- il validatore registrato viene scoperto via validator_for(node.type) e chiamato;
- riceve l'`upstream: InferredSchema` corretto (colonne + STATUS) calcolato dal
  canvas PASSATO (non da quello salvato);
- gli Issue emessi finiscono nel ValidationReport;
- nessun validatore registrato → niente per-node (fast path).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.shared.execution.validation_types import Issue


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _node(node_id, ntype, **data):
    n = MagicMock()
    n.id = node_id
    n.type = ntype
    n.data = data.get("data", {}) or {}
    n.columns = data.get("columns", []) or []
    n.calculated_columns = []
    n.source_project_id = data.get("source_project_id")
    n.file_source_id = None
    n.transform_op = data.get("transform_op")
    n.config = data.get("config")
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


class _RecordingValidator:
    def __init__(self):
        self.seen = []

    def validate_node(self, node, *, upstream):
        self.seen.append((node.id, upstream))
        return [Issue(node_id=node.id, severity="warning", code="test.seen", message="visto")]


@pytest.fixture(autouse=True)
def _clean_registry():
    from app.shared.execution import validation_protocol as vp
    vp.clear_node_validators()
    yield
    vp.clear_node_validators()


def _validate(canvas, project_id=""):
    from app.core.projects.canvas_validation import validate_canvas
    return validate_canvas(canvas, project_id=project_id)


def test_registered_validator_is_dispatched_and_issues_collected():
    from app.shared.execution import validation_protocol as vp

    rec = _RecordingValidator()
    vp.register_node_validator("transform", rec)

    fs = _node("f", "file_source", data={"columns": [
        {"name": "a", "type": "int", "visible": True},
        {"name": "b", "type": "str", "visible": True},
    ]})
    tr = _node("t", "transform", transform_op="join")
    report = _validate(_canvas([fs, tr], [_edge("f", "t")]))

    # il validatore è stato chiamato sul nodo transform
    assert [nid for nid, _ in rec.seen] == ["t"]
    # ...con l'upstream corretto (colonne del file_source, status certain)
    _, upstream = rec.seen[0]
    assert [c["name"] for c in upstream.columns] == ["a", "b"]
    assert upstream.status == "certain"
    # gli Issue emessi sono nel report
    assert any(i.code == "test.seen" and i.node_id == "t" for i in report.issues)


def test_upstream_status_indeterminate_reaches_validator():
    from app.shared.execution import validation_protocol as vp

    rec = _RecordingValidator()
    vp.register_node_validator("transform", rec)

    # project_source senza source_project_id -> indeterminate (307) -> a valle
    ps = _node("ps", "project_source", source_project_id=None)
    tr = _node("t", "transform", transform_op="join")
    _validate(_canvas([ps, tr], [_edge("ps", "t")]))

    _, upstream = rec.seen[0]
    assert upstream.status == "indeterminate"


def test_no_validator_registered_is_structural_only():
    # nessun validatore -> nessun Issue per-node (solo strutturali, qui zero)
    fs = _node("f", "file_source", data={"columns": []})
    tr = _node("t", "transform", transform_op="join")
    report = _validate(_canvas([fs, tr], [_edge("f", "t")]))
    assert all(not i.code.startswith("test.") for i in report.issues)
