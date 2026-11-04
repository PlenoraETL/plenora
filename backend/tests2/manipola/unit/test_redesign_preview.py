"""Preview parziale: ramo isolato, output selezionato e contratto HTTP."""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.manipola.executor import ManipolaExecutor
from app.core.projects.canvas_preview_scope import preview_canvas
from app.core.projects.canvas_models import CanvasState

pytestmark = [pytest.mark.unit, pytest.mark.contract]


def canvas_fixture():
    def node(id, type, **kwargs):
        return {"id": id, "type": type, "position": {"x": 0, "y": 0}, **kwargs}
    return CanvasState.model_validate({
        "nodes": [node("src", "staticValues", data={"fields": [{"name": "n", "value": "1", "field_type": "integer"}]}),
                  node("t1", "transform", transform_op="formula", config={"items": [{"new_column": "x", "formula": "n * 2"}]}),
                  node("t2", "transform", transform_op="select_columns", config={"columns": [{"name": "x", "label": "X"}]}),
                  node("out", "result"), node("unrelated", "staticValues")],
        "edges": [{"id": a + b, "source": a, "target": b} for a, b in [("src", "t1"), ("t1", "t2"), ("t2", "out")]],
    })


def test_scope_does_not_mutate_canvas():
    canvas = canvas_fixture()
    assert [n.id for n in preview_canvas(canvas, "t1").nodes] == ["src", "t1"]
    assert [n.id for n in preview_canvas(canvas, "src").nodes] == ["src"]
    assert len(canvas.nodes) == 5
    with pytest.raises(ValueError, match="non trovato"):
        preview_canvas(canvas, "missing")


@pytest.mark.parametrize("node_id, expected_nodes, expected_columns", [
    ("src", ["src"], ["n"]),
    ("t1", ["src", "t1"], ["n", "x"]),
    ("t2", ["src", "t1", "t2"], ["X", "n"]),
    ("out", ["src", "t1", "t2", "out"], ["X", "n"]),
    (None, ["src", "t1", "t2", "out", "unrelated"], ["X", "n"]),
])
def test_preview_only_loads_ancestors(node_id, expected_nodes, expected_columns):
    executor = ManipolaExecutor(project_service=MagicMock())
    def load(canvas, frames, **kwargs):
        assert [n.id for n in canvas.nodes] == expected_nodes
        frames["src"] = pd.DataFrame({"n": [1, 2, 3]})
    with ExitStack() as stack:
        stack.enter_context(patch.object(executor, "_init_security_context", return_value="group"))
        for name in ("_carica_sorgenti_progetto", "_carica_sorgenti_file", "_carica_sorgenti_datasource"):
            stack.enter_context(patch.object(executor, name, return_value=None))
        stack.enter_context(patch.object(executor, "_carica_sorgenti_statiche", side_effect=load))
        stack.enter_context(patch.object(executor, "_applica_group_by", side_effect=lambda df, node: df))
        result = executor.preview("project", canvas_fixture(), 2, node_id=node_id)
    assert result["success"] is True, result
    assert [c["name"] for c in result["columns"]] == expected_columns
    assert result["total_count"] == 3
    assert result["preview_count"] == 2
    assert result["truncated"] is True
    assert len(result["rows"]) == 2


def test_runtime_type_map_propagates_new_names():
    types = {}
    from app.core.manipola.canvas_schema_runtime import build_columns_with_types
    assert build_columns_with_types(pd.DataFrame({"X": ["text"]}), types) == [{"name": "X", "type": "str"}]
    assert build_columns_with_types(pd.DataFrame({"X": [2]}), types) == [{"name": "X", "type": "int"}]


@pytest.mark.parametrize("node_id", [None, "t1", "missing"])
def test_router_validates_before_enqueue_and_preserves_async_response(monkeypatch, node_id):
    from fastapi import HTTPException
    from app.core.orchestrator import router
    from app.core.orchestrator.execution_contract import PipelineExecutionContract

    contract = PipelineExecutionContract(project_id="project", project_type="manipola", group_id="group", canvas=canvas_fixture().model_dump())
    monkeypatch.setattr(router, "_orchestrator", object())
    monkeypatch.setattr(router, "_assert_project_group", lambda *args: None)
    monkeypatch.setattr(router, "_project_service", lambda: object())
    monkeypatch.setattr(router, "build_pipeline_execution_contract", lambda **kwargs: contract)
    enqueue = MagicMock(return_value={"task_id": "task"})
    monkeypatch.setattr(router.task_service_module.task_service, "create_preview_task", enqueue)
    monkeypatch.setattr(router, "_task_info_to_dict", lambda info: info)
    kwargs = dict(project_id="project", limit=2, current_user=SimpleNamespace(user_id="user"), node_id=node_id)
    if node_id == "missing":
        with pytest.raises(HTTPException) as exc:
            router.preview_project(**kwargs)
        assert exc.value.status_code == 400
        enqueue.assert_not_called()
    else:
        assert router.preview_project(**kwargs) == {"task_id": "task", "async": True}
        # Senza node_id il contesto e' vuoto e il payload omette la chiave
        # (comportamento legacy invariato): leggerla con un default.
        context = enqueue.call_args.kwargs["execution_contract"].get("runtime_context", {})
        assert context.get("preview_node_id") == node_id
