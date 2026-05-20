"""Extended cross-database ESTRAI/MANIPOLA scenarios."""

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
    project_source_node_from_contract,
    result_node,
    run_test,
    source_column,
    source_node,
    transform_node,
    unique_name,
)


PayloadFactory = Callable[[str], dict[str, Any]]


DIALECTS: dict[str, PayloadFactory] = {
    "mysql": mysql_fixture_connection_payload,
    "mssql": mssql_fixture_connection_payload,
    "oracle": oracle_fixture_connection_payload,
}


def _enabled(dialect: str) -> bool:
    if os.environ.get("PLENORA_E2E_CROSS_DB", "").lower() in {"1", "true", "yes", "all"}:
        return True
    enabled = {
        item.strip().lower()
        for item in os.environ.get("PLENORA_E2E_CROSS_DB_DIALECTS", "").split(",")
        if item.strip()
    }
    return dialect in enabled


def _connection(ctx: E2EContext, dialect: str, scenario: str) -> str:
    payload = DIALECTS[dialect](unique_name(f"e2e_v2_{scenario}_{dialect}"))
    connection_id = ctx.create_connection(payload)
    tested = ctx.test_connection(connection_id)
    assert_true(tested.get("success"), f"{dialect} connection test must succeed: {tested!r}")
    return connection_id


def _groupby_canvas(connection_id: str, dialect: str) -> dict[str, Any]:
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
                fileName=f"e2e_cross_db_{dialect}_groupby",
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


def scenario_groupby(ctx: E2EContext, dialect: str) -> None:
    connection_id = _connection(ctx, dialect, "groupby")
    project_id = ctx.create_project(
        name=f"e2e_v2_cross_db_{dialect}_groupby",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_groupby_canvas(connection_id, dialect),
    )
    rows = ctx.preview_project(project_id, limit=20).get("rows") or []
    comparable_rows = [
        {**row, "stock_sum": int(row["stock_sum"])}
        for row in rows
    ]
    assert_equal(
        comparable_rows,
        [
            {"category": "BOOKS", "product_count": 1, "stock_sum": 10},
            {"category": "CLOTHING", "product_count": 4, "stock_sum": 455},
            {"category": "ELECTRONICS", "product_count": 3, "stock_sum": 270},
            {"category": "FOOD", "product_count": 5, "stock_sum": 1602},
        ],
        f"{dialect}: GROUP BY/aggregations must match fixture",
    )
    ctx.execute_project(project_id)
    contract = (ctx.get_project(project_id).get("output_contract") or {}).get("columns", [])
    assert_equal(
        [column["physical_name"] for column in contract],
        ["category", "product_count", "stock_sum"],
        f"{dialect}: GROUP BY output_contract must expose aggregate aliases",
    )


def _cross_canvas(connection_id: str, dialect: str) -> dict[str, Any]:
    return {
        "nodes": [
            source_node(
                node_id="regions",
                table_name="regions",
                alias="r",
                connection_id=connection_id,
                columns=[source_column("code", logical_type="str", alias="region_code")],
            ),
            source_node(
                node_id="products",
                table_name="products",
                alias="p",
                connection_id=connection_id,
                columns=[source_column("code", logical_type="str", alias="product_code")],
                position={"x": 0, "y": 180},
            ),
            result_node(format="csv", fileName=f"e2e_cross_db_{dialect}_cross"),
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


def _on_filter_canvas(connection_id: str, dialect: str) -> dict[str, Any]:
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
            result_node(format="csv", fileName=f"e2e_cross_db_{dialect}_on_filter"),
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


def scenario_cross_and_on_filter(ctx: E2EContext, dialect: str) -> None:
    connection_id = _connection(ctx, dialect, "joins")
    cross_id = ctx.create_project(
        name=f"e2e_v2_cross_db_{dialect}_cross_join",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_cross_canvas(connection_id, dialect),
    )
    assert_equal(
        len(ctx.preview_project(cross_id, limit=100).get("rows") or []),
        52,
        f"{dialect}: CROSS JOIN must produce 4 regions x 13 products",
    )

    filtered_id = ctx.create_project(
        name=f"e2e_v2_cross_db_{dialect}_on_filter",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_on_filter_canvas(connection_id, dialect),
    )
    rows = ctx.preview_project(filtered_id, limit=100).get("rows") or []
    assert_equal(len(rows), 12, f"{dialect}: ON filter must keep FOOD order_items")
    assert_equal(
        sorted({row["product_code"] for row in rows}),
        ["FOO-001", "FOO-002", "FOO-VOID"],
        f"{dialect}: ON filter join must expose only FOOD product codes",
    )


def _edge_case_canvas(connection_id: str, dialect: str) -> dict[str, Any]:
    return {
        "nodes": [
            source_node(
                node_id="customers",
                table_name="customers",
                alias="c",
                connection_id=connection_id,
                columns=[
                    source_column("id", logical_type="int"),
                    source_column("full_name", logical_type="str"),
                    source_column("email", logical_type="str"),
                    source_column("age", logical_type="int"),
                    source_column("signup_date", logical_type="date"),
                    source_column("status", logical_type="str"),
                ],
            ),
            result_node(
                format="csv",
                fileName=f"e2e_cross_db_{dialect}_edge_cases",
                limit=22,
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


def scenario_edge_cases(ctx: E2EContext, dialect: str) -> None:
    connection_id = _connection(ctx, dialect, "edge_cases")
    project_id = ctx.create_project(
        name=f"e2e_v2_cross_db_{dialect}_edge_cases",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_edge_case_canvas(connection_id, dialect),
    )
    rows = ctx.preview_project(project_id, limit=30).get("rows") or []
    by_id = {row["id"]: row for row in rows}
    assert_equal(len(rows), 22, f"{dialect}: edge-case preview must include all customers")
    assert_equal(by_id[13]["full_name"], "O'Brien Patrick", f"{dialect}: apostrophe text must roundtrip")
    assert_equal(by_id[20]["status"], None if dialect == "oracle" else "", f"{dialect}: empty status semantics")
    assert_equal(by_id[21]["age"], None, f"{dialect}: NULL numeric value must roundtrip")
    assert_equal(by_id[22]["email"], None, f"{dialect}: NULL string value must roundtrip")

    ctx.execute_project(project_id)
    contract = (ctx.get_project(project_id).get("output_contract") or {}).get("columns", [])
    assert_equal(
        [column["physical_name"] for column in contract],
        ["id", "full_name", "email", "age", "signup_date", "status"],
        f"{dialect}: edge-case output_contract must preserve visible column order",
    )


def _spatial_contains_canvas(connection_id: str, dialect: str) -> dict[str, Any]:
    return {
        "nodes": [
            source_node(
                node_id="regions",
                table_name="regions",
                alias="r",
                connection_id=connection_id,
                columns=[
                    source_column("code", logical_type="str", alias="region_code"),
                    source_column("boundary", logical_type="geometry:multipolygon", visible=False),
                ],
            ),
            source_node(
                node_id="cities",
                table_name="cities",
                alias="c",
                connection_id=connection_id,
                columns=[
                    source_column("name", logical_type="str", alias="city_name"),
                    source_column("geom", logical_type="geometry:point", visible=False),
                ],
                position={"x": 0, "y": 180},
            ),
            result_node(format="csv", fileName=f"e2e_cross_db_{dialect}_spatial_contains"),
        ],
        "edges": [
            {
                "id": "e-regions-cities-contains",
                "source": "regions",
                "target": "cities",
                "join_type": "inner",
                "is_spatial": True,
                "spatial_predicate": "ST_Contains",
                "source_geom_column": "boundary",
                "target_geom_column": "geom",
            },
            edge("e-cities-result", "cities", "result-1"),
        ],
    }


def _spatial_empty_canvas(connection_id: str, dialect: str, predicate: str) -> dict[str, Any]:
    return {
        "nodes": [
            source_node(
                node_id="cities",
                table_name="cities",
                alias="c",
                connection_id=connection_id,
                columns=[
                    source_column("name", logical_type="str", alias="city_name"),
                    source_column("geom", logical_type="geometry:point", visible=False),
                ],
            ),
            source_node(
                node_id="regions",
                table_name="regions",
                alias="r",
                connection_id=connection_id,
                columns=[
                    source_column("code", logical_type="str", alias="region_code"),
                    source_column("boundary", logical_type="geometry:multipolygon", visible=False),
                ],
                position={"x": 0, "y": 180},
            ),
            result_node(format="csv", fileName=f"e2e_cross_db_{dialect}_{predicate.lower()}"),
        ],
        "edges": [
            {
                "id": f"e-cities-regions-{predicate}",
                "source": "cities",
                "target": "regions",
                "join_type": "inner",
                "is_spatial": True,
                "spatial_predicate": predicate,
                "source_geom_column": "geom",
                "target_geom_column": "boundary",
            },
            edge("e-regions-result", "regions", "result-1"),
        ],
    }


def scenario_spatial(ctx: E2EContext, dialect: str) -> None:
    connection_id = _connection(ctx, dialect, "spatial")
    contains_id = ctx.create_project(
        name=f"e2e_v2_cross_db_{dialect}_spatial_contains",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_spatial_contains_canvas(connection_id, dialect),
    )
    contains_rows = ctx.preview_project(contains_id, limit=100).get("rows") or []
    assert_equal(len(contains_rows), 14, f"{dialect}: ST_Contains region->city must match fixture")

    for predicate in ("ST_Touches", "ST_Overlaps"):
        project_id = ctx.create_project(
            name=f"e2e_v2_cross_db_{dialect}_{predicate.lower()}",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=_spatial_empty_canvas(connection_id, dialect, predicate),
        )
        rows = ctx.preview_project(project_id, limit=100).get("rows") or []
        assert_equal(len(rows), 0, f"{dialect}: {predicate} point/polygon join must be empty")


def _extract_canvas(connection_id: str, dialect: str) -> dict[str, Any]:
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
                fileName=f"e2e_cross_db_{dialect}_upstream_customers",
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


def _manipola_canvas(source_project_id: str, contract: dict[str, Any], dialect: str) -> dict[str, Any]:
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
            result_node(format="csv", fileName=f"e2e_cross_db_{dialect}_manipola"),
        ],
        "edges": [
            edge("e-source-formula", "source-1", "formula-1"),
            edge("e-formula-filter", "formula-1", "filter-1"),
            edge("e-filter-result", "filter-1", "result-1"),
        ],
    }


def scenario_manipola_chain(ctx: E2EContext, dialect: str) -> None:
    connection_id = _connection(ctx, dialect, "manipola_chain")
    upstream_id = ctx.create_project(
        name=f"e2e_v2_cross_db_{dialect}_upstream",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_extract_canvas(connection_id, dialect),
    )
    ctx.execute_project(upstream_id)
    contract = ctx.get_project(upstream_id).get("output_contract") or {}
    assert_equal(
        [column["physical_name"] for column in contract.get("columns", [])],
        ["customer_id", "customer_name", "age", "status"],
        f"{dialect}: upstream output_contract must expose expected columns",
    )

    downstream_id = ctx.create_project(
        name=f"e2e_v2_cross_db_{dialect}_manipola",
        project_type="manipola",
        canvas=_manipola_canvas(upstream_id, contract, dialect),
    )
    rows = ctx.preview_project(downstream_id, limit=20).get("rows") or []
    actual = [(row["customer_id"], row["customer_name"], row["age"], row["double_age"]) for row in rows]
    assert_true((2, "Marco Bianchi", 41, 82) in actual, f"{dialect}: manipola must compute formula")
    assert_true((4, "Paolo Ferrari", 52, 104) in actual, f"{dialect}: manipola must keep filtered rows")
    assert_true(all(row[2] >= 40 for row in actual), f"{dialect}: manipola filter must apply after formula")


SCENARIOS: dict[str, Callable[[E2EContext, str], None]] = {
    "groupby": scenario_groupby,
    "joins": scenario_cross_and_on_filter,
    "edge_cases": scenario_edge_cases,
    "spatial": scenario_spatial,
    "manipola_chain": scenario_manipola_chain,
}


def run_cross_db_scenario(dialect: str, scenario: str) -> None:
    if dialect not in DIALECTS:
        raise AssertionError(f"Unknown dialect: {dialect}")
    if scenario not in SCENARIOS:
        raise AssertionError(f"Unknown cross-db scenario: {scenario}")
    if not _enabled(dialect):
        print(
            f"SKIP cross-db {dialect}: set PLENORA_E2E_CROSS_DB=1 "
            f"or PLENORA_E2E_CROSS_DB_DIALECTS={dialect}",
            flush=True,
        )
        return
    ensure_plenora_test_network_access()
    run_test(lambda ctx: SCENARIOS[scenario](ctx, dialect))
