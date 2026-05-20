"""Reusable ESTRAI E2E matrix cases for numbered host tests."""

from __future__ import annotations

from typing import Any

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


ColumnSpec = tuple[str, str, str | None, bool]


def _source(spec: dict[str, Any], connection_id: str) -> dict[str, Any]:
    return source_node(
        node_id=spec["id"],
        table_name=spec["table"],
        alias=spec["alias"],
        connection_id=connection_id,
        columns=[
            source_column(name, logical_type=logical_type, alias=alias, visible=visible)
            for name, logical_type, alias, visible in spec["columns"]
        ],
        filters=spec.get("filters") or [],
        position=spec.get("position"),
    )


def _canvas(case: dict[str, Any], connection_id: str) -> dict[str, Any]:
    nodes = [_source(spec, connection_id) for spec in case["sources"]]
    result_config = {
        "format": "csv",
        "fileName": f"e2e_{case['id']}",
    }
    result_config.update(case.get("result") or {})
    nodes.append(result_node(**result_config))
    return {
        "nodes": nodes,
        "edges": [*case.get("edges", []), edge("e-last-result", case["last"], "result-1")],
    }


def _run_case(case_id: str, ctx: E2EContext) -> None:
    case = CASES[case_id]
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name(f"e2e_v2_{case_id}_postgres"))
    )
    project_id = ctx.create_project(
        name=f"e2e_v2_{case_id}_{case['slug']}",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_canvas(case, connection_id),
    )
    rows = ctx.preview_project(project_id, limit=case.get("preview_limit", 500)).get("rows") or []
    assert_equal(len(rows), case["expected_count"], case["message"])

    for column, expected in (case.get("expected_non_null") or {}).items():
        actual = sum(1 for row in rows if row.get(column) is not None)
        assert_equal(actual, expected, f"{case_id}: non-null count for {column}")

    if "expected_columns" in case:
        actual_columns = list(rows[0].keys()) if rows else []
        assert_equal(actual_columns, case["expected_columns"], f"{case_id}: visible columns")


def run_case(case_id: str) -> None:
    run_test(lambda ctx: _run_case(case_id, ctx))


CUSTOMERS: list[ColumnSpec] = [
    ("id", "int", "customer_id", True),
    ("city_id", "int", None, True),
    ("full_name", "str", "customer_name", True),
    ("age", "int", None, True),
    ("status", "str", None, True),
]

CITIES: list[ColumnSpec] = [
    ("id", "int", "city_id", True),
    ("region_id", "int", None, True),
    ("name", "str", "city_name", True),
    ("founded_year", "int", None, True),
    ("geom", "geometry:point", None, False),
]

REGIONS: list[ColumnSpec] = [
    ("id", "int", "region_id", True),
    ("code", "str", "region_code", True),
    ("name", "str", "region_name", True),
    ("boundary", "geometry:multipolygon", None, False),
]

PRODUCTS: list[ColumnSpec] = [
    ("id", "int", "product_id", True),
    ("category", "str", None, True),
    ("price", "decimal", None, True),
    ("weight_kg", "decimal", None, True),
    ("in_stock_count", "int", None, True),
]

ORDERS: list[ColumnSpec] = [
    ("id", "int", "order_id", True),
    ("customer_id", "int", None, True),
    ("total_amount", "decimal", None, True),
    ("status", "str", "order_status", True),
]

ORDER_ITEMS: list[ColumnSpec] = [
    ("id", "int", "item_id", True),
    ("order_id", "int", None, True),
    ("product_id", "int", None, True),
    ("qty", "int", None, True),
    ("line_total", "decimal", None, True),
]

EMPLOYEES: list[ColumnSpec] = [
    ("id", "int", "employee_id", True),
    ("manager_id", "int", None, True),
    ("name", "str", "employee_name", True),
    ("role", "str", None, True),
]

ROUTES: list[ColumnSpec] = [
    ("id", "int", "route_id", True),
    ("name", "str", "route_name", True),
    ("geom", "geometry:linestring", None, False),
]


def _single(
    case_id: str,
    *,
    slug: str,
    table: str,
    alias: str,
    columns: list[ColumnSpec],
    expected_count: int,
    filters: list[dict[str, Any]] | None = None,
    result: dict[str, Any] | None = None,
    message: str | None = None,
) -> tuple[str, dict[str, Any]]:
    return case_id, {
        "id": case_id,
        "slug": slug,
        "sources": [{"id": table, "table": table, "alias": alias, "columns": columns, "filters": filters or []}],
        "edges": [],
        "last": table,
        "result": result or {},
        "expected_count": expected_count,
        "message": message or f"{case_id}: ESTRAI single-source case must match fixture rows",
    }


def _join(
    case_id: str,
    *,
    slug: str,
    sources: list[dict[str, Any]],
    join_edges: list[dict[str, Any]],
    last: str,
    expected_count: int,
    expected_non_null: dict[str, int] | None = None,
    preview_limit: int = 500,
    message: str | None = None,
) -> tuple[str, dict[str, Any]]:
    return case_id, {
        "id": case_id,
        "slug": slug,
        "sources": sources,
        "edges": join_edges,
        "last": last,
        "expected_count": expected_count,
        "expected_non_null": expected_non_null or {},
        "preview_limit": preview_limit,
        "message": message or f"{case_id}: ESTRAI join case must match fixture rows",
    }


def _src(node_id: str, table: str, alias: str, columns: list[ColumnSpec]) -> dict[str, Any]:
    return {"id": node_id, "table": table, "alias": alias, "columns": columns}


CASES = dict([
    _single("test_017", slug="customers_filter_eq", table="customers", alias="c", columns=CUSTOMERS, expected_count=17, filters=[{"column": "status", "operator": "=", "value": "active"}]),
    _single("test_018", slug="customers_filter_in", table="customers", alias="c", columns=CUSTOMERS, expected_count=19, filters=[{"column": "status", "operator": "IN", "value": "active,pending"}]),
    _single("test_019", slug="customers_city_null", table="customers", alias="c", columns=CUSTOMERS, expected_count=2, filters=[{"column": "city_id", "operator": "IS NULL", "value": ""}]),
    _single("test_020", slug="customers_city_not_null", table="customers", alias="c", columns=CUSTOMERS, expected_count=20, filters=[{"column": "city_id", "operator": "IS NOT NULL", "value": ""}]),
    _single("test_021", slug="customers_age_between", table="customers", alias="c", columns=CUSTOMERS, expected_count=10, filters=[{"column": "age", "operator": "BETWEEN", "value": "30 AND 40"}]),
    _single("test_022", slug="customers_like", table="customers", alias="c", columns=CUSTOMERS, expected_count=3, filters=[{"column": "full_name", "operator": "LIKE", "value": "Anna"}]),
    _single("test_023", slug="customers_not_like", table="customers", alias="c", columns=CUSTOMERS, expected_count=19, filters=[{"column": "full_name", "operator": "NOT LIKE", "value": "Anna"}]),
    _single("test_024", slug="orders_not_in", table="orders", alias="o", columns=ORDERS, expected_count=19, filters=[{"column": "status", "operator": "NOT IN", "value": "cancelled"}]),
    _single("test_025", slug="products_price_gt", table="products", alias="p", columns=PRODUCTS, expected_count=3, filters=[{"column": "price", "operator": ">", "value": "100"}]),
    _single("test_026", slug="products_weight_null", table="products", alias="p", columns=PRODUCTS, expected_count=1, filters=[{"column": "weight_kg", "operator": "IS NULL", "value": ""}]),
    _single("test_027", slug="cities_founded_null", table="cities", alias="ci", columns=CITIES, expected_count=9, filters=[{"column": "founded_year", "operator": "IS NULL", "value": ""}]),
    _single("test_028", slug="products_distinct_category", table="products", alias="p", columns=[("category", "str", None, True)], expected_count=4, result={"distinct": True}),
    _single("test_029", slug="products_order_limit", table="products", alias="p", columns=PRODUCTS, expected_count=3, result={"limit": 3, "order_by_enabled": True, "order_by_columns": [{"column": "price", "direction": "DESC"}], "data": {"order_by_enabled": True, "order_by_columns": [{"column": "price", "direction": "DESC"}]}}),
    _single("test_030", slug="customers_group_having", table="customers", alias="c", columns=CUSTOMERS, expected_count=3, result={"group_by_enabled": True, "group_by_columns": ["status"], "aggregations": [{"column": "*", "function": "COUNT", "alias": "customer_count"}], "having_enabled": True, "having_conditions": [{"function": "COUNT", "column": "*", "operator": ">", "value": "1"}], "data": {"group_by_enabled": True, "group_by_columns": ["status"], "aggregations": [{"column": "*", "function": "COUNT", "alias": "customer_count"}], "having_enabled": True, "having_conditions": [{"function": "COUNT", "column": "*", "operator": ">", "value": "1"}]}}),
    _join("test_031", slug="customers_cities_inner", sources=[_src("customers", "customers", "c", CUSTOMERS), _src("cities", "cities", "ci", CITIES)], join_edges=[{"id": "e-c-ci", "source": "customers", "target": "cities", "join_type": "inner", "source_column": "city_id", "target_column": "id"}], last="cities", expected_count=20),
    _join("test_032", slug="customers_cities_left", sources=[_src("customers", "customers", "c", CUSTOMERS), _src("cities", "cities", "ci", CITIES)], join_edges=[{"id": "e-c-ci", "source": "customers", "target": "cities", "join_type": "left", "source_column": "city_id", "target_column": "id"}], last="cities", expected_count=22, expected_non_null={"city_name": 20}),
    _join("test_033", slug="customers_cities_right", sources=[_src("customers", "customers", "c", CUSTOMERS), _src("cities", "cities", "ci", CITIES)], join_edges=[{"id": "e-c-ci", "source": "customers", "target": "cities", "join_type": "right", "source_column": "city_id", "target_column": "id"}], last="cities", expected_count=32, expected_non_null={"customer_id": 20}),
    _join("test_034", slug="customers_cities_full", sources=[_src("customers", "customers", "c", CUSTOMERS), _src("cities", "cities", "ci", CITIES)], join_edges=[{"id": "e-c-ci", "source": "customers", "target": "cities", "join_type": "full", "source_column": "city_id", "target_column": "id"}], last="cities", expected_count=34, expected_non_null={"customer_id": 22, "city_name": 32}),
    _join("test_035", slug="orders_customers_inner", sources=[_src("orders", "orders", "o", ORDERS), _src("customers", "customers", "c", CUSTOMERS)], join_edges=[{"id": "e-o-c", "source": "orders", "target": "customers", "join_type": "inner", "source_column": "customer_id", "target_column": "id"}], last="customers", expected_count=21),
    _join("test_036", slug="customers_orders_left", sources=[_src("customers", "customers", "c", CUSTOMERS), _src("orders", "orders", "o", ORDERS)], join_edges=[{"id": "e-c-o", "source": "customers", "target": "orders", "join_type": "left", "source_column": "id", "target_column": "customer_id"}], last="orders", expected_count=33, expected_non_null={"order_id": 21}),
    _join("test_037", slug="cities_regions_inner", sources=[_src("cities", "cities", "ci", CITIES), _src("regions", "regions", "r", REGIONS)], join_edges=[{"id": "e-ci-r", "source": "cities", "target": "regions", "join_type": "inner", "source_column": "region_id", "target_column": "id"}], last="regions", expected_count=18),
    _join("test_038", slug="regions_cities_left", sources=[_src("regions", "regions", "r", REGIONS), _src("cities", "cities", "ci", CITIES)], join_edges=[{"id": "e-r-ci", "source": "regions", "target": "cities", "join_type": "left", "source_column": "id", "target_column": "region_id"}], last="cities", expected_count=19, expected_non_null={"city_name": 18}),
    _join("test_039", slug="orders_items_inner", sources=[_src("orders", "orders", "o", ORDERS), _src("order_items", "order_items", "oi", ORDER_ITEMS)], join_edges=[{"id": "e-o-oi", "source": "orders", "target": "order_items", "join_type": "inner", "source_column": "id", "target_column": "order_id"}], last="order_items", expected_count=35),
    _join("test_040", slug="items_products_inner", sources=[_src("order_items", "order_items", "oi", ORDER_ITEMS), _src("products", "products", "p", PRODUCTS)], join_edges=[{"id": "e-oi-p", "source": "order_items", "target": "products", "join_type": "inner", "source_column": "product_id", "target_column": "id"}], last="products", expected_count=35),
    _join("test_041", slug="orders_items_products_chain", sources=[_src("orders", "orders", "o", ORDERS), _src("order_items", "order_items", "oi", ORDER_ITEMS), _src("products", "products", "p", PRODUCTS)], join_edges=[{"id": "e-o-oi", "source": "orders", "target": "order_items", "join_type": "inner", "source_column": "id", "target_column": "order_id"}, {"id": "e-oi-p", "source": "order_items", "target": "products", "join_type": "inner", "source_column": "product_id", "target_column": "id"}], last="products", expected_count=35),
    _join("test_042", slug="customers_orders_items_left_chain", sources=[_src("customers", "customers", "c", CUSTOMERS), _src("orders", "orders", "o", ORDERS), _src("order_items", "order_items", "oi", ORDER_ITEMS)], join_edges=[{"id": "e-c-o", "source": "customers", "target": "orders", "join_type": "left", "source_column": "id", "target_column": "customer_id"}, {"id": "e-o-oi", "source": "orders", "target": "order_items", "join_type": "left", "source_column": "id", "target_column": "order_id"}], last="order_items", expected_count=48, expected_non_null={"item_id": 35}),
    _join("test_043", slug="employees_self_inner", sources=[_src("employees", "employees", "e", EMPLOYEES), _src("managers", "employees", "m", [("id", "int", "manager_pk", True), ("name", "str", "manager_name", True), ("role", "str", "manager_role", True)])], join_edges=[{"id": "e-emp-mgr", "source": "employees", "target": "managers", "join_type": "inner", "source_column": "manager_id", "target_column": "id"}], last="managers", expected_count=14),
    _join("test_044", slug="employees_self_left", sources=[_src("employees", "employees", "e", EMPLOYEES), _src("managers", "employees", "m", [("id", "int", "manager_pk", True), ("name", "str", "manager_name", True), ("role", "str", "manager_role", True)])], join_edges=[{"id": "e-emp-mgr", "source": "employees", "target": "managers", "join_type": "left", "source_column": "manager_id", "target_column": "id"}], last="managers", expected_count=17, expected_non_null={"manager_name": 14}),
    _join("test_045", slug="customers_self_trim_age", sources=[_src("customers_a", "customers", "a", CUSTOMERS), _src("customers_b", "customers", "b", [("id", "int", "customer_b_id", True), ("full_name", "str", "customer_b_name", True), ("age", "int", "age_b", True)])], join_edges=[{"id": "e-a-b", "source": "customers_a", "target": "customers_b", "join_type": "inner", "join_conditions": [{"source_column": "full_name", "target_column": "full_name", "source_func": "TRIM", "target_func": "TRIM"}, {"source_column": "age", "target_column": "age"}]}], last="customers_b", expected_count=27),
    _join("test_046", slug="items_products_on_food", sources=[_src("order_items", "order_items", "oi", ORDER_ITEMS), _src("products", "products", "p", PRODUCTS)], join_edges=[{"id": "e-oi-p-food", "source": "order_items", "target": "products", "join_type": "inner", "source_column": "product_id", "target_column": "id", "on_filters": [{"column": "category", "operator": "=", "value": "FOOD"}]}], last="products", expected_count=12),
    _join("test_047", slug="customers_cities_on_like", sources=[_src("customers", "customers", "c", CUSTOMERS), _src("cities", "cities", "ci", CITIES)], join_edges=[{"id": "e-c-ci-roma", "source": "customers", "target": "cities", "join_type": "left", "source_column": "city_id", "target_column": "id", "on_filters": [{"column": "name", "operator": "LIKE", "value": "Roma"}]}], last="cities", expected_count=22, expected_non_null={"city_name": 5}),
    _join("test_048", slug="customers_cities_on_in", sources=[_src("customers", "customers", "c", CUSTOMERS), _src("cities", "cities", "ci", CITIES)], join_edges=[{"id": "e-c-ci-region", "source": "customers", "target": "cities", "join_type": "left", "source_column": "city_id", "target_column": "id", "on_filters": [{"column": "region_id", "operator": "IN", "value": "1,2"}]}], last="cities", expected_count=22, expected_non_null={"city_name": 17}),
    _join("test_049", slug="regions_cities_on_null", sources=[_src("regions", "regions", "r", REGIONS), _src("cities", "cities", "ci", CITIES)], join_edges=[{"id": "e-r-ci-null", "source": "regions", "target": "cities", "join_type": "left", "source_column": "id", "target_column": "region_id", "on_filters": [{"column": "founded_year", "operator": "IS NULL", "value": ""}]}], last="cities", expected_count=10, expected_non_null={"city_name": 9}),
    _join("test_050", slug="items_products_on_price", sources=[_src("order_items", "order_items", "oi", ORDER_ITEMS), _src("products", "products", "p", PRODUCTS)], join_edges=[{"id": "e-oi-p-price", "source": "order_items", "target": "products", "join_type": "left", "source_column": "product_id", "target_column": "id", "on_filters": [{"column": "price", "operator": ">", "value": "100"}]}], last="products", expected_count=35, expected_non_null={"product_id": 35, "category": 5}),
    _join("test_051", slug="spatial_city_region_intersects", sources=[_src("cities", "cities", "c", CITIES), _src("regions", "regions", "r", REGIONS)], join_edges=[{"id": "e-c-r-sp", "source": "cities", "target": "regions", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Intersects", "source_geom_column": "geom", "target_geom_column": "boundary"}], last="regions", expected_count=14),
    _join("test_052", slug="spatial_city_region_within", sources=[_src("cities", "cities", "c", CITIES), _src("regions", "regions", "r", REGIONS)], join_edges=[{"id": "e-c-r-sp", "source": "cities", "target": "regions", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Within", "source_geom_column": "geom", "target_geom_column": "boundary"}], last="regions", expected_count=14),
    _join("test_053", slug="spatial_region_city_contains", sources=[_src("regions", "regions", "r", REGIONS), _src("cities", "cities", "c", CITIES)], join_edges=[{"id": "e-r-c-sp", "source": "regions", "target": "cities", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Contains", "source_geom_column": "boundary", "target_geom_column": "geom"}], last="cities", expected_count=14),
    _join("test_054", slug="spatial_city_city_equals", sources=[_src("cities_a", "cities", "a", CITIES), _src("cities_b", "cities", "b", [("id", "int", "city_b_id", True), ("name", "str", "city_b_name", True), ("geom", "geometry:point", None, False)])], join_edges=[{"id": "e-a-b-equals", "source": "cities_a", "target": "cities_b", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Equals", "source_geom_column": "geom", "target_geom_column": "geom"}], last="cities_b", expected_count=20),
    _join("test_055", slug="spatial_city_city_dwithin", sources=[_src("cities_a", "cities", "a", CITIES), _src("cities_b", "cities", "b", [("id", "int", "city_b_id", True), ("name", "str", "city_b_name", True), ("geom", "geometry:point", None, False)])], join_edges=[{"id": "e-a-b-dwithin", "source": "cities_a", "target": "cities_b", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_DWithin", "source_geom_column": "geom", "target_geom_column": "geom", "spatial_distance": 0.01}], last="cities_b", expected_count=22),
    _join("test_056", slug="spatial_route_region_crosses", sources=[_src("routes", "routes", "ro", ROUTES), _src("regions", "regions", "r", REGIONS)], join_edges=[{"id": "e-ro-r-crosses", "source": "routes", "target": "regions", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Crosses", "source_geom_column": "geom", "target_geom_column": "boundary"}], last="regions", expected_count=4),
    _join("test_057", slug="spatial_route_region_intersects", sources=[_src("routes", "routes", "ro", ROUTES), _src("regions", "regions", "r", REGIONS)], join_edges=[{"id": "e-ro-r-intersects", "source": "routes", "target": "regions", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Intersects", "source_geom_column": "geom", "target_geom_column": "boundary"}], last="regions", expected_count=6),
    _join("test_058", slug="spatial_route_city_intersects", sources=[_src("routes", "routes", "ro", ROUTES), _src("cities", "cities", "c", CITIES)], join_edges=[{"id": "e-ro-c-intersects", "source": "routes", "target": "cities", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Intersects", "source_geom_column": "geom", "target_geom_column": "geom"}], last="cities", expected_count=12),
    _join("test_059", slug="spatial_route_city_dwithin_zero", sources=[_src("routes", "routes", "ro", ROUTES), _src("cities", "cities", "c", CITIES)], join_edges=[{"id": "e-ro-c-dwithin", "source": "routes", "target": "cities", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_DWithin", "source_geom_column": "geom", "target_geom_column": "geom", "spatial_distance": 0}], last="cities", expected_count=12),
    _join("test_060", slug="spatial_region_region_touches", sources=[_src("regions_a", "regions", "a", REGIONS), _src("regions_b", "regions", "b", [("id", "int", "region_b_id", True), ("code", "str", "region_b_code", True), ("boundary", "geometry:multipolygon", None, False)])], join_edges=[{"id": "e-a-b-touches", "source": "regions_a", "target": "regions_b", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Touches", "source_geom_column": "boundary", "target_geom_column": "boundary"}], last="regions_b", expected_count=2),
    _join("test_061", slug="spatial_region_region_disjoint", sources=[_src("regions_a", "regions", "a", REGIONS), _src("regions_b", "regions", "b", [("id", "int", "region_b_id", True), ("code", "str", "region_b_code", True), ("boundary", "geometry:multipolygon", None, False)])], join_edges=[{"id": "e-a-b-disjoint", "source": "regions_a", "target": "regions_b", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Disjoint", "source_geom_column": "boundary", "target_geom_column": "boundary"}], last="regions_b", expected_count=4),
    _join("test_062", slug="spatial_region_region_overlaps", sources=[_src("regions_a", "regions", "a", REGIONS), _src("regions_b", "regions", "b", [("id", "int", "region_b_id", True), ("code", "str", "region_b_code", True), ("boundary", "geometry:multipolygon", None, False)])], join_edges=[{"id": "e-a-b-overlaps", "source": "regions_a", "target": "regions_b", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Overlaps", "source_geom_column": "boundary", "target_geom_column": "boundary"}], last="regions_b", expected_count=0),
    _join("test_063", slug="spatial_region_city_covers", sources=[_src("regions", "regions", "r", REGIONS), _src("cities", "cities", "c", CITIES)], join_edges=[{"id": "e-r-c-covers", "source": "regions", "target": "cities", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Covers", "source_geom_column": "boundary", "target_geom_column": "geom"}], last="cities", expected_count=14),
    _join("test_064", slug="spatial_city_region_coveredby", sources=[_src("cities", "cities", "c", CITIES), _src("regions", "regions", "r", REGIONS)], join_edges=[{"id": "e-c-r-coveredby", "source": "cities", "target": "regions", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_CoveredBy", "source_geom_column": "geom", "target_geom_column": "boundary"}], last="regions", expected_count=14),
    _join("test_065", slug="spatial_city_city_disjoint", sources=[_src("cities_a", "cities", "a", CITIES), _src("cities_b", "cities", "b", [("id", "int", "city_b_id", True), ("name", "str", "city_b_name", True), ("geom", "geometry:point", None, False)])], join_edges=[{"id": "e-a-b-disjoint", "source": "cities_a", "target": "cities_b", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Disjoint", "source_geom_column": "geom", "target_geom_column": "geom"}], last="cities_b", expected_count=304, preview_limit=400),
    _join("test_066", slug="spatial_route_region_within", sources=[_src("routes", "routes", "ro", ROUTES), _src("regions", "regions", "r", REGIONS)], join_edges=[{"id": "e-ro-r-within", "source": "routes", "target": "regions", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Within", "source_geom_column": "geom", "target_geom_column": "boundary"}], last="regions", expected_count=2),
    _join("test_067", slug="spatial_route_region_coveredby", sources=[_src("routes", "routes", "ro", ROUTES), _src("regions", "regions", "r", REGIONS)], join_edges=[{"id": "e-ro-r-coveredby", "source": "routes", "target": "regions", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_CoveredBy", "source_geom_column": "geom", "target_geom_column": "boundary"}], last="regions", expected_count=2),
    _join("test_068", slug="spatial_route_city_touches", sources=[_src("routes", "routes", "ro", ROUTES), _src("cities", "cities", "c", CITIES)], join_edges=[{"id": "e-ro-c-touches", "source": "routes", "target": "cities", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Touches", "source_geom_column": "geom", "target_geom_column": "geom"}], last="cities", expected_count=12),
    _join("test_069", slug="spatial_route_city_crosses", sources=[_src("routes", "routes", "ro", ROUTES), _src("cities", "cities", "c", CITIES)], join_edges=[{"id": "e-ro-c-crosses", "source": "routes", "target": "cities", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_Crosses", "source_geom_column": "geom", "target_geom_column": "geom"}], last="cities", expected_count=0),
    _join("test_070", slug="spatial_city_region_dwithin_one", sources=[_src("cities", "cities", "c", CITIES), _src("regions", "regions", "r", REGIONS)], join_edges=[{"id": "e-c-r-dwithin", "source": "cities", "target": "regions", "join_type": "inner", "is_spatial": True, "spatial_predicate": "ST_DWithin", "source_geom_column": "geom", "target_geom_column": "boundary", "spatial_distance": 1.0}], last="regions", expected_count=20),
])
