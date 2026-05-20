"""test_005 - ESTRAI -> MANIPOLA execution contract with expected rows.

Scenario covered:
- create and execute an ESTRAI upstream project over fixture ``customers``;
- consume its persisted output_contract from a MANIPOLA project_source;
- run a transform chain using stable column refs:
  ``formula(age * 2) -> filter(age >= 40)``;
- preview through the real async API and verify exact rows from the seed.

This checks the project boundary that matters in production: downstream
Manipola receives data plus output_contract from upstream EstrAi.
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
            result_node(
                format="csv",
                fileName="e2e_test_005_upstream_customers",
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
            "formula": "age*2",
            "new_column": "double_age",
            "formula_column_refs": [{"id": age_column["id"], "name": "age"}],
        },
        position={"x": 260, "y": 0},
        input_columns=columns,
    )
    filter_node = transform_node(
        node_id="filter-1",
        transform_op="filter",
        config={
            "column": "age",
            "column_column_id": age_column["id"],
            "operator": ">=",
            "value": "40",
        },
        position={"x": 520, "y": 0},
    )
    return {
        "nodes": [
            source,
            formula,
            filter_node,
            result_node(format="csv", fileName="e2e_test_005_manipola"),
        ],
        "edges": [
            edge("e-source-formula", "source-1", "formula-1"),
            edge("e-formula-filter", "formula-1", "filter-1"),
            edge("e-filter-result", "filter-1", "result-1"),
        ],
    }


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_005_postgres"))
    )
    upstream_id = ctx.create_project(
        name="e2e_v2_test_005_upstream_estrai",
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
        "Upstream ESTRAI must expose the expected output_contract",
    )

    downstream_id = ctx.create_project(
        name="e2e_v2_test_005_downstream_manipola",
        project_type="manipola",
        canvas=_manipola_canvas(upstream_id, contract),
    )
    preview = ctx.preview_project(downstream_id, limit=20)
    rows = preview.get("rows") or []

    assert_equal(
        [(row["customer_id"], row["customer_name"], row["age"], row["double_age"]) for row in rows],
        [
            (2, "Marco Bianchi", 41, 82),
            (4, "Paolo Ferrari", 52, 104),
            (12, "Roberto Costa", 55, 110),
            (14, "M\u00fcller Hans", 44, 88),
        ],
        "MANIPOLA must consume ESTRAI data/contract and produce expected transform rows",
    )


if __name__ == "__main__":
    run_test(test)
