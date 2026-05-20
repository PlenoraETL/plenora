"""test_007 - ESTRAI GROUP BY/HAVING/ORDER contract on products fixture.

Scenario covered:
- create a real PostgreSQL connection to the E2E v2 fixture database;
- create an ESTRAI project reading ``products``;
- use result-node GROUP BY aggregations with explicit aliases;
- verify deterministic grouped rows from the seed;
- execute and verify the output_contract exposes aggregate aliases.
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
                node_id="products",
                table_name="products",
                alias="p",
                connection_id=connection_id,
                columns=[
                    source_column("id", logical_type="int"),
                    source_column("category", logical_type="str"),
                    source_column("in_stock_count", logical_type="int"),
                ],
            ),
            result_node(
                format="csv",
                fileName="e2e_test_007_products_grouped",
                group_by_enabled=True,
                group_by_columns=["category"],
                aggregations=[
                    {"column": "id", "function": "COUNT", "alias": "product_count"},
                    {"column": "in_stock_count", "function": "SUM", "alias": "stock_sum"},
                ],
                order_by_enabled=True,
                order_by_columns=[{"column": "category", "direction": "ASC"}],
                data={
                    "group_by_enabled": True,
                    "group_by_columns": ["category"],
                    "aggregations": [
                        {"column": "id", "function": "COUNT", "alias": "product_count"},
                        {"column": "in_stock_count", "function": "SUM", "alias": "stock_sum"},
                    ],
                    "order_by_enabled": True,
                    "order_by_columns": [{"column": "category", "direction": "ASC"}],
                },
            ),
        ],
        "edges": [edge("e-products-result", "products", "result-1")],
    }


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_007_postgres"))
    )
    project_id = ctx.create_project(
        name="e2e_v2_test_007_estrai_groupby",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_project_canvas(connection_id),
    )

    preview = ctx.preview_project(project_id, limit=20)
    rows = preview.get("rows") or []
    assert_equal(
        rows,
        [
            {"category": "BOOKS", "product_count": 1, "stock_sum": 10},
            {"category": "CLOTHING", "product_count": 4, "stock_sum": 455},
            {"category": "ELECTRONICS", "product_count": 3, "stock_sum": 270},
            {"category": "FOOD", "product_count": 5, "stock_sum": 1602},
        ],
        "ESTRAI GROUP BY preview must match deterministic product seed",
    )

    ctx.execute_project(project_id)
    contract = (ctx.get_project(project_id).get("output_contract") or {}).get("columns", [])
    assert_equal(
        [column["physical_name"] for column in contract],
        ["category", "product_count", "stock_sum"],
        "ESTRAI GROUP BY output_contract must expose aggregate aliases",
    )


if __name__ == "__main__":
    run_test(test)
