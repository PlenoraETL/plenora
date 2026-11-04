"""Single correction pass: shared runtime/schema/registry contracts."""
from types import SimpleNamespace
from unittest.mock import Mock
import pandas as pd
import pytest
from app.core.projects.canvas_models import Node, CanvasState
from app.core.projects.schema_inference import SchemaInferenceService, _NodeSchemaContext
from app.shared.execution.schema_types import ColumnSchema, InferredSchema
from app.core.manipola.plugin_executor import PluginExecutor
from app.core.manipola.transforms.columns import op_select_columns
from app.core.dataframes.output_columns import apply_output_columns

pytestmark = [pytest.mark.unit, pytest.mark.contract]


def node(**kwargs):
    return Node.model_validate({"id": "n", "type": "result", "position": {"x": 0, "y": 0}, **kwargs})


@pytest.mark.parametrize("apply", [op_select_columns, apply_output_columns])
@pytest.mark.parametrize("columns", [
    [None], ["a"], [{}], [{"name": ""}], [{"name": "a", "keep": 1}],
    [{"name": "a"}, {"name": "a"}],
    [{"name": "a", "label": " "}], [{"name": "a", "label": 0}],
    [{"name": "a", "label": "b"}],
])
def test_malformed_selection_enumeration(apply, columns):
    with pytest.raises(ValueError):
        apply(pd.DataFrame({"a": [1], "b": [2]}), columns)


@pytest.mark.parametrize("apply", [op_select_columns, apply_output_columns])
def test_missing_selection_names_warn(apply, caplog):
    assert list(apply(pd.DataFrame({"a": [1]}), [{"name": "gone"}])) == ["a"]
    assert "gone" in caplog.text


def test_presence_roundtrip_all_consumers():
    from app.core.manipola.executor import _nf
    from app.core.projects.schema_inference import _node_config
    from app.shared.execution.canvas_node import CanvasNode
    n = node(output_columns=[], data={"output_columns": [{"name": "a", "keep": False}]})
    assert "output_columns" in Node.model_fields
    n = Node.model_validate_json(n.model_dump_json())
    assert _nf(n, "output_columns") == []
    assert _node_config(n, ("output_columns",))["output_columns"] == []
    assert CanvasNode(n.model_dump()).get_field("output_columns") == []


def test_real_model_grouping_after_selection_matches_schema():
    from app.core.manipola.executor import ManipolaExecutor
    from app.core.projects.canvas_models import AggregationConfig
    n = node(output_columns=[{"name": "a", "label": "key"}, {"name": "b", "label": "value"}],
             group_by_enabled=True, group_by_columns=["key"],
             aggregations=[{"column": "value", "function": "AVG"}, {"column": "*", "function": "COUNT", "alias": "rows"}])
    assert isinstance(n.aggregations[0], AggregationConfig)
    df = pd.DataFrame({"a": [1, 1], "b": [2, 4]})
    out = ManipolaExecutor(SimpleNamespace())._applica_group_by(apply_output_columns(df, n.output_columns), n)
    assert out.to_dict("list") == {"key": [1], "value_mean": [3.0], "rows": [2]}
    svc = SchemaInferenceService(None)
    svc._get_input_schemas = Mock(return_value=[[ColumnSchema("a", "int"), ColumnSchema("b", "int")]])
    schema = svc._infer_result_node_schema(_NodeSchemaContext(n, {}, {}, "p"))
    assert [c.name for c in schema] == list(out)
    assert [c.type for c in schema] == ["int", "real", "int"]


@pytest.mark.parametrize("bad", [
    {"type": "result", "output_columns": [{"name": "a", "keep": "no"}]},
    {"type": "transform", "transform_op": "lookup", "config": {"column": "a", "mapping": {}, "unmatched": "invalid"}},
    {"type": "transform", "transform_op": "join", "config": {"left_keys": ["a"], "right_keys": ["a"], "left_columns": ["absent"]}},
])
def test_schema_errors_are_node_local_passthrough(bad, caplog, monkeypatch):
    source = node(id="source", type="file_source", data={"columns": [{"name": "a", "type": "int"}]})
    broken = node(**bad)
    other = node(id="other")
    canvas = CanvasState(nodes=[source, broken, other], edges=[
        {"id": "e1", "source": "source", "target": "n"},
        {"id": "e2", "source": "source", "target": "n"},
        {"id": "e3", "source": "source", "target": "other"},
    ])
    svc = SchemaInferenceService(SimpleNamespace(get_project_canvas=lambda _: canvas))
    result = svc.infer_all_schemas("p")
    assert [c["name"] for c in result["n"]] == ["a"]
    assert result["other"] == result["source"]
    # Lo stato appartiene alla singola chiamata, non al servizio condiviso.
    assert svc.infer_node_schema_typed("p", "n").status == "indeterminate"
    assert svc.infer_node_schema_typed("p", "other").status == "certain"
    assert "Schema issue for node n" in caplog.text
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core.projects import router as routes
    monkeypatch.setattr(routes, "_schema_service", svc)
    monkeypatch.setattr(routes, "_project_service", SimpleNamespace(get_project_metadata=lambda _: {}))
    monkeypatch.setattr(routes, "assert_group_access", lambda *args: None)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_not_esecutore] = lambda: {}
    response = TestClient(app).get("/projects/p/schemas")
    assert response.status_code == 200, response.text
    assert response.json()["schemas"]["n"] == result["n"]


def test_registry_type_enumeration_converter_and_validator():
    from app.shared.execution.registry_types import KNOWN_PARAM_TYPES, require_known_param_type
    from app.core.manipola.node_validator import ManipolaNodeValidator
    executor = PluginExecutor()
    actual = {p["type"] for t in executor.registry.values() for p in t.get("parameters", [])}
    assert actual == KNOWN_PARAM_TYPES
    for typ in actual:
        require_known_param_type(typ)
        executor._converti_valore(None, typ)
    for op, definition in executor.registry.items():
        ManipolaNodeValidator().validate_node(node(type="transform", transform_op=op), upstream=InferredSchema(columns=[]))
    with pytest.raises(ValueError):
        executor._converti_valore("x", "unknown_type")


@pytest.mark.parametrize("mapping", [None, [], {}])
@pytest.mark.parametrize("mode", [None, "", "keep", "null", "value"])
def test_empty_lookup_modes(mapping, mode):
    from app.core.manipola.transforms.analysis import op_lookup
    df = pd.DataFrame({"a": ["original"]})
    out = op_lookup(df, "a", mapping, unmatched=mode, default="2")
    if mode == "null":
        assert out.a.isna().all()
    else:
        assert out.a.tolist() == ([2] if mode == "value" else ["original"])


@pytest.mark.parametrize("mapping", [{"x": "1"}, [{"from": "x", "to": "1"}], [{"old_name": "x", "new_name": "1"}]])
def test_lookup_value_mapping_dispatch(mapping):
    out = PluginExecutor().execute_with_params("lookup", [pd.DataFrame({"a": ["x", "untouched"]})], {"column": "a", "mapping": mapping, "unmatched": "keep"})
    assert out.a.tolist() == ["1", "untouched"]


def test_join_legacy_dispatch():
    executor = PluginExecutor()
    spy = Mock(return_value="joined")
    executor.functions["join"] = spy
    assert executor.execute_with_params("join", [pd.DataFrame(), pd.DataFrame()], {"left_on": "a", "right_on": "b", "join_type": "left"}) == "joined"
    assert spy.call_args.kwargs["left_keys"] == ["a"]
    assert spy.call_args.kwargs["right_keys"] == ["b"]


def test_selection_reference_ids_use_inputs_only():
    n = node(type="transform", transform_op="select_columns", data={
        "inputColumns": [{"id": "stable", "name": "original"}],
        "columns": [{"id": "stable", "name": "alias"}],
        "config": {"columns": [{"name": "alias", "name_column_id": "stable", "label": "alias"}]},
    })
    out = PluginExecutor().execute("select_columns", [pd.DataFrame({"original": [1]})], n)
    assert out.to_dict("list") == {"alias": [1]}


@pytest.mark.parametrize("typ, value", [
    ("filter_conditions", [{"column": "a", "operator": "==", "value": 1}]),
    ("formula_items", [{"new_column": "b", "formula": "a + 1"}]),
    ("select_columns_list", [{"name": "a", "label": "b", "keep": True}]),
])
def test_registry_list_shapes_and_references(typ, value):
    import json
    from app.shared.execution.config_column_refs import resolve_config_column_refs
    executor = PluginExecutor()
    assert executor._converti_valore(json.dumps(value), typ) == value
    assert executor._converti_valore(value, typ) == value
    config = {"conditions": [{"column": "old", "column_id": "id"}],
              "columns": [{"name": "old", "name_column_id": "id"}],
              "items": [{"new_column": "old", "formula": "old + 1"}],
              "mapping": [{"old_name": "old", "new_name": "value"}]}
    out = resolve_config_column_refs(config, {"id": "input"})
    assert out["conditions"][0]["column"] == "input"
    assert out["columns"][0]["name"] == "input"
    assert out["items"] == config["items"]
    assert out["mapping"] == config["mapping"]


def test_grouping_plan_preserves_order_and_repeated_source_aggregations():
    from app.core.manipola.executor import ManipolaExecutor
    n = node(group_by_enabled=True, group_by_columns=["key"], aggregations=[
        {"column": "*", "function": "COUNT", "alias": "rows"},
        {"column": "value", "function": "SUM"},
        {"column": "value", "function": "MAX"},
    ])
    out = ManipolaExecutor(SimpleNamespace())._applica_group_by(pd.DataFrame({"key": [1, 1], "value": [2, 4]}), n)
    assert out.to_dict("list") == {"key": [1], "rows": [2], "value_sum": [6], "value_max": [4]}
    svc = SchemaInferenceService(None)
    svc._get_input_schemas = Mock(return_value=[[ColumnSchema("key", "int"), ColumnSchema("value", "int")]])
    assert [c.name for c in svc._infer_result_node_schema(_NodeSchemaContext(n, {}, {}, "p"))] == list(out)


@pytest.mark.parametrize("label", ["", None, "   ", 123, "b"])
def test_missing_selection_dropped_before_validation(label):
    from app.shared.execution.column_selection import select_column_plan
    assert select_column_plan(["b"], [{"name": "a", "label": label}], skip_missing=True) == [("b", "b")]


@pytest.mark.parametrize("label", ["", None])
@pytest.mark.parametrize("keep", [True, False])
def test_empty_selection_label_uses_input_name(label, keep):
    from app.shared.execution.column_selection import select_column_plan
    assert select_column_plan(["a"], [{"name": "a", "label": label, "keep": keep}]) == ([("a", "a")] if keep else [])


def test_hidden_selection_allows_whitespace_label():
    from app.shared.execution.column_selection import select_column_plan
    assert select_column_plan(["a"], [{"name": "a", "label": "   ", "keep": False}]) == []


@pytest.mark.parametrize("raw", [None, ""])
def test_empty_json_parameters_and_lookup_noop(raw):
    from app.shared.execution.registry_types import LIST_PARAM_TYPES
    executor = PluginExecutor()
    for typ in LIST_PARAM_TYPES | {"pivot_mapping", "value_mapping"}:
        assert executor._converti_valore(raw, typ) is None
    df = pd.DataFrame({"a": ["x"]})
    pd.testing.assert_frame_equal(executor.execute_with_params("lookup", [df], {"column": "a", "mapping": raw}), df)


def test_malformed_lookup_json_has_domain_error():
    with pytest.raises(ValueError, match="^Mappa valori: formato non valido$") as exc:
        PluginExecutor()._converti_valore("not json", "value_mapping")
    assert type(exc.value) is ValueError


@pytest.mark.parametrize("node_type, op, field", [("transform", "select_columns", "columns"), ("result", None, "output_columns")])
@pytest.mark.parametrize("entries, severity", [([{"name": "missing"}], "warning"), (["wrong type"], "error"), ([{}], "error"), ([{"name": "a", "keep": "no"}], "error")])
def test_selection_validation_missing_warns_malformed_errors(node_type, op, field, entries, severity):
    from app.core.manipola.node_validator import ManipolaNodeValidator, register_manipola_validator
    from app.shared.execution.validation_protocol import validator_for
    n = node(type=node_type, transform_op=op, **({"config": {field: entries}} if node_type == "transform" else {"data": {field: entries}}))
    issues = ManipolaNodeValidator().validate_node(n, upstream=InferredSchema(columns=[{"name": "a", "id": "id1"}]))
    assert issues
    assert {issue.severity for issue in issues} == {severity}
    register_manipola_validator()
    assert isinstance(validator_for(node_type), ManipolaNodeValidator)
