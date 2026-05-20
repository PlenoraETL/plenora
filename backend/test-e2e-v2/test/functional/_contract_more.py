"""Output-contract propagation edge cases for E2E v2 host tests."""

from __future__ import annotations

from typing import Callable

from _e2e import (
    E2EContext,
    assert_equal,
    contract_column,
    edge,
    output_contract,
    project_source_node_from_contract,
    result_node,
    run_test,
    set_output_contract,
    transform_node,
)


def _col(column_id: str, source_name: str, physical_name: str, logical_type: str = "integer") -> dict:
    return contract_column(
        column_id=column_id,
        source_name=source_name,
        physical_name=physical_name,
        logical_type=logical_type,
    )


def _contract(project_id: str, columns: list[dict]) -> dict:
    return output_contract(columns, producer_project_id=project_id)


def _source(upstream_id: str, contract: dict) -> dict:
    return project_source_node_from_contract(
        node_id="source-1",
        source_project_id=upstream_id,
        contract=contract,
    )


def _project_with_transform(
    ctx: E2EContext,
    *,
    upstream_id: str,
    contract: dict,
    transform: dict,
    name: str,
) -> str:
    return ctx.create_project(
        name=name,
        project_type="manipola",
        canvas={
            "nodes": [_source(upstream_id, contract), transform, result_node()],
            "edges": [
                edge("e-source-transform", "source-1", transform["id"]),
                edge("e-transform-result", transform["id"], "result-1"),
            ],
        },
    )


def _resync_and_config(ctx: E2EContext, project_id: str, node_id: str) -> dict:
    canvas = ctx.get_project(project_id)["canvas"]
    ctx.update_canvas(project_id, canvas)
    synced = ctx.get_project(project_id)["canvas"]
    return {node["id"]: node for node in synced["nodes"]}[node_id]["config"]


def _resync_and_nodes(ctx: E2EContext, project_id: str) -> dict[str, dict]:
    canvas = ctx.get_project(project_id)["canvas"]
    ctx.update_canvas(project_id, canvas)
    synced = ctx.get_project(project_id)["canvas"]
    return {node["id"]: node for node in synced["nodes"]}


def _upstream(ctx: E2EContext, case_id: str, columns: list[dict]) -> tuple[str, dict]:
    upstream_id = ctx.create_project(name=f"e2e_v2_{case_id}_upstream", project_type="estrai")
    contract = _contract(upstream_id, columns)
    set_output_contract(upstream_id, contract)
    return upstream_id, contract


def test_091(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_091", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="filter-1",
            transform_op="filter",
            config={"column": "age", "column_column_id": "col-age", "operator": ">=", "value": "18"},
        ),
        name="e2e_v2_test_091_filter_rename",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "filter-1")
    assert_equal(config["column"], "years", "Scalar filter column must follow upstream rename")
    assert_equal(config["column_column_id"], "col-age", "Scalar filter id must be preserved")


def test_092(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_092", [_col("col-id", "id", "id"), _col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="filter-1",
            transform_op="filter",
            config={"column": "age", "column_column_id": "col-age", "operator": ">=", "value": "18"},
        ),
        name="e2e_v2_test_092_filter_removed",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-id", "id", "id")]))
    config = _resync_and_config(ctx, project_id, "filter-1")
    assert_equal(config["column"], "", "Removed scalar column must clear filter column")
    assert_equal(config["column_column_id"], None, "Removed scalar column must clear companion id")


def test_093(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_093", [_col("col-date", "signup_date", "signup_date", "date")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="sort-1",
            transform_op="sort",
            config={"order_column": "signup_date", "order_column_column_id": "col-date", "ascending": True},
        ),
        name="e2e_v2_test_093_sort_rename",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-date", "signup_date", "registered_at", "date")]))
    config = _resync_and_config(ctx, project_id, "sort-1")
    assert_equal(config["order_column"], "registered_at", "Sort order_column must follow upstream rename")


def test_094(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_094", [_col("col-first", "first", "first"), _col("col-last", "last", "last")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="concat-1",
            transform_op="concat_columns",
            config={"columns": ["first", "last"], "columns_column_ids": ["col-first", "col-last"], "output_column": "full"},
        ),
        name="e2e_v2_test_094_list_rename",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-first", "first", "given"), _col("col-last", "last", "family")]))
    config = _resync_and_config(ctx, project_id, "concat-1")
    assert_equal(config["columns"], ["given", "family"], "List column config must follow upstream renames")
    assert_equal(config["columns_column_ids"], ["col-first", "col-last"], "List column ids must stay aligned")


def test_095(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_095", [_col("col-first", "first", "first"), _col("col-last", "last", "last")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="concat-1",
            transform_op="concat_columns",
            config={"columns": ["first", "last"], "columns_column_ids": ["col-first", "col-last"], "output_column": "full"},
        ),
        name="e2e_v2_test_095_list_removed",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-first", "first", "first")]))
    config = _resync_and_config(ctx, project_id, "concat-1")
    assert_equal(config["columns"], ["first"], "Removed list column must be dropped from config")
    assert_equal(config["columns_column_ids"], ["col-first"], "Removed list column id must be dropped from companion ids")


def test_096(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_096", [_col("col-amount", "amount", "amount")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="aggregate-1",
            transform_op="aggregate",
            config={"group_by": [], "aggregations": [{"column": "amount", "column_id": "col-amount", "function": "sum", "alias": "amount_sum"}]},
        ),
        name="e2e_v2_test_096_aggregation_rename",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-amount", "amount", "total_amount")]))
    config = _resync_and_config(ctx, project_id, "aggregate-1")
    assert_equal(config["aggregations"][0]["column"], "total_amount", "Aggregation dict column must follow upstream rename")


def test_097(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_097", [_col("col-id", "id", "id"), _col("col-amount", "amount", "amount")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="aggregate-1",
            transform_op="aggregate",
            config={"group_by": [], "aggregations": [{"column": "amount", "column_id": "col-amount", "function": "sum", "alias": "amount_sum"}]},
        ),
        name="e2e_v2_test_097_aggregation_removed",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-id", "id", "id")]))
    config = _resync_and_config(ctx, project_id, "aggregate-1")
    assert_equal(config["aggregations"], [], "Removed aggregation source column must drop that aggregation")


def test_098(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_098", [_col("col-id", "id", "id")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="rename-1",
            transform_op="rename",
            config={"renames": [{"old_name": "id", "old_name_id": "col-id", "new_name": "identifier"}]},
        ),
        name="e2e_v2_test_098_rename_input_rename",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-id", "id", "customer_id")]))
    config = _resync_and_config(ctx, project_id, "rename-1")
    assert_equal(config["renames"][0]["old_name"], "customer_id", "Rename old_name must follow upstream rename")


def test_099(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_099", [_col("col-id", "id", "id"), _col("col-z", "z", "z")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="rename-1",
            transform_op="rename",
            config={"renames": [{"old_name": "z", "old_name_id": "col-z", "new_name": "zed"}]},
        ),
        name="e2e_v2_test_099_rename_input_removed",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-id", "id", "id")]))
    config = _resync_and_config(ctx, project_id, "rename-1")
    assert_equal(config["renames"], [], "Removed rename input column must drop that rename mapping")


def test_100(ctx: E2EContext) -> None:
    left_id, left_contract = _upstream(ctx, "test_100_left", [_col("left-key", "id", "id")])
    right_id, right_contract = _upstream(ctx, "test_100_right", [_col("right-key", "id", "id")])
    left = project_source_node_from_contract(node_id="left-source", source_project_id=left_id, contract=left_contract)
    right = project_source_node_from_contract(node_id="right-source", source_project_id=right_id, contract=right_contract)
    join = transform_node(
        node_id="join-1",
        transform_op="join",
        config={"left_on": "id", "left_on_column_id": "left-key", "right_on": "id", "right_on_column_id": "right-key", "how": "inner"},
    )
    project_id = ctx.create_project(
        name="e2e_v2_test_100_join_key_renames",
        project_type="manipola",
        canvas={"nodes": [left, right, join, result_node()], "edges": [edge("e1", "left-source", "join-1"), edge("e2", "right-source", "join-1"), edge("e3", "join-1", "result-1")]},
    )
    set_output_contract(left_id, _contract(left_id, [_col("left-key", "id", "customer_id")]))
    set_output_contract(right_id, _contract(right_id, [_col("right-key", "id", "city_id")]))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["left_on"], "customer_id", "Join left_on must follow left upstream rename")
    assert_equal(config["right_on"], "city_id", "Join right_on must follow right upstream rename")


def test_101(ctx: E2EContext) -> None:
    left_id, left_contract = _upstream(ctx, "test_101_left", [_col("left-key", "id", "id")])
    right_id, right_contract = _upstream(ctx, "test_101_right", [_col("right-key", "id", "id")])
    left = project_source_node_from_contract(node_id="left-source", source_project_id=left_id, contract=left_contract)
    right = project_source_node_from_contract(node_id="right-source", source_project_id=right_id, contract=right_contract)
    join = transform_node(
        node_id="join-1",
        transform_op="join",
        config={"left_on": "id", "left_on_column_id": "left-key", "right_on": "id", "right_on_column_id": "right-key", "how": "inner"},
    )
    project_id = ctx.create_project(
        name="e2e_v2_test_101_join_key_removed",
        project_type="manipola",
        canvas={"nodes": [left, right, join, result_node()], "edges": [edge("e1", "left-source", "join-1"), edge("e2", "right-source", "join-1"), edge("e3", "join-1", "result-1")]},
    )
    set_output_contract(left_id, _contract(left_id, []))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["left_on"], "", "Removed join left key must clear left_on")
    assert_equal(config["left_on_column_id"], None, "Removed join left key must clear companion id")


def test_102(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_102", [_col("col-a", "a", "a"), _col("col-b", "b", "b")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="formula-1",
            transform_op="formula",
            config={"formula": "a+1", "new_column": "calc", "formula_column_refs": [{"id": "col-a", "name": "a"}]},
        ),
        name="e2e_v2_test_102_reorder_no_false_rename",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-b", "b", "b"), _col("col-a", "a", "a")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "a+1", "Column reorder must not rewrite formula to a different column")
    assert_equal(config["formula_column_refs"], [{"id": "col-a", "name": "a"}], "Column reorder must preserve formula refs")


def test_103(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_103", [_col("col-a", "a", "a"), _col("col-b", "b", "b")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="formula-1",
            transform_op="formula",
            config={"formula": "a+1", "new_column": "calc", "formula_column_refs": [{"id": "col-a", "name": "a"}]},
        ),
        name="e2e_v2_test_103_reorder_with_rename",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-b", "b", "b"), _col("col-a", "a", "alpha")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "alpha+1", "Column reorder plus rename must rewrite formula by id")
    assert_equal(config["formula_column_refs"], [{"id": "col-a", "name": "alpha"}], "Column reorder plus rename must keep formula id")


def test_104(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_104", [_col("col-id", "id", "id"), _col("col-z", "z", "z")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="drop-1", transform_op="drop_columns", config={"columns": ["z"], "columns_column_ids": ["col-z"]}),
        name="e2e_v2_test_104_inactive_column_removed",
    )
    inactive = _col("col-z", "z", "z")
    inactive["status"] = "inactive"
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-id", "id", "id"), inactive]))
    config = _resync_and_config(ctx, project_id, "drop-1")
    assert_equal(config["columns"], [], "Inactive upstream column must be treated as removed")


def test_105(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_105", [_col("col-id", "id", "id")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="noop-1", transform_op="reorder_columns", config={"columns": ["id"], "columns_column_ids": ["col-id"]}),
        name="e2e_v2_test_105_added_column_source_sync",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-id", "id", "id"), _col("col-z", "z", "z")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal([c["name"] for c in nodes["source-1"]["columns"]], ["id", "z"], "Added upstream column must appear in project_source")


def test_106(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_106", [_col("col-id", "id", "id")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="noop-1", transform_op="reorder_columns", config={"columns": ["id"], "columns_column_ids": ["col-id"]}),
        name="e2e_v2_test_106_visibility_preserved",
    )
    nodes = _resync_and_nodes(ctx, project_id)
    nodes["source-1"]["columns"][0]["visible"] = False
    nodes["source-1"]["data"]["columns"][0]["visible"] = False
    ctx.update_canvas(project_id, {"nodes": list(nodes.values()), "edges": ctx.get_project(project_id)["canvas"]["edges"]})
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-id", "id", "customer_id")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["source-1"]["columns"][0]["visible"], False, "Project_source visibility must be preserved by column id")


def test_107(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_107", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="formula-1", transform_op="formula", config={"formula": "age*2", "new_column": "double_age"}),
        name="e2e_v2_test_107_formula_without_refs",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "years*2", "Formula without refs must still be rewritten from legacy names")
    assert_equal(config["formula_column_refs"], [{"id": "col-age", "name": "years"}], "Formula without refs must collect current refs")


def test_108(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_108", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="formula-1", transform_op="formula", config={"formula": "`age`*2", "new_column": "double_age", "formula_column_refs": [{"id": "col-age", "name": "age"}]}),
        name="e2e_v2_test_108_formula_backtick_refs",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "`years`*2", "Backtick formula token must be rewritten safely")


def test_109(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_109", [_col("col-id", "id", "id"), _col("col-z", "z", "z")])
    source = _source(upstream_id, contract)
    pass_node = transform_node(node_id="rename-1", transform_op="rename", config={"renames": []})
    drop = transform_node(node_id="drop-1", transform_op="drop_columns", config={"columns": ["z"], "columns_column_ids": ["col-z"]})
    project_id = ctx.create_project(
        name="e2e_v2_test_109_removed_column_through_chain",
        project_type="manipola",
        canvas={"nodes": [source, pass_node, drop, result_node()], "edges": [edge("e1", "source-1", "rename-1"), edge("e2", "rename-1", "drop-1"), edge("e3", "drop-1", "result-1")]},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-id", "id", "id")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["drop-1"]["config"]["columns"], [], "Removed column migrations must propagate through intermediate transforms")


def test_110(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_110", [_col("col-id", "id", "id"), _col("col-status", "status", "status")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="aggregate-1",
            transform_op="aggregate",
            config={"group_by": ["status"], "group_by_column_ids": ["col-status"], "aggregations": [{"column": "id", "column_id": "col-id", "function": "count", "alias": "n"}]},
        ),
        name="e2e_v2_test_110_group_by_rename",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-id", "id", "id"), _col("col-status", "status", "state")]))
    config = _resync_and_config(ctx, project_id, "aggregate-1")
    assert_equal(config["group_by"], ["state"], "Aggregate group_by list must follow upstream rename")
    assert_equal(config["group_by_column_ids"], ["col-status"], "Aggregate group_by ids must stay aligned")


TESTS: dict[str, Callable[[E2EContext], None]] = {
    f"test_{i:03d}": globals()[f"test_{i:03d}"]
    for i in range(91, 111)
}


def run_case(case_id: str) -> None:
    run_test(TESTS[case_id])
