"""Shared Python/JavaScript oracle freshness and backend regression contracts."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest

import generate_column_plans_cases as generator
from app.core.manipola import node_validator
from app.core.manipola.transforms.analysis import op_lookup
from app.shared.execution.schema_types import InferredSchema

pytestmark = [pytest.mark.unit, pytest.mark.contract]
COMMITTED = json.loads(generator.FIXTURE_PATH.read_text(encoding="utf-8"))


def assert_fixture_fresh():
    assert COMMITTED == generator.build_cases(), "Regenerate column_plans_cases.json after changing Python rules"


def test_fixture_matches_regenerated_python_rules():
    assert_fixture_fresh()
    assert generator.FIXTURE_PATH.read_text(encoding="utf-8") == generator.serialize(COMMITTED)


@pytest.mark.parametrize("rule", [
    "select_column_plan", "join_column_plan", "prepared_join_plan",
    "aggregation_output_type", "result_group_plan",
])
def test_freshness_check_detects_changed_python_rule(monkeypatch, rule):
    def changed_rule(*args, **kwargs):
        raise ValueError("Changed Python rule")

    monkeypatch.setattr(generator, rule, changed_rule)
    with pytest.raises(AssertionError, match="Regenerate"):
        assert_fixture_fresh()


@pytest.mark.parametrize("kind,case", [
    pytest.param(kind, case, id=f"{kind}/{case['id']}")
    for kind, cases in COMMITTED.items() for case in cases
])
def test_actual_plan_matches_shared_expectation(kind, case):
    assert generator.evaluate(kind, case["input"]) == case["expected"]


def test_grouped_schema_preserves_group_then_aggregation_order(monkeypatch):
    from app.core.projects.schema_inference import ColumnSchema, SchemaInferenceService

    service = SchemaInferenceService(None)
    source = [ColumnSchema(name=name, type=kind) for name, kind in
              [("g", "str"), ("v", "int"), ("h", "str")]]
    monkeypatch.setattr(service, "_get_input_schemas", lambda *args: [source])
    node = SimpleNamespace(id="result", group_by_enabled=True, group_by_columns=["h", "g"],
                           aggregations=[
                               {"column": "v", "function": "sum", "alias": "total"},
                               {"function": "count", "alias": "rows"},
                               {"column": "v", "function": "mean", "alias": "average"},
                               {"column": "v", "function": "std", "alias": "spread"},
                           ])
    context = SimpleNamespace(node=node, nodes_by_id={}, edges_by_target={}, project_id="p")
    result = service._infer_result_node_schema(context)
    assert [(col.name, col.type) for col in result] == [
        ("h", "str"), ("g", "str"), ("total", "int"), ("rows", "int"),
        ("average", "real"), ("spread", "real"),
    ]


@pytest.mark.parametrize("mapping", [None, {}, []])
@pytest.mark.parametrize("dtype", ["int64", "Int64", "boolean", "category", "string"])
@pytest.mark.parametrize("output_column", [None, "out"])
def test_empty_lookup_keep_preserves_entire_frame(mapping, dtype, output_column):
    values = [True, False] if dtype == "boolean" else [1, 2]
    df = pd.DataFrame({"s": pd.Series(values, dtype=dtype), "other": [3, 4]})
    before = df.copy(deep=True)
    result = op_lookup(df, "s", mapping, unmatched="keep", output_column=output_column)
    assert result is df
    pd.testing.assert_frame_equal(result, before)


@pytest.mark.parametrize("mapping", [None, {}, []])
@pytest.mark.parametrize("mode", ["null", "value"])
def test_empty_lookup_nonkeep_treats_every_row_as_unmatched(mapping, mode):
    df = pd.DataFrame({"s": [1, 2]})
    result = op_lookup(df, "s", mapping, unmatched=mode, default="3,5")
    if mode == "null":
        assert result.s.isna().all()
    else:
        assert result.s.tolist() == [3.5, 3.5]
    assert df.s.tolist() == [1, 2]


@pytest.mark.parametrize("columns,invalid", [([], False), ([{"name": "a", "keep": "false"}], True)])
def test_selection_rejection_comes_from_execution(columns, invalid):
    # A3: il piano di selezione invalido lo rifiuta l'esecuzione a vuoto, con
    # lo stesso messaggio dell'esecuzione reale con righe.
    import pandas as pd
    from app.core.manipola.plugin_executor import get_executor
    node = SimpleNamespace(id="t", type="transform", transform_op="select_columns", config={"columns": columns})
    issues = node_validator.ManipolaNodeValidator().validate_node(node, upstream=InferredSchema([{"name": "a", "type": "int"}]))
    errors = [issue for issue in issues if issue.severity == "error"]
    if not invalid:
        assert errors == []
        return
    with pytest.raises(ValueError) as runtime:
        get_executor().execute_with_params("select_columns", [pd.DataFrame({"a": [1]})], {"columns": columns})
    assert [(issue.code, issue.message) for issue in errors] == [("preparation.invalid", str(runtime.value))]


@pytest.mark.parametrize("data", [{}, {"output_columns": None}, {"output_columns": []}])
def test_registered_result_validator_is_harmless_for_estrai(monkeypatch, data):
    from app.core.projects.canvas_models import Node
    from app.shared.execution import validation_protocol

    monkeypatch.setattr(validation_protocol, "_VALIDATORS", {})
    node_validator.register_manipola_validator()
    node = Node.model_validate({"id": "r", "type": "result", "position": {"x": 0, "y": 0}, "data": data})
    validator = validation_protocol.validator_for("result")
    assert validator.validate_node(node, upstream=InferredSchema([])) == []


def test_result_output_selection_still_validates_without_transform_checks():
    node = SimpleNamespace(id="r", type="result", data={"output_columns": [{"name": "a", "label": "  "}]})
    issues = node_validator.ManipolaNodeValidator().validate_node(node, upstream=InferredSchema([{"name": "a"}]))
    assert [issue.code for issue in issues] == ["columns.invalid"]


def test_selection_missing_reference_warns_only_once():
    node = SimpleNamespace(id="t", type="transform", transform_op="select_columns", config={"columns": [{"name": "gone"}]})
    issues = node_validator.ManipolaNodeValidator().validate_node(node, upstream=InferredSchema([{"name": "a"}]))
    assert [(issue.code, issue.severity) for issue in issues] == [("column.missing", "warning")]
