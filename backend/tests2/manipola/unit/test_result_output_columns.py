"""Contratti della selezione finale, condivisa con inferenza e persistenza."""
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app.core.dataframes.output_columns import apply_output_columns
from app.shared.execution.column_selection import select_column_plan

pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.mark.parametrize("columns", [None, []])
def test_legacy_passthrough_preserves_duplicate_columns(columns):
    df = pd.DataFrame([[1, 2]], columns=["a", "a"])
    assert apply_output_columns(df, columns) is df


def test_result_selection_skips_stale_columns_and_preserves_input(caplog):
    df = pd.DataFrame({"a": [1], "b": [2], "c": [3], "new": [4]})
    columns = [{"name": "missing"}, {"name": "c", "label": "C"}, {"name": "a", "keep": False}]
    out = apply_output_columns(df, columns)
    assert out.to_dict("list") == {"C": [3], "b": [2], "new": [4]}
    assert list(df) == ["a", "b", "c", "new"]
    assert "missing" in caplog.text
    with pytest.raises(ValueError):
        select_column_plan(list(df), columns)


def test_result_schema_and_canvas_roundtrip():
    from app.core.projects.canvas_models import CanvasState
    from app.core.projects.schema_inference import SchemaInferenceService, _NodeSchemaContext
    from app.shared.execution.schema_types import ColumnSchema
    columns = [{"name": "b", "label": "B"}, {"name": "gone"}, {"name": "a", "keep": False}]
    canvas = CanvasState.model_validate({"nodes": [{"id": "r", "type": "result", "position": {"x": 0, "y": 0}, "data": {"output_columns": columns}}], "edges": []})
    loaded = CanvasState.model_validate_json(canvas.model_dump_json())
    assert loaded.nodes[0].data["output_columns"] == columns
    svc = SchemaInferenceService(None)
    svc._get_input_schemas = Mock(return_value=[[ColumnSchema("a", "int"), ColumnSchema("b", "str", id="stable"), ColumnSchema("new", "real")]])
    schema = svc._infer_result_node_schema(_NodeSchemaContext(loaded.nodes[0], {}, {}, "p"))
    assert [(c.name, c.type) for c in schema] == [("B", "str"), ("new", "real")]
    assert schema[0].id == "stable"


def test_manipola_selects_before_group_by_and_save(monkeypatch):
    from app.core.manipola.executor import ManipolaExecutor
    from app.core.projects.canvas_models import CanvasState
    executor = ManipolaExecutor(project_service=SimpleNamespace())
    monkeypatch.setattr(executor, "_init_security_context", lambda *a, **kw: "g")
    for method in ("_carica_sorgenti_progetto", "_carica_sorgenti_file", "_carica_sorgenti_datasource", "_carica_sorgenti_statiche"):
        monkeypatch.setattr(executor, method, lambda *a, **kw: None)
    monkeypatch.setattr(executor, "_applica_trasformazioni", lambda *a, **kw: pd.DataFrame({"a": [1, 1], "b": [2, 3]}))
    saved = []
    def save(project_id, node, df, *args, **kwargs):
        saved.append(df)
        return {"success": True, "rows_count": len(df)}
    monkeypatch.setattr(executor, "_salva_risultato", save)
    canvas = CanvasState.model_validate({"nodes": [{"id": "r", "type": "result", "position": {"x": 0, "y": 0}, "data": {
        "output_columns": [{"name": "a", "label": "key"}, {"name": "b", "label": "value"}],
        "group_by_enabled": True, "group_by_columns": ["key"],
        "aggregations": [{"column": "value", "function": "sum", "alias": "total"}],
    }}], "edges": []})
    node = canvas.nodes[0]
    node.group_by_enabled = True
    node.group_by_columns = ["key"]
    assert executor.execute(project_id="p", canvas=canvas)["success"]
    assert saved[0].to_dict("list") == {"key": [1], "total": [5]}


@pytest.mark.parametrize("id_key", ["id", "column_id"])
@pytest.mark.parametrize("keep", [True, False])
def test_result_selection_resolves_input_ids_after_rename(id_key, keep):
    from app.core.projects.canvas_models import Node
    from app.core.projects.schema_inference import SchemaInferenceService, _NodeSchemaContext
    from app.shared.execution.schema_types import ColumnSchema
    columns = [{"name": "a", "name_column_id": "id1", "keep": keep}]
    inputs = [{id_key: "id1", "name": "b"}]
    df = pd.DataFrame({"b": [1], "c": [2]})
    expected = ["b", "c"] if keep else ["c"]
    assert list(apply_output_columns(df, columns, inputs)) == expected
    n = Node.model_validate({"id": "r", "type": "result", "position": {"x": 0, "y": 0}, "output_columns": columns})
    svc = SchemaInferenceService(None)
    svc._get_input_schemas = Mock(return_value=[[ColumnSchema("b", "int", id="id1"), ColumnSchema("c", "int")]])
    assert [col.name for col in svc._infer_result_node_schema(_NodeSchemaContext(n, {}, {}, "p"))] == expected
    assert columns[0]["name"] == "a"
