"""Contract — status PRECISO di `infer_node_schema_typed` (Epic CanvasVal 0.1b).

I 4 `return []` critici diventano `indeterminate`+reason esplicito; lo status si
propaga a valle (un nodo vuoto perché l'upstream è indeterminate è esso stesso
indeterminate → "mai falso blocco"). Gli edge (134/142/380) restano `certain`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _node(node_id, ntype, **data):
    n = MagicMock()
    n.id = node_id
    n.type = ntype
    n.data = data.get("data", {}) or {}
    n.columns = data.get("columns", []) or []
    n.calculated_columns = data.get("calculated_columns", []) or []
    n.source_project_id = data.get("source_project_id")
    n.file_source_id = data.get("file_source_id")
    n.transform_op = data.get("transform_op")
    n.config = data.get("config")
    return n


def _edge(source, target):
    e = MagicMock()
    e.source = source
    e.target = target
    e.source_column = None
    return e


def _canvas(nodes, edges):
    c = MagicMock()
    c.nodes = nodes
    c.edges = edges
    return c


def _svc_with_canvas(canvas):
    from app.core.projects.schema_inference import SchemaInferenceService

    ps = MagicMock()
    ps.get_project_canvas.return_value = canvas
    return SchemaInferenceService(project_service=ps)


# --- 307: project_source senza source_project_id ---


def test_project_source_without_source_id_is_indeterminate():
    ps = _node("ps1", "project_source", source_project_id=None)
    svc = _svc_with_canvas(_canvas([ps], []))
    typed = svc.infer_node_schema_typed("proj", "ps1")
    assert typed.columns == []
    assert typed.status == "indeterminate"
    assert "source_project_id" in typed.reason


# --- 312: metadata progetto sorgente assente ---


def test_project_source_without_metadata_is_indeterminate(monkeypatch):
    monkeypatch.setattr(
        "app.core.projects.schema_inference.load_project_source_schema_context",
        lambda pid: None,
    )
    ps = _node("ps1", "project_source", source_project_id="src-1")
    svc = _svc_with_canvas(_canvas([ps], []))
    typed = svc.infer_node_schema_typed("proj", "ps1")
    assert typed.status == "indeterminate"
    assert typed.reason  # reason non vuota


# --- 321: output_contract sorgente assente (non ancora eseguito) ---


def test_project_source_without_output_contract_is_indeterminate(monkeypatch):
    from app.core.projects.project_context_loader import ProjectSourceSchemaContext

    monkeypatch.setattr(
        "app.core.projects.schema_inference.load_project_source_schema_context",
        lambda pid: ProjectSourceSchemaContext(project_id=pid, output_contract=None),
    )
    ps = _node("ps1", "project_source", source_project_id="src-1")
    svc = _svc_with_canvas(_canvas([ps], []))
    typed = svc.infer_node_schema_typed("proj", "ps1")
    assert typed.status == "indeterminate"
    assert typed.reason


# --- 260: result senza input risolvibile ---


def test_result_without_input_is_indeterminate():
    r = _node("r1", "result", data={})
    svc = _svc_with_canvas(_canvas([r], []))
    typed = svc.infer_node_schema_typed("proj", "r1")
    assert typed.status == "indeterminate"
    assert typed.reason


# --- PROPAGAZIONE: downstream di indeterminate è indeterminate (no falso blocco) ---


def test_indeterminate_propagates_downstream():
    # project_source irrisolvibile (307) -> result. Il result è vuoto PERCHÉ
    # l'upstream è indeterminate → deve ereditare indeterminate, NON "certain empty"
    # (che causerebbe un falso blocco sui ref a valle).
    ps = _node("ps1", "project_source", source_project_id=None)
    r = _node("r1", "result", data={})
    svc = _svc_with_canvas(_canvas([ps, r], [_edge("ps1", "r1")]))
    typed = svc.infer_node_schema_typed("proj", "r1")
    assert typed.columns == []
    assert typed.status == "indeterminate"


def test_certain_source_downstream_stays_certain():
    # file_source con colonne -> result: certain, colonne propagate
    f = _node("f1", "file_source", data={"columns": [
        {"name": "id", "type": "int", "visible": True},
    ]})
    r = _node("r1", "result", data={})
    svc = _svc_with_canvas(_canvas([f, r], [_edge("f1", "r1")]))
    typed = svc.infer_node_schema_typed("proj", "r1")
    assert [c["name"] for c in typed.columns] == ["id"]
    assert typed.status == "certain"
