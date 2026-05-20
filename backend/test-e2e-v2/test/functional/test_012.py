"""test_012 - output_contract alias rename propagates through aggregate config.

Scenario covered:
- upstream exposes ``status`` as alias ``state``;
- downstream aggregate groups by ``state`` using the stable column id;
- upstream alias changes back to ``status``;
- downstream canvas sync must rewrite aggregate ``group_by`` while preserving
  the stable id companion.
"""

from __future__ import annotations

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


STATUS_COL = "col_e2e_test012_status"
ID_COL = "col_e2e_test012_id"


def test(ctx: E2EContext) -> None:
    upstream_id = ctx.create_project(
        name="e2e_v2_test_012_upstream_alias",
        project_type="estrai",
    )
    initial_contract = output_contract(
        [
            contract_column(column_id=ID_COL, source_name="id", physical_name="id", ordinal=0),
            contract_column(
                column_id=STATUS_COL,
                source_name="status",
                physical_name="state",
                logical_type="text",
                ordinal=1,
            ),
        ],
        producer_project_id=upstream_id,
    )
    set_output_contract(upstream_id, initial_contract)

    source = project_source_node_from_contract(
        node_id="source-1",
        source_project_id=upstream_id,
        contract=initial_contract,
    )
    aggregate = transform_node(
        node_id="aggregate-1",
        transform_op="aggregate",
        config={
            "group_by": ["state"],
            "group_by_column_ids": [STATUS_COL],
            "aggregations": [
                {"column": "id", "column_id": ID_COL, "function": "count", "alias": "n"}
            ],
        },
        input_columns=source["columns"],
    )
    downstream_id = ctx.create_project(
        name="e2e_v2_test_012_downstream_aggregate_sync",
        project_type="manipola",
        canvas={
            "nodes": [source, aggregate, result_node()],
            "edges": [
                edge("e-source-aggregate", "source-1", "aggregate-1"),
                edge("e-aggregate-result", "aggregate-1", "result-1"),
            ],
        },
    )

    set_output_contract(
        upstream_id,
        output_contract(
            [
                contract_column(column_id=ID_COL, source_name="id", physical_name="id", ordinal=0),
                contract_column(
                    column_id=STATUS_COL,
                    source_name="status",
                    physical_name="status",
                    logical_type="text",
                    ordinal=1,
                ),
            ],
            producer_project_id=upstream_id,
        ),
    )

    synced = ctx.get_project(downstream_id)["canvas"]
    aggregate_node = next(node for node in synced["nodes"] if node["id"] == "aggregate-1")
    assert_equal(
        aggregate_node["config"]["group_by"],
        ["status"],
        "aggregate group_by must follow upstream alias rename",
    )
    assert_equal(
        aggregate_node["config"]["group_by_column_ids"],
        [STATUS_COL],
        "aggregate group_by must preserve stable id companion",
    )


if __name__ == "__main__":
    run_test(test)
