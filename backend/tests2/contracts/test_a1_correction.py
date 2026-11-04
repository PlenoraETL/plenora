"""Regressioni del giro indipendente A1: identità, aspetti e varianti runtime."""

import pandas as pd
import pytest

from app.core.dataframes.output_columns import apply_output_columns
from app.core.manipola.plugin_executor import get_executor
from app.core.manipola.canvas_schema_runtime import build_columns_with_types
from app.core.projects.schema_inference import SchemaInferenceService
from app.shared.execution.canvas_columns import compute_result_columns
from tests2.contracts.inference_runtime_cases import CASES, EXTRA
from tests2.contracts.test_inference_runtime_parity import canvas_of, infer_canvas, frames


def canvas_frames(op, inputs, config):
    nodes = [{"id": f"s{i}", "type": "file_source", "data": {"columns": [
        dict(col, id=f"s{i}:{col['name']}:{j}")
        for j, col in enumerate(build_columns_with_types(frame, {}))]}}
        for i, frame in enumerate(inputs)]
    nodes.append({"id": "t", "type": "transform", "transform_op": op, "config": config})
    return canvas_of(nodes, [{"id": f"e{i}", "source": f"s{i}", "target": "t"}
                            for i in range(len(inputs))])


@pytest.mark.parametrize("op,config", list(CASES.items()) + EXTRA)
def test_variants_names_and_unique_ids(op, config):
    inputs = frames(op, False)
    config = dict(config)
    for param in get_executor().registry[op].get("parameters", []):
        if "geom" in param["id"] and param["id"] != "keep_geom_type":
            config.setdefault(param["id"], "geometry")
    if op == "flatten_json":
        for frame in inputs:
            frame["s"] = ['{"a": 1}', '{"b": 2}', '{"a": 3}', '{"c": 4}']
        config["prefix"] = "s."
    if op == "melt" and not config:
        with pytest.raises((ValueError, TypeError)):
            get_executor().execute_with_params(op, inputs, config)
        return
    typed = infer_canvas(canvas_frames(op, inputs, config), "t")
    actual = get_executor().execute_with_params(op, inputs, config)
    ids = [col["id"] for col in typed.columns]
    assert len(ids) == len(set(ids))
    names = [col["name"] for col in typed.columns]
    if names != list(actual.columns):
        alternative = get_executor().execute_with_params(op, [f.iloc[:0] for f in inputs], config)
        assert list(alternative.columns) != list(actual.columns)
        assert typed.names_status == "indeterminate" or any(c.get("optional") for c in typed.columns)


@pytest.mark.parametrize("branches", [False, True])
def test_self_join_identity_and_legacy_output(branches):
    source = {"id": "s", "type": "file_source", "data": {"columns": [
        {"name": "id", "type": "int", "id": "key"},
        {"name": "b", "type": "int", "id": "b-1"}]}}
    nodes = [source]
    edges = []
    sides = ["s", "s"]
    if branches:
        sides = ["left", "right"]
        for side in sides:
            nodes.append({"id": side, "type": "transform", "transform_op": "filter",
                          "config": {"column": "b", "value": "1"}})
            edges.append({"id": side, "source": "s", "target": side})
    nodes += [{"id": "j", "type": "transform", "transform_op": "join",
               "config": {"left_keys": ["id"], "right_keys": ["id"]}},
              {"id": "r", "type": "result", "columns": [
                  {"name": "b_L", "id": "b-1", "alias": "sinistra"}]}]
    edges += [{"id": f"j{i}", "source": side, "target": "j", "targetHandle": handle}
              for i, (side, handle) in enumerate(zip(sides, ["left", "right"]))]
    edges.append({"id": "out", "source": "j", "target": "r"})
    canvas = canvas_of(nodes, edges)
    schema = infer_canvas(canvas, "j").columns
    assert len({col["id"] for col in schema}) == len(schema)
    reordered = canvas.model_copy(deep=True)
    saved_columns = reordered.nodes[0].data["columns"]
    reordered.nodes[0].data["columns"] = [{"name": "extra", "type": "int", "id": "extra-id"}, *reversed(saved_columns)]
    changed = {col["name"]: col["id"] for col in infer_canvas(reordered, "j").columns}
    assert all(changed[col["name"]] == col["id"] for col in schema)
    frame = pd.DataFrame({"id": [1], "b_L": [2], "b_R": [3]})
    selection = [{"name": "b_L", "name_column_id": "b-1", "label": "sinistra"}]
    actual = apply_output_columns(frame, selection, schema)
    assert actual.sinistra.tolist() == [2]
    saved = compute_result_columns(canvas, "r", "p", None, SchemaInferenceService)
    assert [(col["name"], col["alias"]) for col in saved] == [
        ("id", None), ("b_L", "sinistra"), ("b_R", None)]


def test_ambiguous_identity_falls_back_to_name():
    frame = pd.DataFrame({"b_L": [2], "b_R": [3]})
    schema = [{"id": "b-1", "name": name} for name in frame.columns]
    result = apply_output_columns(frame, [{"name": "b_L", "name_column_id": "b-1", "label": "sinistra"}], schema)
    assert result.sinistra.tolist() == [2]


def test_aggregate_ids_survive_insert_and_reorder():
    inputs = frames("aggregate", False)
    aggs = [{"column": "a", "function": "sum", "alias": "tot_a"},
            {"column": "b", "function": "sum", "alias": "tot_b"}]
    before = infer_canvas(canvas_frames("aggregate", inputs, {"group_by": ["id"], "aggregations": aggs}), "t")
    after = infer_canvas(canvas_frames("aggregate", inputs, {"group_by": ["id"], "aggregations": [
        {"column": "b", "function": "max", "alias": "max_b"}, *reversed(aggs)]}), "t")
    old = {col["name"]: col["id"] for col in before.columns}
    new = {col["name"]: col["id"] for col in after.columns}
    assert all(new[name] == identity for name, identity in old.items())
    frame = pd.DataFrame({"id": [1], "max_b": [9], "tot_b": [8], "tot_a": [7]})
    result = apply_output_columns(frame, [{"name": "tot_a", "name_column_id": old["tot_a"], "label": "Totale A"}], after.columns)
    assert result["Totale A"].tolist() == [7]


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("formula", [False, True])
def test_missing_filter_reference_stays_error(external, formula):
    from app.core.projects.canvas_validation import validate_canvas
    from app.core.manipola.node_validator import register_manipola_validator
    register_manipola_validator()
    nodes = [{"id": "s", "type": "file_source", "file_source_id": "file" if external else None,
              "data": {"columns": [{"name": "a", "type": "int"}]}}]
    if formula:
        nodes.append({"id": "fo", "type": "transform", "transform_op": "formula",
                      "config": {"new_column": "z", "formula": "a*2"}})
    nodes += [{"id": "f", "type": "transform", "transform_op": "filter",
               "config": {"column": "inesistente", "value": "1"}}, {"id": "r", "type": "result"}]
    canvas = canvas_of(nodes, [{"id": str(i), "source": a["id"], "target": b["id"]}
                              for i, (a, b) in enumerate(zip(nodes, nodes[1:]))])
    report = validate_canvas(canvas, project_id="p", project_type="manipola")
    assert any(issue.severity == "error" and "inesistente" in issue.message for issue in report.issues)
    typed = infer_canvas(canvas, "fo" if formula else "s")
    assert typed.names_status in {"certain", "metadata"}
    if external or formula:
        assert typed.types_status == "indeterminate"


def test_wkt_preview_and_branch_types():
    assert build_columns_with_types(pd.DataFrame({"geometry": ["POINT (0 0)"]}), {}) == [
        {"name": "geometry", "type": "geometry"}]
    canvas = canvas_frames("join", [pd.DataFrame({"id": [1], "a": [2]}),
                                   pd.DataFrame({"id": [1], "a": ["testo"]})],
                           {"left_keys": ["id"], "right_keys": ["id"]})
    assert {col["name"]: col["type"] for col in infer_canvas(canvas, "t").columns} == {
        "id": "int", "a_L": "int", "a_R": "str"}


@pytest.mark.parametrize("kind,values", [("int", [1, 3]), ("real", [1.5, 3.5]),
    ("bool", [True, False]), ("date", pd.to_datetime(["2024-01-01", "2024-01-03"])),
    ("datetime", pd.to_datetime(["2024-01-01", "2024-01-03"]))])
def test_median_type_matches_runtime(kind, values):
    from app.shared.execution.schema_column_plans import aggregation_output_type
    frame = pd.DataFrame({"id": [1, 1], "a": values})
    result = get_executor().execute_with_params("aggregate", [frame], {
        "group_by": ["id"], "aggregations": [{"column": "a", "function": "median"}]})
    predicted = aggregation_output_type("median", kind)
    assert ("date" if predicted == "datetime" else predicted) == build_columns_with_types(result, {})[1]["type"]


def test_median_text_is_rejected_by_runtime():
    with pytest.raises((TypeError, ValueError)):
        get_executor().execute_with_params("aggregate", [pd.DataFrame({"id": [1, 1], "a": ["x", "y"]})], {
            "group_by": ["id"], "aggregations": [{"column": "a", "function": "median"}]})


@pytest.mark.parametrize("config", [{}, {"columns": [{"name": "a", "label": "A"}]}])
def test_select_duplicate_inputs(config):
    inputs = [pd.DataFrame([[1, 2, 3]], columns=["a", "b", "a"])]
    if not config:
        with pytest.raises(ValueError, match="univoci"):
            get_executor().execute_with_params("select_columns", inputs, config)
        return
    actual = get_executor().execute_with_params("select_columns", inputs, config)
    typed = infer_canvas(canvas_frames("select_columns", inputs, config), "t")
    assert [col["name"] for col in typed.columns] == list(actual.columns)
    assert len({col["id"] for col in typed.columns}) == len(typed.columns)


def test_concat_different_schemas():
    inputs = [pd.DataFrame({"a": [1], "b": [2]}), pd.DataFrame({"b": ["x"], "c": [True]})]
    actual = get_executor().execute_with_params("concat", inputs, {})
    typed = infer_canvas(canvas_frames("concat", inputs, {}), "t")
    assert [col["name"] for col in typed.columns] == list(actual.columns)
    assert typed.names_status == "certain"
    assert typed.types_status == "indeterminate"


def test_formula_items_identity_survives_reorder():
    inputs = frames("formula", False)
    items = [{"new_column": "prima", "formula": "a*2"}, {"new_column": "seconda", "formula": "b*2"}]
    old = infer_canvas(canvas_frames("formula", inputs, {"items": items}), "t")
    new = infer_canvas(canvas_frames("formula", inputs, {"items": list(reversed(items))}), "t")
    assert {c["name"]: c["id"] for c in old.columns} == {c["name"]: c["id"] for c in new.columns}


def test_runtime_node_types_have_executable_cases():
    import ast
    from pathlib import Path
    from tests2.contracts import test_inference_runtime_parity as cases
    from tests2.contracts.test_a3_preparation import test_static_source_distinct_preparation
    from tests2.contracts.test_a3_completion import test_api_geometry_overrides_absent_and_stale_saved_columns
    coverage = {
        "file_source": cases.test_source_nodes,
        "datasource_node": cases.test_source_nodes,
        "staticValues": cases.test_static_node_duplicates_and_casts,
        "project_source": cases.test_project_source_projection,
        "project_input": test_static_source_distinct_preparation,
        "api_enrich": test_api_geometry_overrides_absent_and_stale_saved_columns,
        "transform": test_variants_names_and_unique_ids,
        "multi_join": cases.test_multi_join_node,
        "result": cases.test_result_group_alias_collision,
    }
    root = Path(__file__).parents[2] / "app/core/manipola"
    discovered = set()
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            left = node.left
            is_type = isinstance(left, ast.Attribute) and left.attr == "type"
            is_type |= (isinstance(left, ast.Call) and isinstance(left.func, ast.Name)
                        and left.func.id == "getattr" and len(left.args) > 1
                        and isinstance(left.args[1], ast.Constant) and left.args[1].value == "type")
            if is_type:
                for comparator in node.comparators:
                    for value in ast.walk(comparator):
                        if isinstance(value, ast.Constant) and isinstance(value.value, str):
                            discovered.add(value.value)
    assert discovered
    assert discovered <= coverage.keys(), f"Tipi runtime senza casi: {discovered - coverage.keys()}"
    assert all(callable(case) for case in coverage.values())
