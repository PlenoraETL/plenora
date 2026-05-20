"""Cross-database ESTRAI smoke matrix for MySQL, SQL Server and Oracle."""

from __future__ import annotations

import os
from typing import Any, Callable

from _e2e import (
    E2EContext,
    assert_equal,
    assert_true,
    edge,
    ensure_plenora_test_network_access,
    mssql_fixture_connection_payload,
    mysql_fixture_connection_payload,
    oracle_fixture_connection_payload,
    result_node,
    run_test,
    source_column,
    source_node,
    unique_name,
)


PayloadFactory = Callable[[str], dict[str, Any]]


DIALECTS: dict[str, PayloadFactory] = {
    "mysql": mysql_fixture_connection_payload,
    "mssql": mssql_fixture_connection_payload,
    "oracle": oracle_fixture_connection_payload,
}


def _cross_db_enabled(dialect: str) -> bool:
    if os.environ.get("PLENORA_E2E_CROSS_DB", "").lower() in {"1", "true", "yes", "all"}:
        return True
    enabled = {
        item.strip().lower()
        for item in os.environ.get("PLENORA_E2E_CROSS_DB_DIALECTS", "").split(",")
        if item.strip()
    }
    return dialect in enabled


def _single_table_canvas(connection_id: str, dialect: str) -> dict[str, Any]:
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
                fileName=f"e2e_cross_db_{dialect}_customers",
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


def _join_canvas(connection_id: str, dialect: str) -> dict[str, Any]:
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
                fileName=f"e2e_cross_db_{dialect}_join",
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


def _run_dialect(ctx: E2EContext, dialect: str) -> None:
    if not _cross_db_enabled(dialect):
        print(
            f"SKIP cross-db {dialect}: set PLENORA_E2E_CROSS_DB=1 "
            f"or PLENORA_E2E_CROSS_DB_DIALECTS={dialect}",
            flush=True,
        )
        return

    ensure_plenora_test_network_access()
    payload_factory = DIALECTS[dialect]
    connection_id = ctx.create_connection(
        payload_factory(unique_name(f"e2e_v2_cross_db_{dialect}"))
    )

    connection_test = ctx.test_connection(connection_id)
    assert_true(
        connection_test.get("success"),
        f"{dialect} connection test must succeed: {connection_test!r}",
    )

    single_project_id = ctx.create_project(
        name=f"e2e_v2_cross_db_{dialect}_single_table",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_single_table_canvas(connection_id, dialect),
    )
    single_rows = ctx.preview_project(single_project_id, limit=10).get("rows") or []
    assert_equal(
        [row["customer_id"] for row in single_rows],
        [1, 2, 4, 6, 7],
        f"{dialect}: single-table preview must honor filter/order/limit",
    )
    assert_equal(
        [row["customer_name"] for row in single_rows],
        ["Anna Rossi", "Marco Bianchi", "Paolo Ferrari", "Stefano Conti", "Elena Greco"],
        f"{dialect}: single-table preview must expose aliases",
    )

    ctx.execute_project(single_project_id)
    single_contract = (ctx.get_project(single_project_id).get("output_contract") or {}).get("columns", [])
    assert_equal(
        [column["physical_name"] for column in single_contract],
        ["customer_id", "customer_name", "age", "status"],
        f"{dialect}: single-table output_contract must expose public columns",
    )

    join_project_id = ctx.create_project(
        name=f"e2e_v2_cross_db_{dialect}_join",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_join_canvas(connection_id, dialect),
    )
    join_rows = ctx.preview_project(join_project_id, limit=20).get("rows") or []
    assert_equal(len(join_rows), 12, f"{dialect}: join preview must honor result limit")
    assert_equal(
        [(row["customer_id"], row["customer_name"], row.get("city_name")) for row in join_rows[:5]],
        [
            (1, "Anna Rossi", "Milano"),
            (2, "Marco Bianchi", "Milano"),
            (3, "Giulia Verdi", "Milano"),
            (4, "Paolo Ferrari", "Brescia"),
            (5, "Laura Romano", "Roma"),
        ],
        f"{dialect}: join preview must return deterministic rows",
    )
    assert_equal(join_rows[-1]["customer_id"], 12, f"{dialect}: LEFT JOIN must keep unmatched row")
    assert_equal(join_rows[-1].get("city_name"), None, f"{dialect}: unmatched city must be NULL")


def run_dialect(dialect: str) -> None:
    if not _cross_db_enabled(dialect):
        print(
            f"SKIP cross-db {dialect}: set PLENORA_E2E_CROSS_DB=1 "
            f"or PLENORA_E2E_CROSS_DB_DIALECTS={dialect}",
            flush=True,
        )
        return
    run_test(lambda ctx: _run_dialect(ctx, dialect))
