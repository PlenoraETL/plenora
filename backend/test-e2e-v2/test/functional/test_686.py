"""test_686 - MANIPOLA consumes an upstream ESTRAI XLSX result.

Scenario covered:
- create and execute an ESTRAI project over fixture ``customers``;
- persist the upstream result as XLSX;
- consume that upstream result from a MANIPOLA ``project_source`` node;
- preview and execute a downstream transform chain.

This protects the project-source boundary where result blobs are materialized
without a file suffix: the loader must honor the persisted result format instead
of guessing CSV from the temporary path.
"""

from __future__ import annotations

from _e2e import (
    E2EContext,
    assert_equal,
    assert_true,
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
            result_node(
                format="xlsx",
                fileName="e2e_test_686_upstream_customers",
                order_by_enabled=True,
                order_by_columns=[{"column": "id", "direction": "ASC"}],
                data={
                    "order_by_enabled": True,
                    "order_by_columns": [{"column": "id", "direction": "ASC"}],
                },
            ),
        ],
        "edges": [edge("e-customers-result", "customers", "result-1")],
    }


def _manipola_canvas(source_project_id: str, contract: dict) -> dict:
    source = project_source_node_from_contract(
        node_id="source-1",
        source_project_id=source_project_id,
        contract=contract,
    )
    columns = source["columns"]
    age_column = next(column for column in columns if column["name"] == "age")
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={
            "formula": "age+1",
            "new_column": "next_age",
            "formula_column_refs": [{"id": age_column["id"], "name": "age"}],
        },
        input_columns=columns,
        position={"x": 260, "y": 0},
    )
    return {
        "nodes": [
            source,
            formula,
            result_node(format="csv", fileName="e2e_test_686_downstream_manipola"),
        ],
        "edges": [
            edge("e-source-formula", "source-1", "formula-1"),
            edge("e-formula-result", "formula-1", "result-1"),
        ],
    }


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_686_postgres"))
    )
    upstream_id = ctx.create_project(
        name="e2e_v2_test_686_upstream_estrai_xlsx",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_extract_canvas(connection_id),
    )
    ctx.execute_project(upstream_id)

    upstream = ctx.get_project(upstream_id)
    contract = upstream.get("output_contract") or {}
    assert_equal(
        [column["physical_name"] for column in contract.get("columns", [])],
        ["customer_id", "customer_name", "age", "status"],
        "Upstream ESTRAI XLSX result must expose the expected output_contract",
    )

    downstream_id = ctx.create_project(
        name="e2e_v2_test_686_downstream_manipola",
        project_type="manipola",
        canvas=_manipola_canvas(upstream_id, contract),
    )
    preview_rows = ctx.preview_project(downstream_id, limit=5).get("rows") or []
    assert_equal(
        [(row["customer_id"], row["customer_name"], row["age"], row["next_age"]) for row in preview_rows[:2]],
        [
            (1, "Anna Rossi", 34, 35),
            (2, "Marco Bianchi", 41, 42),
        ],
        "MANIPOLA preview must load the upstream XLSX result through project_source",
    )

    result = ctx.execute_project(downstream_id)
    assert_true(int(result.get("rows_count") or 0) > 0, "MANIPOLA execute must persist rows from XLSX project_source")


if __name__ == "__main__":
    run_test(test)
