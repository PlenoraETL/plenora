"""test_013 - ESTRAI attribute join types.

Scenario covered:
- real PostgreSQL fixture connection;
- ESTRAI joins between ``customers`` and ``cities``;
- INNER, LEFT, RIGHT and FULL/OUTER join semantics;
- deterministic row counts from fixture data.
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


EXPECTED_COUNTS = {
    "inner": 20,
    "left": 22,
    "right": 32,
    "full": 34,
}


def _join_canvas(connection_id: str, join_type: str) -> dict:
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
                ],
                position={"x": 0, "y": 180},
            ),
            result_node(format="csv", fileName=f"e2e_test_013_{join_type}"),
        ],
        "edges": [
            {
                "id": f"e-customers-cities-{join_type}",
                "source": "customers",
                "target": "cities",
                "join_type": join_type,
                "source_column": "city_id",
                "target_column": "id",
            },
            edge("e-cities-result", "cities", "result-1"),
        ],
    }


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_013_postgres"))
    )

    actual_counts = {}
    null_city_rows = {}
    null_customer_rows = {}
    for join_type in ("inner", "left", "right", "full"):
        project_id = ctx.create_project(
            name=f"e2e_v2_test_013_join_{join_type}",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=_join_canvas(connection_id, join_type),
        )
        rows = ctx.preview_project(project_id, limit=100).get("rows") or []
        actual_counts[join_type] = len(rows)
        null_city_rows[join_type] = sum(1 for row in rows if row.get("city_name") is None)
        null_customer_rows[join_type] = sum(1 for row in rows if row.get("customer_id") is None)

    assert_equal(
        actual_counts,
        EXPECTED_COUNTS,
        "ESTRAI attribute join types must return deterministic row counts",
    )
    assert_equal(
        null_city_rows,
        {"inner": 0, "left": 2, "right": 0, "full": 2},
        "LEFT/FULL joins must preserve customers without city",
    )
    assert_equal(
        null_customer_rows,
        {"inner": 0, "left": 0, "right": 12, "full": 12},
        "RIGHT/FULL joins must preserve cities without customers",
    )


if __name__ == "__main__":
    run_test(test)
