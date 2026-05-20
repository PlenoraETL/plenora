"""Fifth output-contract propagation block for E2E v2 host tests."""

from __future__ import annotations

from typing import Callable

from _contract_more import (
    _col,
    _contract,
    _project_with_transform,
    _resync_and_config,
    _resync_and_nodes,
    _source,
    _upstream,
)
from _contract_more3 import _inactive, _sync_config
from _contract_more4 import _formula_project, _two_source_project
from _e2e import (
    E2EContext,
    assert_equal,
    edge,
    result_node,
    run_test,
    set_output_contract,
    transform_node,
)


def _fanout_project(ctx: E2EContext, case_id: str) -> tuple[str, str]:
    upstream_id, contract = _upstream(
        ctx,
        case_id,
        [_col("col-age", "age", "age"), _col("col-score", "score", "score")],
    )
    source = _source(upstream_id, contract)
    filter_node = transform_node(
        node_id="filter-1",
        transform_op="filter",
        config={"column": "age", "column_column_id": "col-age", "operator": ">", "value": "18"},
    )
    drop_node = transform_node(
        node_id="drop-1",
        transform_op="drop_columns",
        config={"columns": ["score"], "columns_column_ids": ["col-score"]},
    )
    project_id = ctx.create_project(
        name=f"e2e_v2_{case_id}_fanout",
        project_type="manipola",
        canvas={
            "nodes": [source, filter_node, drop_node, result_node()],
            "edges": [
                edge("e-filter", "source-1", "filter-1"),
                edge("e-drop", "source-1", "drop-1"),
                edge("e-result", "drop-1", "result-1"),
            ],
        },
    )
    return upstream_id, project_id


def _two_step_project(
    ctx: E2EContext,
    case_id: str,
    *,
    first_config: dict,
    second_config: dict,
) -> tuple[str, str]:
    upstream_id, contract = _upstream(
        ctx,
        case_id,
        [_col("col-age", "age", "age"), _col("col-score", "score", "score")],
    )
    source = _source(upstream_id, contract)
    first = transform_node(
        node_id="first-1",
        transform_op="formula",
        config=first_config,
    )
    second = transform_node(
        node_id="second-1",
        transform_op="drop_columns",
        config=second_config,
    )
    project_id = ctx.create_project(
        name=f"e2e_v2_{case_id}_two_step",
        project_type="manipola",
        canvas={
            "nodes": [source, first, second, result_node()],
            "edges": [
                edge("e1", "source-1", "first-1"),
                edge("e2", "first-1", "second-1"),
                edge("e3", "second-1", "result-1"),
            ],
        },
    )
    return upstream_id, project_id


def test_251(ctx: E2EContext) -> None:
    previous = [_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")]
    synced = _sync_config(
        ctx,
        "test_251",
        config={"column": "id", "column_column_id": "stale-id"},
        previous_columns=previous,
        next_columns=[_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")],
    )
    assert_equal(synced["column"], "id", "Stale scalar id with ambiguous name must preserve name")
    assert_equal(synced["column_column_id"], "stale-id", "Stale scalar id with ambiguous name must preserve id")


def test_252(ctx: E2EContext) -> None:
    previous = [_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")]
    synced = _sync_config(
        ctx,
        "test_252",
        config={"columns": ["id"], "columns_column_ids": ["stale-id"]},
        previous_columns=previous,
        next_columns=[_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")],
    )
    assert_equal(synced["columns"], ["id"], "Stale list id with ambiguous name must preserve item")
    assert_equal(synced["columns_column_ids"], ["stale-id"], "Stale list id with ambiguous name must preserve id")


def test_253(ctx: E2EContext) -> None:
    previous = [_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")]
    original = {"column": "id", "column_id": "stale-id", "function": "sum"}
    synced = _sync_config(
        ctx,
        "test_253",
        config={"aggregations": [original]},
        previous_columns=previous,
        next_columns=[_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")],
    )
    assert_equal(synced["aggregations"], [original], "Stale dict id with ambiguous name must preserve item")


def test_254(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_254",
        config={"formula": "id * 2", "formula_column_refs": [{"id": "stale-id", "name": "id"}]},
        previous_columns=[_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")],
        next_columns=[_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")],
        transform_op="formula",
    )
    assert_equal(synced["formula_column_refs"], [{"id": "stale-id", "name": "id"}], "Stale formula ref with ambiguous name must be preserved")


def test_255(ctx: E2EContext) -> None:
    upstream_id, project_id = _fanout_project(ctx, "test_255")
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years"), _col("col-score", "score", "points")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["filter-1"]["config"]["column"], "years", "Fanout filter branch must follow rename")
    assert_equal(nodes["drop-1"]["config"]["columns"], ["points"], "Fanout drop branch must follow rename independently")


def test_256(ctx: E2EContext) -> None:
    upstream_id, project_id = _fanout_project(ctx, "test_256")
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "age")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["filter-1"]["config"]["column"], "age", "Fanout preserved branch must stay valid")
    assert_equal(nodes["drop-1"]["config"]["columns"], [], "Fanout removed branch must clear")


def test_257(ctx: E2EContext) -> None:
    upstream_id, project_id = _fanout_project(ctx, "test_257")
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-score", "score", "score")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["filter-1"]["config"]["column"], "", "Fanout removed scalar branch must clear")
    assert_equal(nodes["drop-1"]["config"]["columns"], ["score"], "Fanout preserved list branch must stay valid")


def test_258(ctx: E2EContext) -> None:
    upstream_id, project_id = _two_step_project(
        ctx,
        "test_258",
        first_config={"formula": "age * 2", "new_column": "calc", "formula_column_refs": [{"id": "col-age", "name": "age"}]},
        second_config={"columns": ["age"], "columns_column_ids": ["col-age"]},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years"), _col("col-score", "score", "score")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["first-1"]["config"]["formula"], "years * 2", "Two-step formula must follow upstream rename")
    assert_equal(nodes["second-1"]["config"]["columns"], ["years"], "Two-step downstream config must follow upstream rename")


def test_259(ctx: E2EContext) -> None:
    upstream_id, project_id = _two_step_project(
        ctx,
        "test_259",
        first_config={"formula": "age * 2", "new_column": "calc", "formula_column_refs": [{"id": "col-age", "name": "age"}]},
        second_config={"columns": ["score"], "columns_column_ids": ["col-score"]},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "age")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["first-1"]["config"]["formula"], "age * 2", "Two-step preserved formula input must stay valid")
    assert_equal(nodes["second-1"]["config"]["columns"], [], "Two-step downstream removed input must clear")


def test_260(ctx: E2EContext) -> None:
    upstream_id, project_id = _two_step_project(
        ctx,
        "test_260",
        first_config={"formula": "age * 2", "new_column": "calc", "formula_column_refs": [{"id": "col-age", "name": "age"}]},
        second_config={"columns": ["calc"], "columns_column_ids": []},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years"), _col("col-score", "score", "score")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["second-1"]["config"]["columns"], ["calc"], "Two-step calculated column config must remain stable")


def test_261(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_261", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "old_age", "operator": ">", "value": "18"}),
        name="e2e_v2_test_261_previous_contract_scalar",
    )
    set_output_contract(project_id, _contract(project_id, [_col("col-age", "age", "old_age")]))
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "transform-1")
    assert_equal(config["column"], "years", "Previous project output_contract must rewrite stale scalar configs")


def test_262(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_262", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="drop_columns", config={"columns": ["old_age"]}),
        name="e2e_v2_test_262_previous_contract_list",
    )
    set_output_contract(project_id, _contract(project_id, [_col("col-age", "age", "old_age")]))
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "transform-1")
    assert_equal(config["columns"], ["years"], "Previous project output_contract must rewrite stale list configs")


def test_263(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_263", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="formula", config={"formula": "old_age * 2", "new_column": "calc"}),
        name="e2e_v2_test_263_previous_contract_formula",
    )
    set_output_contract(project_id, _contract(project_id, [_col("col-age", "age", "old_age")]))
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "transform-1")
    assert_equal(config["formula"], "years * 2", "Previous project output_contract must rewrite stale formula configs")


def test_264(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_264", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="aggregate", config={"aggregations": [{"column": "old_age", "column_id": "", "function": "sum"}]}),
        name="e2e_v2_test_264_previous_contract_dict",
    )
    set_output_contract(project_id, _contract(project_id, [_col("col-age", "age", "old_age")]))
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "transform-1")
    assert_equal(config["aggregations"], [{"column": "years", "column_id": "col-age", "function": "sum"}], "Previous project output_contract must rewrite stale dict configs")


def test_265(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(
        ctx,
        "test_265",
        left_columns=[_col("left-a", "a", "a"), _col("left-b", "b", "b")],
        right_columns=[_col("right-a", "a", "a"), _col("right-b", "b", "b")],
        join_config={"left_keys": ["a", "b"], "left_keys_column_ids": ["left-a", "left-b"], "right_keys": ["a", "b"], "right_keys_column_ids": ["right-a", "right-b"]},
    )
    set_output_contract(left_id, _contract(left_id, [_col("left-a", "a", "left_a"), _col("left-b", "b", "left_b")]))
    set_output_contract(right_id, _contract(right_id, [_col("right-a", "a", "right_a"), _col("right-b", "b", "right_b")]))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["left_keys"], ["left_a", "left_b"], "Multi-key join left_keys must follow left renames")
    assert_equal(config["right_keys"], ["right_a", "right_b"], "Multi-key join right_keys must follow right renames")


def test_266(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(
        ctx,
        "test_266",
        left_columns=[_col("left-a", "a", "a"), _col("left-b", "b", "b")],
        right_columns=[_col("right-a", "a", "a"), _col("right-b", "b", "b")],
        join_config={"left_keys": ["a", "b"], "left_keys_column_ids": ["left-a", "left-b"], "right_keys": ["a", "b"], "right_keys_column_ids": ["right-a", "right-b"]},
    )
    set_output_contract(left_id, _contract(left_id, [_col("left-a", "a", "a")]))
    set_output_contract(right_id, _contract(right_id, [_col("right-a", "a", "a"), _col("right-b", "b", "b")]))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["left_keys"], ["a"], "Multi-key join must drop removed left key only")
    assert_equal(config["right_keys"], ["a", "b"], "Multi-key join must preserve right keys when left key is removed")


def test_267(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(
        ctx,
        "test_267",
        left_columns=[_col("left-a", "a", "a"), _col("left-b", "b", "b")],
        right_columns=[_col("right-a", "a", "a"), _col("right-b", "b", "b")],
        join_config={"left_keys": ["a", "b"], "left_keys_column_ids": ["left-a", "left-b"], "right_keys": ["a", "b"], "right_keys_column_ids": ["right-a", "right-b"]},
    )
    set_output_contract(left_id, _contract(left_id, [_col("left-a", "a", "a"), _col("left-b", "b", "b")]))
    set_output_contract(right_id, _contract(right_id, [_col("right-a", "a", "a")]))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["left_keys"], ["a", "b"], "Multi-key join must preserve left keys when right key is removed")
    assert_equal(config["right_keys"], ["a"], "Multi-key join must drop removed right key only")


def test_268(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_268",
        config={"column": "age", "column_column_id": "col-age", "note": "age"},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "years")],
    )
    assert_equal(synced["note"], "age", "Non-column config keys must not be rewritten")


def test_269(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_269",
        config={"mapping": {"age": "old"}, "column": "age", "column_column_id": "col-age"},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "years")],
    )
    assert_equal(synced["mapping"], {"age": "old"}, "Arbitrary dict config values must not be rewritten")


def test_270(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_270",
        config={"conditions": [{"column": "age"}], "column": "age", "column_column_id": "col-age"},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "years")],
    )
    assert_equal(synced["conditions"], [{"column": "age"}], "Unsupported nested condition configs must not be rewritten silently")


def test_271(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_271", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-age"}),
        name="e2e_v2_test_271_data_config_sync",
    )
    canvas = ctx.get_project(project_id)["canvas"]
    for node in canvas["nodes"]:
        if node["id"] == "transform-1":
            node["data"]["config"] = {"column": "age", "column_column_id": "col-age", "operator": ">", "value": "18"}
            node["config"] = node["data"]["config"]
    ctx.update_canvas(project_id, canvas)
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["transform-1"]["data"]["config"]["column"], "years", "data.config must sync with node config")


def test_272(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_272", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-age"}),
        name="e2e_v2_test_272_project_source_data_columns",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["source-1"]["data"]["columns"][0]["name"], "years", "project_source data.columns must sync with columns")


def test_273(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_273", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-age"}),
        name="e2e_v2_test_273_data_visibility",
    )
    nodes = _resync_and_nodes(ctx, project_id)
    nodes["source-1"]["data"]["columns"][0]["visible"] = False
    nodes["source-1"]["columns"][0]["visible"] = False
    canvas = ctx.get_project(project_id)["canvas"]
    ctx.update_canvas(project_id, {"nodes": list(nodes.values()), "edges": canvas["edges"]})
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["source-1"]["data"]["columns"][0]["visible"], False, "project_source data.columns visibility must be preserved")


def test_274(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_274", [_col("col-age", "age", "age"), _col("col-score", "score", "score")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="drop_columns", config={"columns": ["age", "score"], "columns_column_ids": ["col-age", "col-score"]}),
        name="e2e_v2_test_274_order_change",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-score", "score", "points"), _col("col-age", "age", "years")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal([c["name"] for c in nodes["source-1"]["columns"]], ["points", "years"], "project_source must follow output_contract order")
    assert_equal(nodes["transform-1"]["config"]["columns"], ["years", "points"], "Transform list must follow ids, not project_source order")


def test_275(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_275",
        config={"columns": ["age", "age"], "columns_column_ids": ["col-age", "col-age"]},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "years")],
    )
    assert_equal(synced["columns"], ["years", "years"], "Duplicate list refs to the same id must both rename")


def test_276(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_276",
        config={"aggregations": [{"column": "age", "column_id": "col-age", "function": "sum"}, {"column": "age", "column_id": "col-age", "function": "max"}]},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "years")],
    )
    assert_equal([item["column"] for item in synced["aggregations"]], ["years", "years"], "Duplicate dict refs to same id must all rename")


def test_277(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_277",
        columns=[_col("col-age", "age", "age")],
        config={"formula": "age + age", "formula_column_refs": [{"id": "col-age", "name": "age"}]},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "years + years", "Repeated formula token must rewrite every occurrence")


def test_278(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_278",
        columns=[_col("col-age", "age", "age")],
        config={"formula": "`age` + df['age'] + df[\"age\"] + age", "formula_column_refs": [{"id": "col-age", "name": "age"}]},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "`years` + df['years'] + df[\"years\"] + years", "Formula rewrite must handle all supported token forms together")


def test_279(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_279",
        config={"formula": "age + score", "new_column": "calc"},
        previous_columns=[_col("col-age", "age", "age"), _col("col-score", "score", "score")],
        next_columns=[_col("col-score", "score", "points"), _col("col-age", "age", "years")],
        transform_op="formula",
    )
    assert_equal(synced["formula"], "years + points", "Formula without refs must handle reorder plus multiple rename")


def test_280(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_280",
        config={"formula": "age + score", "new_column": "calc"},
        previous_columns=[_col("col-age", "age", "age"), _col("col-score", "score", "score")],
        next_columns=[_col("col-score", "score", "score")],
        transform_op="formula",
    )
    assert_equal(synced["formula"], "", "Formula without refs must clear when one reordered input is removed")


def test_281(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_281",
        config={"column": "age", "column_column_id": "col-age"},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "age"), _col("col-new", "new_col", "new_col")],
    )
    assert_equal(synced["column"], "age", "Adding columns must not disturb existing scalar config")


def test_282(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_282",
        config={"columns": ["age"], "columns_column_ids": ["col-age"]},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "age"), _col("col-new", "new_col", "new_col")],
    )
    assert_equal(synced["columns"], ["age"], "Adding columns must not disturb existing list config")


def test_283(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_283",
        config={"aggregations": [{"column": "age", "column_id": "col-age", "function": "sum"}]},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "age"), _col("col-new", "new_col", "new_col")],
    )
    assert_equal(synced["aggregations"][0]["column"], "age", "Adding columns must not disturb existing dict config")


def test_284(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_284", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-age"}),
        name="e2e_v2_test_284_output_contract_none",
    )
    set_output_contract(upstream_id, {"version": "1.0", "producer_project_id": upstream_id, "columns": None})
    config = _resync_and_config(ctx, project_id, "transform-1")
    assert_equal(config["column"], "", "None output_contract columns must behave like removed columns")


def test_285(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_285", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-age"}),
        name="e2e_v2_test_285_missing_name_column",
    )
    broken = _col("col-age", "age", "age")
    broken["physical_name"] = ""
    broken["display_name"] = ""
    broken["source_name"] = ""
    set_output_contract(upstream_id, _contract(upstream_id, [broken]))
    config = _resync_and_config(ctx, project_id, "transform-1")
    assert_equal(config["column"], "", "Output_contract columns without usable name must be treated as removed")


def test_286(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_286", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-age"}),
        name="e2e_v2_test_286_missing_source_name",
    )
    col = _col("col-age", "age", "years")
    col["source_name"] = ""
    set_output_contract(upstream_id, _contract(upstream_id, [col]))
    config = _resync_and_config(ctx, project_id, "transform-1")
    assert_equal(config["column"], "years", "Same id must still sync when source_name is missing")


def test_287(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_287",
        config={"column": "age", "column_column_id": "col-age"},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age-v2", "other_source", "years")],
    )
    assert_equal(synced["column"], "years", "Equal-length fallback must handle id and source_name replacement")
    assert_equal(synced["column_column_id"], "col-age-v2", "Equal-length fallback must update replacement id")


def test_288(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_288",
        config={"column": "age", "column_column_id": "col-age"},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-new", "new_source", "new_name"), _col("col-other", "other", "other")],
    )
    assert_equal(synced["column"], "", "Non-equal replacement without id/source match must be treated as removed")


def test_289(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_289", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-age"}),
        name="e2e_v2_test_289_revision_only",
    )
    current = _contract(upstream_id, [_col("col-age", "age", "age")])
    current["revision"] = 999
    set_output_contract(upstream_id, current)
    config = _resync_and_config(ctx, project_id, "transform-1")
    assert_equal(config["column"], "age", "Revision-only output_contract change must not disturb config")


def test_290(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_290", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-age"}),
        name="e2e_v2_test_290_nullable_change",
    )
    col = _col("col-age", "age", "age")
    col["type"]["nullable"] = False
    set_output_contract(upstream_id, _contract(upstream_id, [col]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["source-1"]["columns"][0]["type"], "integer", "Nullable-only type metadata change must keep logical type stable")


def test_291(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_291",
        config={"formula": "age + 1", "formula_column_refs": [{"id": "col-age", "name": "age"}]},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "age")],
        transform_op="formula",
    )
    assert_equal(synced["formula"], "age + 1", "No-op formula sync must keep expression stable")


def test_292(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_292",
        config={"formula": "", "formula_column_refs": [{"id": "col-age", "name": "age"}]},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "years")],
        transform_op="formula",
    )
    assert_equal(synced["formula_column_refs"], [{"id": "col-age", "name": "years"}], "Empty formula with refs must still update ref metadata")


def test_293(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_293",
        config={"formula": None, "formula_column_refs": [{"id": "col-age", "name": "age"}]},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "years")],
        transform_op="formula",
    )
    assert_equal(synced["formula_column_refs"], [{"id": "col-age", "name": "age"}], "Non-string formula must not rewrite refs")


def test_294(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_294",
        config={"columns": "age", "columns_column_ids": ["col-age"]},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "years")],
    )
    assert_equal(synced["columns"], "age", "Non-list list-key config must be preserved")


def test_295(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_295",
        config={"column": ["age"], "column_column_id": "col-age"},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "years")],
    )
    assert_equal(synced["column"], ["age"], "Non-string scalar-key config must be preserved")


def test_296(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_296",
        config={"aggregations": {"column": "age", "column_id": "col-age"}},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "years")],
    )
    assert_equal(synced["aggregations"], {"column": "age", "column_id": "col-age"}, "Non-list dict-key config must be preserved")


def test_297(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_297",
        config={"aggregations": [{"column": 123, "column_id": "col-age", "function": "sum"}]},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "years")],
    )
    assert_equal(synced["aggregations"], [{"column": 123, "column_id": "col-age", "function": "sum"}], "Non-string dict name fields must be preserved")


def test_298(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_298",
        config={"columns": ["age"], "columns_column_ids": "col-age"},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "years")],
    )
    assert_equal(synced["columns"], ["years"], "List config must rename even when companion ids shape is invalid")
    assert_equal(synced["columns_column_ids"], ["col-age"], "List config must repair invalid companion ids shape")


def test_299(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(
        ctx,
        "test_299",
        join_config={"left_on": "id", "left_on_column_id": "left-id", "right_on": "id", "right_on_column_id": "right-id", "columns": ["id"], "columns_column_ids": ["left-id"]},
    )
    set_output_contract(left_id, _contract(left_id, [_col("left-id", "id", "customer_id")]))
    set_output_contract(right_id, _contract(right_id, [_col("right-id", "id", "city_id")]))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["columns"], ["customer_id"], "Extra list config on join must resolve explicit id despite duplicate names")


def test_300(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(
        ctx,
        "test_300",
        join_config={"left_on": "id", "left_on_column_id": "left-id", "right_on": "id", "right_on_column_id": "right-id", "columns": ["id"]},
    )
    set_output_contract(left_id, _contract(left_id, [_col("left-id", "id", "customer_id")]))
    set_output_contract(right_id, _contract(right_id, [_col("right-id", "id", "city_id")]))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["columns"], ["id"], "Extra list config on join without id must preserve ambiguous duplicate name")


TESTS: dict[str, Callable[[E2EContext], None]] = {
    f"test_{i:03d}": globals()[f"test_{i:03d}"]
    for i in range(251, 301)
}


def run_case(case_id: str) -> None:
    run_test(TESTS[case_id])
