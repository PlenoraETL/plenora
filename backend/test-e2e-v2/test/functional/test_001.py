"""test_001 - upstream alias removal propagates to downstream column params.

Scenario covered:
- an upstream project exposes source column ``id`` as visible alias ``z``;
- a downstream Manipola project uses that column in a formula and in
  ``drop_columns``;
- the upstream alias is removed, so its public output_contract exposes ``id``;
- saving the downstream canvas through the real HTTP API must rewrite both
  the formula refs and the non-expression ``drop_columns`` parameter.

The script is launched from the host. It talks to the backend via HTTP and
uses Docker CLI only to seed the test output_contract fixture in Postgres.
If the test passes, every generated project is deleted.
"""

from __future__ import annotations

from _e2e import (
    E2EContext,
    assert_equal,
    contract_column,
    edge,
    output_contract,
    project_source_node,
    result_node,
    run_test,
    set_output_contract,
)


COL_ID = "col_e2e_test001_id"


def test(ctx: E2EContext) -> None:
    upstream_id = ctx.create_project(
        name="e2e_v2_test_001_upstream",
        project_type="estrai",
    )
    set_output_contract(
        upstream_id,
        output_contract(
            [
                contract_column(
                    column_id=COL_ID,
                    source_name="id",
                    physical_name="z",
                )
            ],
            producer_project_id=upstream_id,
        ),
    )

    source = project_source_node(
        node_id="source-1",
        source_project_id=upstream_id,
        column_id=COL_ID,
        name="z",
        source_name="id",
    )
    formula = {
        "id": "formula-1",
        "type": "transform",
        "position": {"x": 240, "y": 0},
        "transform_op": "formula",
        "config": {
            "formula": "z*2",
            "new_column": "calculated",
            "formula_column_refs": [{"id": COL_ID, "name": "z"}],
        },
        "data": {
            "config": {
                "formula": "z*2",
                "new_column": "calculated",
                "formula_column_refs": [{"id": COL_ID, "name": "z"}],
            }
        },
    }
    drop = {
        "id": "drop-1",
        "type": "transform",
        "position": {"x": 480, "y": 0},
        "transform_op": "drop_columns",
        "config": {"columns": ["z"], "columns_column_ids": []},
        "data": {"config": {"columns": ["z"], "columns_column_ids": []}},
    }
    canvas = {
        "nodes": [source, formula, drop, result_node()],
        "edges": [
            edge("e1", "source-1", "formula-1"),
            edge("e2", "formula-1", "drop-1"),
            edge("e3", "drop-1", "result-1"),
        ],
    }
    downstream_id = ctx.create_project(
        name="e2e_v2_test_001_downstream",
        project_type="manipola",
        canvas=canvas,
    )

    set_output_contract(
        upstream_id,
        output_contract(
            [
                contract_column(
                    column_id=COL_ID,
                    source_name="id",
                    physical_name="id",
                )
            ],
            producer_project_id=upstream_id,
        ),
    )

    ctx.update_canvas(downstream_id, canvas)
    synced = ctx.get_project(downstream_id)["canvas"]
    nodes = {node["id"]: node for node in synced["nodes"]}

    assert_equal(
        nodes["source-1"]["columns"][0]["name"],
        "id",
        "project_source column name must follow upstream output_contract",
    )
    assert_equal(
        nodes["formula-1"]["config"]["formula"],
        "id*2",
        "formula expression must follow upstream alias removal",
    )
    assert_equal(
        nodes["formula-1"]["config"]["formula_column_refs"],
        [{"id": COL_ID, "name": "id"}],
        "formula refs must keep stable id and current name",
    )
    assert_equal(
        nodes["drop-1"]["config"]["columns"],
        ["id"],
        "drop_columns must rewrite stale visible names",
    )
    assert_equal(
        nodes["drop-1"]["config"]["columns_column_ids"],
        [COL_ID],
        "drop_columns must persist the stable column id companion",
    )


if __name__ == "__main__":
    run_test(test)
