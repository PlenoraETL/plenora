"""test_014 - ESTRAI explicit CROSS JOIN and ON filters.

Scenario covered:
- explicit non-spatial CROSS JOIN, avoiding accidental cartesian products;
- attribute join with edge-level ON filter;
- deterministic row counts from fixture ``regions``, ``products`` and
  ``order_items``.
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


def _cross_canvas(connection_id: str) -> dict:
    return {
        "nodes": [
            source_node(
                node_id="regions",
                table_name="regions",
                alias="r",
                connection_id=connection_id,
                columns=[source_column("code", logical_type="str", alias="region_code")],
                position={"x": 0, "y": 0},
            ),
            source_node(
                node_id="products",
                table_name="products",
                alias="p",
                connection_id=connection_id,
                columns=[source_column("code", logical_type="str", alias="product_code")],
                position={"x": 0, "y": 180},
            ),
            result_node(format="csv", fileName="e2e_test_014_cross"),
        ],
        "edges": [
            {
                "id": "e-regions-products-cross",
                "source": "regions",
                "target": "products",
                "join_type": "cross",
            },
            edge("e-products-result", "products", "result-1"),
        ],
    }


def _on_filter_canvas(connection_id: str) -> dict:
    return {
        "nodes": [
            source_node(
                node_id="order_items",
                table_name="order_items",
                alias="oi",
                connection_id=connection_id,
                columns=[
                    source_column("id", logical_type="int", alias="item_id"),
                    source_column("product_id", logical_type="int"),
                    source_column("qty", logical_type="int"),
                ],
                position={"x": 0, "y": 0},
            ),
            source_node(
                node_id="products",
                table_name="products",
                alias="p",
                connection_id=connection_id,
                columns=[
                    source_column("id", logical_type="int", visible=False),
                    source_column("category", logical_type="str"),
                    source_column("code", logical_type="str", alias="product_code"),
                ],
                position={"x": 0, "y": 180},
            ),
            result_node(format="csv", fileName="e2e_test_014_on_filter"),
        ],
        "edges": [
            {
                "id": "e-items-products-food",
                "source": "order_items",
                "target": "products",
                "join_type": "inner",
                "source_column": "product_id",
                "target_column": "id",
                "on_filters": [{"column": "category", "operator": "=", "value": "FOOD"}],
            },
            edge("e-products-result", "products", "result-1"),
        ],
    }


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_014_postgres"))
    )

    cross_id = ctx.create_project(
        name="e2e_v2_test_014_cross_join",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_cross_canvas(connection_id),
    )
    cross_rows = ctx.preview_project(cross_id, limit=100).get("rows") or []
    assert_equal(len(cross_rows), 52, "CROSS JOIN must produce 4 regions x 13 products")

    filtered_id = ctx.create_project(
        name="e2e_v2_test_014_on_filter_join",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_on_filter_canvas(connection_id),
    )
    filtered_rows = ctx.preview_project(filtered_id, limit=100).get("rows") or []
    assert_equal(len(filtered_rows), 12, "ON filter must keep only order_items joined to FOOD products")
    assert_equal(
        sorted({row["product_code"] for row in filtered_rows}),
        ["FOO-001", "FOO-002", "FOO-VOID"],
        "ON filter join must only expose FOOD product codes present in order_items",
    )


if __name__ == "__main__":
    run_test(test)
