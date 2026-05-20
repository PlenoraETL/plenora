"""test_008 - ESTRAI -> MANIPOLA aggregate execution with expected counts.

Scenario covered:
- execute an ESTRAI upstream over all fixture customers;
- consume its output_contract in a MANIPOLA project_source;
- aggregate by ``status`` in Manipola;
- verify exact status counts from the deterministic seed.
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
                    source_column("status", logical_type="str"),
                ],
            ),
            result_node(format="csv", fileName="e2e_test_008_customers"),
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
    status_col = next(column for column in columns if column["name"] == "status")
    id_col = next(column for column in columns if column["name"] == "customer_id")
    aggregate = transform_node(
        node_id="aggregate-1",
        transform_op="aggregate",
        config={
            "group_by": ["status"],
            "group_by_column_ids": [status_col["id"]],
            "aggregations": [
                {"column": "customer_id", "column_id": id_col["id"], "function": "count", "alias": "customer_count"}
            ],
        },
        input_columns=columns,
    )
    return {
        "nodes": [source, aggregate, result_node(format="csv", fileName="e2e_test_008_aggregate")],
        "edges": [
            edge("e-source-aggregate", "source-1", "aggregate-1"),
            edge("e-aggregate-result", "aggregate-1", "result-1"),
        ],
    }


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_008_postgres"))
    )
    upstream_id = ctx.create_project(
        name="e2e_v2_test_008_upstream_customers",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_extract_canvas(connection_id),
    )
    ctx.execute_project(upstream_id)
    contract = ctx.get_project(upstream_id).get("output_contract") or {}

    downstream_id = ctx.create_project(
        name="e2e_v2_test_008_downstream_aggregate",
        project_type="manipola",
        canvas=_manipola_canvas(upstream_id, contract),
    )
    rows = ctx.preview_project(downstream_id, limit=20).get("rows") or []
    by_status = {row["status"]: row["customer_count"] for row in rows}

    assert_equal(
        by_status,
        {None: 1, "active": 17, "inactive": 2, "pending": 2},
        "MANIPOLA aggregate must produce exact fixture status counts",
    )


if __name__ == "__main__":
    run_test(test)
