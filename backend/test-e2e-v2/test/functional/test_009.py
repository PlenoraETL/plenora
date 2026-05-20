"""test_009 - MANIPOLA multi-project join contract.

Scenario covered:
- execute two ESTRAI upstream projects: customers and cities;
- consume both output_contracts in MANIPOLA project_source nodes;
- join the two upstream project outputs through the real Manipola preview path;
- verify deterministic joined rows and output column suffix behavior.
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


def _customers_canvas(connection_id: str) -> dict:
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
            ),
            result_node(format="csv", fileName="e2e_test_009_customers"),
        ],
        "edges": [edge("e-customers-result", "customers", "result-1")],
    }


def _cities_canvas(connection_id: str) -> dict:
    return {
        "nodes": [
            source_node(
                node_id="cities",
                table_name="cities",
                alias="ci",
                connection_id=connection_id,
                columns=[
                    source_column("id", logical_type="int", alias="city_id"),
                    source_column("name", logical_type="str", alias="city_name"),
                ],
            ),
            result_node(format="csv", fileName="e2e_test_009_cities"),
        ],
        "edges": [edge("e-cities-result", "cities", "result-1")],
    }


def _join_canvas(customers_id: str, customers_contract: dict, cities_id: str, cities_contract: dict) -> dict:
    customers = project_source_node_from_contract(
        node_id="customers-source",
        source_project_id=customers_id,
        contract=customers_contract,
        position={"x": 0, "y": 0},
    )
    cities = project_source_node_from_contract(
        node_id="cities-source",
        source_project_id=cities_id,
        contract=cities_contract,
        position={"x": 0, "y": 180},
    )
    left_key = next(column for column in customers["columns"] if column["name"] == "city_id")
    right_key = next(column for column in cities["columns"] if column["name"] == "city_id")
    join = transform_node(
        node_id="join-1",
        transform_op="join",
        config={
            "left_on": "city_id",
            "left_on_column_id": left_key["id"],
            "right_on": "city_id",
            "right_on_column_id": right_key["id"],
            "how": "left",
        },
        position={"x": 360, "y": 80},
        input_columns=customers["columns"] + cities["columns"],
    )
    return {
        "nodes": [customers, cities, join, result_node(format="csv", fileName="e2e_test_009_join")],
        "edges": [
            edge("e-customers-join", "customers-source", "join-1"),
            edge("e-cities-join", "cities-source", "join-1"),
            edge("e-join-result", "join-1", "result-1"),
        ],
    }


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_009_postgres"))
    )
    customers_id = ctx.create_project(
        name="e2e_v2_test_009_upstream_customers",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_customers_canvas(connection_id),
    )
    cities_id = ctx.create_project(
        name="e2e_v2_test_009_upstream_cities",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_cities_canvas(connection_id),
    )
    ctx.execute_project(customers_id)
    ctx.execute_project(cities_id)
    customers_contract = ctx.get_project(customers_id).get("output_contract") or {}
    cities_contract = ctx.get_project(cities_id).get("output_contract") or {}

    downstream_id = ctx.create_project(
        name="e2e_v2_test_009_downstream_join",
        project_type="manipola",
        canvas=_join_canvas(customers_id, customers_contract, cities_id, cities_contract),
    )
    rows = ctx.preview_project(downstream_id, limit=5).get("rows") or []

    assert_equal(
        [
            (row["customer_id_L"], row["customer_name_L"], row.get("city_name_R"))
            for row in rows[:5]
        ],
        [
            (1, "Anna Rossi", "Milano"),
            (2, "Marco Bianchi", "Milano"),
            (3, "Giulia Verdi", "Milano"),
            (4, "Paolo Ferrari", "Brescia"),
            (5, "Laura Romano", "Roma"),
        ],
        "MANIPOLA join must combine two upstream project outputs deterministically",
    )


if __name__ == "__main__":
    run_test(test)
