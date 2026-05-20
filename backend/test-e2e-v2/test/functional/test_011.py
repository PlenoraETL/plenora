"""test_011 - MANIPOLA execute persists output_contract after reshape chain.

Scenario covered:
- execute an ESTRAI upstream over active fixture customers;
- create MANIPOLA downstream with formula, rename and drop_columns;
- execute downstream;
- verify downstream output_contract contains the transformed public schema.
"""

from __future__ import annotations

from _e2e import (
    E2EContext,
    assert_equal,
    edge,
    ensure_plenora_test_network_access,
    postgres_fixture_connection_payload,
    project_source_node_from_contract,
    result_node,
    run_test,
    source_column,
    source_node,
    transform_node,
    unique_name,
)


def _extract_canvas(connection_id: str) -> dict:
    return {
        "nodes": [
            source_node(
                node_id="customers",
                table_name="customers",
                alias="c",
                connection_id=connection_id,
                columns=[
                    source_column("id", logical_type="int", alias="customer_id"),
                    source_column("full_name", logical_type="str", alias="customer_name"),
                    source_column("age", logical_type="int"),
                    source_column("status", logical_type="str"),
                ],
                filters=[{"column": "status", "operator": "=", "value": "active"}],
            ),
            result_node(format="csv", fileName="e2e_test_011_upstream"),
        ],
        "edges": [edge("e-customers-result", "customers", "result-1")],
    }


def _manipola_canvas(upstream_id: str, contract: dict) -> dict:
    source = project_source_node_from_contract(
        node_id="source-1",
        source_project_id=upstream_id,
        contract=contract,
    )
    columns = source["columns"]
    age_col = next(column for column in columns if column["name"] == "age")
    name_col = next(column for column in columns if column["name"] == "customer_name")
    status_col = next(column for column in columns if column["name"] == "status")
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={
            "formula": "age*2",
            "new_column": "double_age",
            "formula_column_refs": [{"id": age_col["id"], "name": "age"}],
        },
        input_columns=columns,
        position={"x": 240, "y": 0},
    )
    rename = transform_node(
        node_id="rename-1",
        transform_op="rename",
        config={
            "renames": [
                {"old_name": "customer_name", "old_name_id": name_col["id"], "new_name": "name"}
            ]
        },
        position={"x": 480, "y": 0},
    )
    drop = transform_node(
        node_id="drop-1",
        transform_op="drop_columns",
        config={"columns": ["status"], "columns_column_ids": [status_col["id"]]},
        position={"x": 720, "y": 0},
    )
    return {
        "nodes": [source, formula, rename, drop, result_node(format="csv", fileName="e2e_test_011_downstream")],
        "edges": [
            edge("e-source-formula", "source-1", "formula-1"),
            edge("e-formula-rename", "formula-1", "rename-1"),
            edge("e-rename-drop", "rename-1", "drop-1"),
            edge("e-drop-result", "drop-1", "result-1"),
        ],
    }


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_011_postgres"))
    )
    upstream_id = ctx.create_project(
        name="e2e_v2_test_011_upstream_estrai",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_extract_canvas(connection_id),
    )
    ctx.execute_project(upstream_id)
    contract = ctx.get_project(upstream_id).get("output_contract") or {}

    downstream_id = ctx.create_project(
        name="e2e_v2_test_011_downstream_contract",
        project_type="manipola",
        canvas=_manipola_canvas(upstream_id, contract),
    )
    ctx.execute_project(downstream_id)
    downstream_contract = (ctx.get_project(downstream_id).get("output_contract") or {}).get("columns", [])

    assert_equal(
        [column["physical_name"] for column in downstream_contract],
        ["customer_id", "name", "age", "double_age"],
        "MANIPOLA output_contract must reflect formula, rename and drop_columns",
    )


if __name__ == "__main__":
    run_test(test)
