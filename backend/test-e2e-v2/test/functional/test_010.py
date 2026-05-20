"""test_010 - output_contract add/remove columns syncs project_source.

Scenario covered:
- create an upstream project with a seeded output_contract;
- create a downstream Manipola project_source consuming that contract;
- update the upstream contract by removing one column and adding another;
- GET the downstream project and verify the project_source columns follow the
  current contract, not stale canvas columns.
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
)


ID_COL = "col_e2e_test010_id"
AGE_COL = "col_e2e_test010_age"
STATUS_COL = "col_e2e_test010_status"


def test(ctx: E2EContext) -> None:
    upstream_id = ctx.create_project(
        name="e2e_v2_test_010_upstream_contract",
        project_type="estrai",
    )
    initial_contract = output_contract(
        [
            contract_column(column_id=ID_COL, source_name="id", physical_name="id", ordinal=0),
            contract_column(column_id=AGE_COL, source_name="age", physical_name="age", ordinal=1),
        ],
        producer_project_id=upstream_id,
    )
    set_output_contract(upstream_id, initial_contract)

    source = project_source_node_from_contract(
        node_id="source-1",
        source_project_id=upstream_id,
        contract=initial_contract,
    )
    downstream_id = ctx.create_project(
        name="e2e_v2_test_010_downstream_sync",
        project_type="manipola",
        canvas={
            "nodes": [source, result_node()],
            "edges": [edge("e-source-result", "source-1", "result-1")],
        },
    )

    next_contract = output_contract(
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
    )
    set_output_contract(upstream_id, next_contract)

    synced = ctx.get_project(downstream_id)["canvas"]
    source_node = next(node for node in synced["nodes"] if node["id"] == "source-1")
    assert_equal(
        [(column["id"], column["name"]) for column in source_node["columns"]],
        [(ID_COL, "id"), (STATUS_COL, "status")],
        "project_source columns must track output_contract add/remove changes",
    )


if __name__ == "__main__":
    run_test(test)
