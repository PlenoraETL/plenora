"""Characterization — contratto PUBBLICO di `SchemaInferenceService`
(`infer_node_schema` / `infer_all_schemas`) PRIMA del wrapping `InferredSchema`
(Epic Canvas Validation, Fase 0.1a).

Pinna il comportamento osservabile dei 2 metodi pubblici — che ritornano
`SchemaRows` (liste di dict via `ColumnSchema.to_dict()`) — inclusi i soft-fail
`return []` 134 (canvas assente), 142 (nodo assente) e 260 (result senza input).
Quando 0.1a introdurrà `infer_node_schema_typed -> InferredSchema`, il pubblico
`infer_node_schema` deve restare IDENTICO (`.columns` == questa lista): questi
test sono la rete che lo garantisce.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _node(node_id: str, ntype: str, **data):
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


def _edge(source: str, target: str):
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

    project_service = MagicMock()
    project_service.get_project_canvas.return_value = canvas
    return SchemaInferenceService(project_service=project_service)


def _chain_canvas():
    """file_source(id,lat) -> api_enrich(+risk) -> result."""
    file_node = _node(
        "f1", "file_source",
        data={"columns": [
            {"name": "id", "type": "int", "visible": True},
            {"name": "lat", "type": "float", "visible": True},
        ]},
    )
    api_node = _node(
        "a1", "api_enrich",
        data={"output_mapping": [{"column": "risk", "type": "str"}]},
    )
    result_node = _node("r1", "result", data={})
    return _canvas(
        [file_node, api_node, result_node],
        [_edge("f1", "a1"), _edge("a1", "r1")],
    )


# --- happy path: il pubblico ritorna liste di dict con i nomi giusti ---


def test_infer_node_schema_returns_dict_rows_for_result_node():
    svc = _svc_with_canvas(_chain_canvas())
    rows = svc.infer_node_schema("proj", "r1")
    assert isinstance(rows, list)
    assert all(isinstance(r, dict) for r in rows)
    assert [r["name"] for r in rows] == ["id", "lat", "risk"]


def test_infer_node_schema_returns_source_columns_for_leaf():
    svc = _svc_with_canvas(_chain_canvas())
    rows = svc.infer_node_schema("proj", "f1")
    assert [r["name"] for r in rows] == ["id", "lat"]


def test_infer_all_schemas_maps_every_node_to_dict_rows():
    svc = _svc_with_canvas(_chain_canvas())
    schemas = svc.infer_all_schemas("proj")
    assert set(schemas.keys()) == {"f1", "a1", "r1"}
    assert [r["name"] for r in schemas["a1"]] == ["id", "lat", "risk"]
    assert [r["name"] for r in schemas["r1"]] == ["id", "lat", "risk"]


# --- soft-fail return [] (i punti che 0.1b classificherà) ---


def test_infer_node_schema_empty_when_canvas_absent():
    # riga 134: get_project_canvas -> None
    svc = _svc_with_canvas(None)
    assert svc.infer_node_schema("proj", "any") == []


def test_infer_all_schemas_empty_when_canvas_absent():
    svc = _svc_with_canvas(None)
    assert svc.infer_all_schemas("proj") == {}


def test_infer_node_schema_empty_when_node_missing():
    # riga 142: node_id non nel canvas
    svc = _svc_with_canvas(_chain_canvas())
    assert svc.infer_node_schema("proj", "does-not-exist") == []


def test_infer_node_schema_empty_for_result_without_input():
    # riga 260: result senza edge entranti -> input_schemas vuoto -> []
    result_node = _node("r1", "result", data={})
    svc = _svc_with_canvas(_canvas([result_node], []))
    assert svc.infer_node_schema("proj", "r1") == []


# --- infer_node_schema_typed (0.1a): status coarse, columns == public ---


def test_typed_certain_with_columns_matches_public_rows():
    svc = _svc_with_canvas(_chain_canvas())
    typed = svc.infer_node_schema_typed("proj", "r1")
    assert typed.status == "certain"
    assert typed.reason == ""
    assert typed.columns == svc.infer_node_schema("proj", "r1")
    assert [c["name"] for c in typed.columns] == ["id", "lat", "risk"]


def test_typed_indeterminate_for_result_without_input():
    # result vuoto -> tipo che DOVREBBE avere colonne -> indeterminate
    result_node = _node("r1", "result", data={})
    svc = _svc_with_canvas(_canvas([result_node], []))
    typed = svc.infer_node_schema_typed("proj", "r1")
    assert typed.columns == []
    assert typed.status == "indeterminate"
    assert "result" in typed.reason


def test_typed_certain_for_empty_declared_file_source():
    # file_source dichiarato senza colonne = vuoto CERTO, non indeterminate
    file_node = _node("f1", "file_source", data={"columns": []})
    svc = _svc_with_canvas(_canvas([file_node], []))
    typed = svc.infer_node_schema_typed("proj", "f1")
    assert typed.columns == []
    assert typed.status == "certain"


def test_typed_certain_when_node_missing():
    # nodo inesistente: nessun tipo -> non classificato indeterminate
    svc = _svc_with_canvas(_chain_canvas())
    typed = svc.infer_node_schema_typed("proj", "nope")
    assert typed.columns == []
    assert typed.status == "certain"
