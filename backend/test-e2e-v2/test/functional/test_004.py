"""test_004 - ESTRAI join contract with expected fixture rows.

Scenario covered:
- create a real PostgreSQL connection to the E2E v2 fixture database;
- create an ESTRAI project joining ``customers`` to ``cities``;
- verify LEFT JOIN behavior, aliases, ordering and NULL propagation through
  the preview API;
- execute and verify the output_contract column boundary.
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
                    source_column("city_id", logical_type="int"),
                    source_column("full_name", logical_type="str", alias="customer_name"),
                ],
                position={"x": 0, "y": 0},
            ),
            source_node(
                node_id="cities",
                table_name="cities",
                alias="ci",
                connection_id=connection_id,
                columns=[
                    source_column("id", logical_type="int", visible=False),
                    source_column("name", logical_type="str", alias="city_name"),
                    source_column("region_id", logical_type="int", visible=False),
                ],
                position={"x": 0, "y": 180},
            ),
            result_node(
                format="csv",
                fileName="e2e_test_004_join",
                limit=12,
                order_by_enabled=True,
                order_by_columns=[{"column": "id", "direction": "ASC"}],
                data={
                    "order_by_enabled": True,
                    "order_by_columns": [{"column": "id", "direction": "ASC"}],
                },
            ),
        ],
        "edges": [
            {
                "id": "e-customers-cities",
                "source": "customers",
                "target": "cities",
                "join_type": "left",
                "source_column": "city_id",
                "target_column": "id",
            },
            edge("e-cities-result", "cities", "result-1"),
        ],
    }


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_004_postgres"))
    )
    project_id = ctx.create_project(
        name="e2e_v2_test_004_estrai_join",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_project_canvas(connection_id),
    )

    preview = ctx.preview_project(project_id, limit=20)
    rows = preview.get("rows") or []

    assert_equal(len(rows), 12, "ESTRAI join preview must honor result LIMIT")
    assert_equal(
        [(row["customer_id"], row["customer_name"], row.get("city_name")) for row in rows[:5]],
        [
            (1, "Anna Rossi", "Milano"),
            (2, "Marco Bianchi", "Milano"),
            (3, "Giulia Verdi", "Milano"),
            (4, "Paolo Ferrari", "Brescia"),
            (5, "Laura Romano", "Roma"),
        ],
        "ESTRAI join preview must return deterministic joined rows",
    )
    assert_equal(
        rows[-1]["customer_id"],
        12,
        "ESTRAI join preview must include the customer with NULL city_id",
    )
    assert_equal(
        rows[-1].get("city_name"),
        None,
        "LEFT JOIN must preserve unmatched customer row with NULL city_name",
    )

    ctx.execute_project(project_id)
    contract = (ctx.get_project(project_id).get("output_contract") or {}).get("columns", [])
    assert_equal(
        [column["physical_name"] for column in contract],
        ["customer_id", "city_id", "customer_name", "city_name"],
        "ESTRAI join output_contract must expose only visible public columns",
    )


if __name__ == "__main__":
    run_test(test)
