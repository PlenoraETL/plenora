"""test_003 - ESTRAI single-table contract with deterministic fixture rows.

Scenario covered:
- create a real PostgreSQL connection to the E2E v2 fixture database;
- create an ESTRAI project reading ``customers``;
- apply a source WHERE filter, result ORDER BY, LIMIT, and visible aliases;
- preview through the real async API and verify exact rows from the seed;
- execute the project and verify the persisted output_contract uses the
  public physical names exchanged with downstream projects.

The script is launched from the host and cleans up projects/connections on
success.
"""

from __future__ import annotations

from _e2e import (
    E2EContext,
    assert_equal,
    edge,
    ensure_plenora_test_network_access,
    postgres_fixture_connection_payload,
    result_node,
    run_test,
    source_column,
    source_node,
    unique_name,
)


def _project_canvas(connection_id: str) -> dict:
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
                format="csv",
                fileName="e2e_test_003_customers",
                limit=5,
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


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_003_postgres"))
    )
    project_id = ctx.create_project(
        name="e2e_v2_test_003_estrai_single_table",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_project_canvas(connection_id),
    )

    preview = ctx.preview_project(project_id, limit=10)
    rows = preview.get("rows") or []

    assert_equal(
        [row["customer_id"] for row in rows],
        [1, 2, 4, 6, 7],
        "ESTRAI preview must honor filter, order and limit",
    )
    assert_equal(
        [row["customer_name"] for row in rows],
        [
            "Anna Rossi",
            "Marco Bianchi",
            "Paolo Ferrari",
            "Stefano Conti",
            "Elena Greco",
        ],
        "ESTRAI preview must expose aliased public names",
    )

    ctx.execute_project(project_id)
    project = ctx.get_project(project_id)
    contract = project.get("output_contract") or {}
    assert_equal(
        [column["physical_name"] for column in contract.get("columns", [])],
        ["customer_id", "customer_name", "age", "status"],
        "ESTRAI output_contract must persist public physical names",
    )


if __name__ == "__main__":
    run_test(test)
