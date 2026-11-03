"""Regressioni dei contratti legacy e multipli del redesign Manipola."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.manipola.plugin_executor import PluginExecutor
from app.core.manipola.transforms.columns import op_select_columns
from app.core.manipola.transforms.filtering import op_filter
from app.core.manipola.transforms.formula import op_formula
from app.core.manipola.transforms.joins import op_join
from app.core.projects.schema_inference import SchemaInferenceService, ColumnSchema

pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_filter_legacy_and_conditions():
    df = pd.DataFrame({"n": [1, 2, 3], "s": ["a", "b", "a"]})
    assert op_filter(df, "n", ">", "1")["n"].tolist() == [2, 3]
    result = op_filter(df, conditions=[{"column": "n", "operator": "in", "value": " 1, 3 "}, {"column": "s", "operator": "not_in", "value": [" b "]}])
    assert result["n"].tolist() == [1, 3]
    pd.testing.assert_frame_equal(op_filter(df, conditions=[]), df)
    assert op_filter(df, "n", "in", [])["n"].tolist() == []
    pd.testing.assert_frame_equal(op_filter(df, "n", "not_in", []), df)
    with pytest.raises(ValueError, match="non trovata"):
        op_filter(df, conditions=[{"column": "missing", "operator": "==", "value": 1}])


def test_formula_legacy_order_empty_and_unknown_column():
    df = pd.DataFrame({"n": [2, 3]})
    assert op_formula(df, "n * 2", "double")["double"].tolist() == [4, 6]
    result = op_formula(df, items=[{"new_column": "double", "formula": "n * 2"}, {"new_column": "next", "formula": "double + 1"}])
    assert result["next"].tolist() == [5, 7]
    assert list(df.columns) == ["n"]
    pd.testing.assert_frame_equal(op_formula(df, items=[]), df)
    with pytest.raises(ValueError):
        op_formula(df, items=[{"new_column": "bad", "formula": "missing + 1"}])
    with pytest.raises(ValueError):
        op_formula(df, items=[{"new_column": "bad", "formula": "__import__('os')"}])


@pytest.mark.parametrize("mode, names", [(None, ["id", "v_L", "v_R"]), ("suffix", ["id", "v_L", "v_R"]), ("keep_left", ["id", "v"]), ("keep_right", ["id", "v"])])
def test_join_legacy_and_new_conflicts(mode, names):
    left = pd.DataFrame({"id": [1], "v": [10]})
    right = pd.DataFrame({"id": [1], "v": [20]})
    result = op_join([left, right], left_on="id", right_on="id", name_conflict=mode)
    assert list(result.columns) == names
    if mode in {"keep_left", "keep_right"}:
        assert result["v"].tolist() == ([10] if mode == "keep_left" else [20])
    assert list(op_join([left, right], left_on="id", right_on="id", left_columns=[], right_columns=[]).columns) == ["id"]
    with pytest.raises(ValueError, match="colonna"):
        op_join([left, right], left_on="id", right_on="id", left_columns=["missing"])


def test_join_distinct_keys_collision_and_outer_rows():
    left = pd.DataFrame({"id": [1], "rid": [10]})
    right = pd.DataFrame({"rid": [2], "id": [20]})
    result = op_join([left, right], left_on="id", right_on="rid", how="outer", name_conflict="keep_left")
    assert list(result.columns) == ["id", "rid_L", "rid", "id_R"]
    assert len(result) == 2
    assert result["id"].dropna().tolist() == [1]
    assert result["rid"].dropna().tolist() == [2]


def test_select_columns_runtime():
    df = pd.DataFrame({"a": [1], "b": [2], "new": [3]})
    result = op_select_columns(df, [{"name": "b", "label": "B"}, {"name": "a", "keep": False}])
    assert result.to_dict("records") == [{"B": 2, "new": 3}]
    pd.testing.assert_frame_equal(op_select_columns(df, []), df)
    pd.testing.assert_frame_equal(op_select_columns(df, [{"name": "missing"}]), df)


def test_registry_executes_new_formats_and_keeps_legacy_defaults():
    plugin = PluginExecutor()
    df = pd.DataFrame({"n": [1, 2]})
    assert plugin.execute_with_params("filter", [df], params={"conditions": [{"column": "n", "operator": "in", "value": [2]}]})["n"].tolist() == [2]
    assert plugin.execute_with_params("formula", [df], params={"items": [{"new_column": "x", "formula": "n + 1"}]})["x"].tolist() == [2, 3]
    assert list(plugin.execute_with_params("select_columns", [df], params={"columns": [{"name": "n", "label": "N"}]}).columns) == ["N"]
    assert "conditions" not in plugin.get_default_config("filter")
    assert "items" not in plugin.get_default_config("formula")
    assert "name_conflict" not in plugin.get_default_config("join")


def test_schema_names_types_and_formula_order():
    handlers = SchemaInferenceService(MagicMock())._build_transform_handlers({})
    src = [ColumnSchema(name="n", type="int"), ColumnSchema(name="geom", type="geometry")]
    selected = handlers["select_columns"]([src], {"columns": [{"name": "geom", "label": "shape"}]}, "select")
    assert [(c.name, c.type) for c in selected] == [("shape", "geometry"), ("n", "int")]
    formulas = handlers["formula"]([src], {"items": [{"new_column": "a", "formula": "n * 2"}, {"new_column": "b", "formula": "a + 1"}]}, "formula")
    assert [(c.name, c.type) for c in formulas][-2:] == [("a", "real"), ("b", "real")]
    assert len({c.id for c in formulas[-2:]}) == 2
    assert handlers["formula"]([src], {"items": []}, "formula")[0].name == "n"
    joined = handlers["join"]([src, [ColumnSchema(name="n", type="int"), ColumnSchema(name="geom", type="str")]], {"left_keys": ["n"], "right_keys": ["n"], "name_conflict": "keep_left"}, "join")
    assert [(c.name, c.type) for c in joined] == [("n", "int"), ("geom", "geometry")]


def test_new_join_does_not_use_legacy_downstream_projection():
    from types import SimpleNamespace
    from app.core.manipola.join_optimization import project_columns_dropped_immediately_after_join

    left = pd.DataFrame({"id": [1], "v": [10]})
    right = pd.DataFrame({"id": [1], "v": [20]})
    join = SimpleNamespace(id="j", transform_op="join", config={"left_columns": ["v"], "left_keys": ["id"], "right_keys": ["id"]})
    drop = SimpleNamespace(id="d", transform_op="drop_columns", config={"columns": ["v_L"]})
    canvas = SimpleNamespace(nodes=[join, drop], edges=[SimpleNamespace(source="j", target="d")])
    inputs = [left, right]
    assert project_columns_dropped_immediately_after_join(canvas, join, inputs) is inputs


def test_validator_accepts_new_forms_without_legacy_required_fields():
    from app.core.manipola.node_validator import ManipolaNodeValidator
    from app.shared.execution.schema_types import InferredSchema
    from types import SimpleNamespace

    upstream = InferredSchema(columns=[{"name": "n", "type": "int"}])
    for op, config in [("filter", {"conditions": []}), ("formula", {"items": []}), ("select_columns", {"columns": [{"name": "n", "label": "N"}]})]:
        node = SimpleNamespace(id="t", transform_op=op, config=config, data={})
        assert not ManipolaNodeValidator().validate_node(node, upstream=upstream)
