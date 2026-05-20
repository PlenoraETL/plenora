"""test_002 - calculated column rename propagates through project chains.

Scenario covered:
- an upstream Manipola-like project exposes a calculated column named
  ``calculated``;
- a downstream Manipola project uses that column in a formula;
- the upstream calculated output is renamed to ``calculated3``;
- saving the downstream canvas through the real HTTP API must rewrite the
  formula and update ``formula_column_refs`` to the current output_contract.

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


OLD_COL_ID = "col_e2e_test002_old_calculated"
NEW_COL_ID = "col_e2e_test002_new_calculated"
CALC_SOURCE = "calculated:formula-1:formula"


def test(ctx: E2EContext) -> None:
    upstream_id = ctx.create_project(
        name="e2e_v2_test_002_upstream",
        project_type="manipola",
    )
    set_output_contract(
        upstream_id,
        output_contract(
            [
                contract_column(
                    column_id=OLD_COL_ID,
                    source_name="calculated",
                    physical_name="calculated",
                )
            ],
            producer_project_id=upstream_id,
        ),
    )

    source = project_source_node(
        node_id="source-1",
        source_project_id=upstream_id,
        column_id=OLD_COL_ID,
        name="calculated",
        source_name="calculated",
    )
    formula = {
        "id": "formula-2",
        "type": "transform",
        "position": {"x": 240, "y": 0},
        "transform_op": "formula",
        "config": {
            "formula": "calculated*4",
            "new_column": "calculated2",
            "formula_column_refs": [{"id": OLD_COL_ID, "name": "calculated"}],
        },
        "data": {
            "config": {
                "formula": "calculated*4",
                "new_column": "calculated2",
                "formula_column_refs": [{"id": OLD_COL_ID, "name": "calculated"}],
            }
        },
    }
    canvas = {
        "nodes": [source, formula, result_node()],
        "edges": [
            edge("e1", "source-1", "formula-2"),
            edge("e2", "formula-2", "result-1"),
        ],
    }
    downstream_id = ctx.create_project(
        name="e2e_v2_test_002_downstream",
        project_type="manipola",
        canvas=canvas,
    )

    set_output_contract(
        upstream_id,
        output_contract(
            [
                contract_column(
                    column_id=NEW_COL_ID,
                    source_name=CALC_SOURCE,
                    physical_name="calculated3",
                )
            ],
            producer_project_id=upstream_id,
        ),
    )

    ctx.update_canvas(downstream_id, canvas)
    synced = ctx.get_project(downstream_id)["canvas"]
    nodes = {node["id"]: node for node in synced["nodes"]}

    assert_equal(
        nodes["source-1"]["columns"][0]["id"],
        NEW_COL_ID,
        "project_source must adopt the current calculated column id",
    )
    assert_equal(
        nodes["source-1"]["columns"][0]["name"],
        "calculated3",
        "project_source must adopt the current calculated column name",
    )
    assert_equal(
        nodes["formula-2"]["config"]["formula"],
        "calculated3*4",
        "formula must follow upstream calculated column rename",
    )
    assert_equal(
        nodes["formula-2"]["config"]["formula_column_refs"],
        [{"id": NEW_COL_ID, "name": "calculated3"}],
        "formula refs must be migrated from old calculated column to current one",
    )


if __name__ == "__main__":
    run_test(test)
