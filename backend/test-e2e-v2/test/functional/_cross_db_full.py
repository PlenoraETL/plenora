"""Cross-database coverage for ESTRAI, MANIPOLA, CARICA and external sources."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

from _cross_db_extended import DIALECTS, _connection, _enabled
from _e2e import (
    E2EContext,
    assert_equal,
    assert_true,
    edge,
    ensure_plenora_test_network_access,
    fail,
    project_source_node_from_contract,
    result_node,
    run_test,
    source_column,
    source_node,
    transform_node,
    unique_name,
)


def _run(cmd: list[str], *, input_text: str | None = None) -> str:
    result = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        fail(
            f"Command failed: {' '.join(cmd)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout


def _enabled_dialects() -> list[str]:
    return [dialect for dialect in DIALECTS if _enabled(dialect)]


def _loop_dialects(ctx: E2EContext, scenario: str) -> list[tuple[str, str]]:
    dialects = _enabled_dialects()
    if not dialects:
        print("SKIP cross-db full: set PLENORA_E2E_CROSS_DB=1", flush=True)
        return []
    ensure_plenora_test_network_access()
    return [(dialect, _connection(ctx, dialect, scenario)) for dialect in dialects]


def _spatial_canvas(
    connection_id: str,
    dialect: str,
    *,
    predicate: str,
    left_table: str,
    right_table: str,
    left_alias: str,
    right_alias: str,
    left_geom: str,
    right_geom: str,
    left_columns: list[dict[str, Any]],
    right_columns: list[dict[str, Any]],
    distance: float | None = None,
) -> dict[str, Any]:
    spatial_edge: dict[str, Any] = {
        "id": f"e-{left_alias}-{right_alias}-{predicate.lower()}",
        "source": "left",
        "target": "right",
        "join_type": "inner",
        "is_spatial": True,
        "spatial_predicate": predicate,
        "source_geom_column": left_geom,
        "target_geom_column": right_geom,
    }
    if distance is not None:
        spatial_edge["spatial_distance"] = distance
    return {
        "nodes": [
            source_node(
                node_id="left",
                table_name=left_table,
                alias=left_alias,
                connection_id=connection_id,
                columns=left_columns + [source_column(left_geom, logical_type="geometry", visible=False)],
            ),
            source_node(
                node_id="right",
                table_name=right_table,
                alias=right_alias,
                connection_id=connection_id,
                columns=right_columns + [source_column(right_geom, logical_type="geometry", visible=False)],
                position={"x": 0, "y": 190},
            ),
            result_node(format="csv", fileName=f"e2e_{dialect}_{predicate.lower()}"),
        ],
        "edges": [spatial_edge, edge("e-right-result", "right", "result-1")],
    }


def scenario_spatial_predicates(ctx: E2EContext) -> None:
    """Cover spatial predicates across MySQL, SQL Server and Oracle."""

    for dialect, connection_id in _loop_dialects(ctx, "spatial_predicates"):
        cases = [
            (
                "ST_Intersects",
                "cities",
                "regions",
                "c",
                "r",
                "geom",
                "boundary",
                [source_column("id", logical_type="int", alias="city_id")],
                [source_column("code", logical_type="str", alias="region_code")],
                14,
                None,
            ),
            (
                "ST_Within",
                "cities",
                "regions",
                "c",
                "r",
                "geom",
                "boundary",
                [source_column("id", logical_type="int", alias="city_id")],
                [source_column("code", logical_type="str", alias="region_code")],
                14,
                None,
            ),
            (
                "ST_DWithin",
                "cities",
                "regions",
                "c",
                "r",
                "geom",
                "boundary",
                [source_column("id", logical_type="int", alias="city_id")],
                [source_column("code", logical_type="str", alias="region_code")],
                14,
                0.0,
            ),
            (
                "ST_Contains",
                "regions",
                "cities",
                "r",
                "c",
                "boundary",
                "geom",
                [source_column("code", logical_type="str", alias="region_code")],
                [source_column("id", logical_type="int", alias="city_id")],
                14,
                None,
            ),
            (
                "ST_Covers",
                "regions",
                "cities",
                "r",
                "c",
                "boundary",
                "geom",
                [source_column("code", logical_type="str", alias="region_code")],
                [source_column("id", logical_type="int", alias="city_id")],
                14,
                None,
            ),
            (
                "ST_CoveredBy",
                "cities",
                "regions",
                "c",
                "r",
                "geom",
                "boundary",
                [source_column("id", logical_type="int", alias="city_id")],
                [source_column("code", logical_type="str", alias="region_code")],
                14,
                None,
            ),
            (
                "ST_Equals",
                "cities",
                "cities",
                "a",
                "b",
                "geom",
                "geom",
                [source_column("id", logical_type="int", alias="city_a_id")],
                [source_column("id", logical_type="int", alias="city_b_id")],
                20,
                None,
            ),
            (
                "ST_Disjoint",
                "cities",
                "cities",
                "a",
                "b",
                "geom",
                "geom",
                [source_column("id", logical_type="int", alias="city_a_id")],
                [source_column("id", logical_type="int", alias="city_b_id")],
                304,
                None,
            ),
            (
                "ST_Touches",
                "regions",
                "regions",
                "a",
                "b",
                "boundary",
                "boundary",
                [source_column("code", logical_type="str", alias="region_a_code")],
                [source_column("code", logical_type="str", alias="region_b_code")],
                2,
                None,
            ),
            (
                "ST_Overlaps",
                "regions",
                "regions",
                "a",
                "b",
                "boundary",
                "boundary",
                [source_column("code", logical_type="str", alias="region_a_code")],
                [source_column("code", logical_type="str", alias="region_b_code")],
                0,
                None,
            ),
            (
                "ST_Crosses",
                "routes",
                "regions",
                "ro",
                "r",
                "geom",
                "boundary",
                [source_column("id", logical_type="int", alias="route_id")],
                [source_column("code", logical_type="str", alias="region_code")],
                4,
                None,
            ),
        ]
        for (
            predicate,
            left_table,
            right_table,
            left_alias,
            right_alias,
            left_geom,
            right_geom,
            left_columns,
            right_columns,
            expected_count,
            distance,
        ) in cases:
            project_id = ctx.create_project(
                name=f"e2e_v2_cross_db_{dialect}_{predicate.lower()}",
                project_type="estrai",
                default_connection_id=connection_id,
                canvas=_spatial_canvas(
                    connection_id,
                    dialect,
                    predicate=predicate,
                    left_table=left_table,
                    right_table=right_table,
                    left_alias=left_alias,
                    right_alias=right_alias,
                    left_geom=left_geom,
                    right_geom=right_geom,
                    left_columns=left_columns,
                    right_columns=right_columns,
                    distance=distance,
                ),
            )
            rows = ctx.preview_project(project_id, limit=500).get("rows") or []
            assert_equal(
                len(rows),
                expected_count,
                f"{dialect}: {predicate} count must match deterministic fixture",
            )


def _row_pairs(rows: list[dict[str, Any]], left_key: str, right_key: str) -> set[tuple[str, str]]:
    return {
        (str(row.get(left_key)), str(row.get(right_key)))
        for row in rows
    }


def _preview_spatial_case(
    ctx: E2EContext,
    *,
    dialect: str,
    connection_id: str,
    predicate: str,
    left_table: str,
    right_table: str,
    left_alias: str,
    right_alias: str,
    left_geom: str,
    right_geom: str,
    left_columns: list[dict[str, Any]],
    right_columns: list[dict[str, Any]],
    distance: float | None = None,
) -> list[dict[str, Any]]:
    project_id = ctx.create_project(
        name=f"e2e_v2_cross_db_{dialect}_{predicate.lower()}_exact",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_spatial_canvas(
            connection_id,
            dialect,
            predicate=predicate,
            left_table=left_table,
            right_table=right_table,
            left_alias=left_alias,
            right_alias=right_alias,
            left_geom=left_geom,
            right_geom=right_geom,
            left_columns=left_columns,
            right_columns=right_columns,
            distance=distance,
        ),
    )
    return ctx.preview_project(project_id, limit=500).get("rows") or []


def scenario_spatial_exact_pairs(ctx: E2EContext) -> None:
    """Assert exact pairs around the spatial predicates that recently regressed."""

    for dialect, connection_id in _loop_dialects(ctx, "spatial_exact_pairs"):
        touches = _preview_spatial_case(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            predicate="ST_Touches",
            left_table="regions",
            right_table="regions",
            left_alias="a",
            right_alias="b",
            left_geom="boundary",
            right_geom="boundary",
            left_columns=[source_column("code", logical_type="str", alias="region_a_code")],
            right_columns=[source_column("code", logical_type="str", alias="region_b_code")],
        )
        assert_equal(
            _row_pairs(touches, "region_a_code", "region_b_code"),
            {("LAZ", "CAM"), ("CAM", "LAZ")},
            f"{dialect}: ST_Touches must only return LAZ/CAM border pairs",
        )

        crosses = _preview_spatial_case(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            predicate="ST_Crosses",
            left_table="routes",
            right_table="regions",
            left_alias="ro",
            right_alias="r",
            left_geom="geom",
            right_geom="boundary",
            left_columns=[source_column("id", logical_type="int", alias="route_id")],
            right_columns=[source_column("code", logical_type="str", alias="region_code")],
        )
        assert_equal(
            _row_pairs(crosses, "route_id", "region_code"),
            {("1", "LOM"), ("1", "LAZ"), ("3", "LAZ"), ("3", "CAM")},
            f"{dialect}: ST_Crosses must return deterministic route/region pairs",
        )

        equals = _preview_spatial_case(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            predicate="ST_Equals",
            left_table="cities",
            right_table="cities",
            left_alias="a",
            right_alias="b",
            left_geom="geom",
            right_geom="geom",
            left_columns=[source_column("id", logical_type="int", alias="city_a_id")],
            right_columns=[source_column("id", logical_type="int", alias="city_b_id")],
        )
        expected_equals = {(str(i), str(i)) for i in range(1, 19)} | {("4", "18"), ("18", "4")}
        assert_equal(
            _row_pairs(equals, "city_a_id", "city_b_id"),
            expected_equals,
            f"{dialect}: ST_Equals must include self pairs plus Roma/Roma Bis duplicate point",
        )


def scenario_spatial_inverse_predicates(ctx: E2EContext) -> None:
    """Verify inverse spatial predicates keep the same deterministic pairs."""

    for dialect, connection_id in _loop_dialects(ctx, "spatial_inverse_predicates"):
        contains = _preview_spatial_case(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            predicate="ST_Contains",
            left_table="regions",
            right_table="cities",
            left_alias="r",
            right_alias="c",
            left_geom="boundary",
            right_geom="geom",
            left_columns=[source_column("code", logical_type="str", alias="region_code")],
            right_columns=[source_column("id", logical_type="int", alias="city_id")],
        )
        within = _preview_spatial_case(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            predicate="ST_Within",
            left_table="cities",
            right_table="regions",
            left_alias="c",
            right_alias="r",
            left_geom="geom",
            right_geom="boundary",
            left_columns=[source_column("id", logical_type="int", alias="city_id")],
            right_columns=[source_column("code", logical_type="str", alias="region_code")],
        )
        contains_pairs = _row_pairs(contains, "city_id", "region_code")
        within_pairs = _row_pairs(within, "city_id", "region_code")
        assert_equal(contains_pairs, within_pairs, f"{dialect}: contains(region, city) must match within(city, region)")

        covers = _preview_spatial_case(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            predicate="ST_Covers",
            left_table="regions",
            right_table="cities",
            left_alias="r",
            right_alias="c",
            left_geom="boundary",
            right_geom="geom",
            left_columns=[source_column("code", logical_type="str", alias="region_code")],
            right_columns=[source_column("id", logical_type="int", alias="city_id")],
        )
        covered_by = _preview_spatial_case(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            predicate="ST_CoveredBy",
            left_table="cities",
            right_table="regions",
            left_alias="c",
            right_alias="r",
            left_geom="geom",
            right_geom="boundary",
            left_columns=[source_column("id", logical_type="int", alias="city_id")],
            right_columns=[source_column("code", logical_type="str", alias="region_code")],
        )
        assert_equal(
            _row_pairs(covers, "city_id", "region_code"),
            _row_pairs(covered_by, "city_id", "region_code"),
            f"{dialect}: covers(region, city) must match coveredBy(city, region)",
        )

        region_covers = _preview_spatial_case(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            predicate="ST_Covers",
            left_table="regions",
            right_table="regions",
            left_alias="a",
            right_alias="b",
            left_geom="boundary",
            right_geom="boundary",
            left_columns=[source_column("code", logical_type="str", alias="region_a_code")],
            right_columns=[source_column("code", logical_type="str", alias="region_b_code")],
        )
        assert_equal(
            _row_pairs(region_covers, "region_a_code", "region_b_code"),
            {("LOM", "LOM"), ("LAZ", "LAZ"), ("CAM", "CAM")},
            f"{dialect}: ST_Covers on regions must include non-null self pairs only",
        )


def scenario_spatial_null_boundaries(ctx: E2EContext) -> None:
    """Ensure null geometries and cross-meridian fixture do not create false positives."""

    for dialect, connection_id in _loop_dialects(ctx, "spatial_null_boundaries"):
        for predicate in ("ST_Intersects", "ST_Touches", "ST_Crosses", "ST_Covers", "ST_CoveredBy"):
            rows = _preview_spatial_case(
                ctx,
                dialect=dialect,
                connection_id=connection_id,
                predicate=predicate,
                left_table="regions",
                right_table="regions",
                left_alias="a",
                right_alias="b",
                left_geom="boundary",
                right_geom="boundary",
                left_columns=[source_column("code", logical_type="str", alias="region_a_code")],
                right_columns=[source_column("code", logical_type="str", alias="region_b_code")],
            )
            pairs = _row_pairs(rows, "region_a_code", "region_b_code")
            assert_true(
                all("VEN" not in pair for pair in pairs),
                f"{dialect}: {predicate} must not match region VEN with null boundary",
            )

        route_intersections = _preview_spatial_case(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            predicate="ST_Intersects",
            left_table="routes",
            right_table="regions",
            left_alias="ro",
            right_alias="r",
            left_geom="geom",
            right_geom="boundary",
            left_columns=[source_column("id", logical_type="int", alias="route_id")],
            right_columns=[source_column("code", logical_type="str", alias="region_code")],
        )
        route_pairs = _row_pairs(route_intersections, "route_id", "region_code")
        assert_true(
            all(route_id != "5" for route_id, _ in route_pairs),
            f"{dialect}: cross-meridian route 5 must not intersect Italian fixture regions",
        )


def scenario_schema_inspector(ctx: E2EContext) -> None:
    """Verify schema inspector table/column/type metadata for all DB fixtures."""

    expected = {
        "customers": {"id", "full_name", "email", "age", "signup_date", "status"},
        "cities": {"id", "name", "latitude", "longitude", "geom"},
        "regions": {"id", "code", "name", "boundary"},
        "routes": {"id", "name", "length_km", "geom"},
        "products": {"id", "code", "price", "weight_kg", "in_stock_count"},
    }
    for dialect, connection_id in _loop_dialects(ctx, "schema_inspector"):
        payload = ctx.api_json("GET", f"/connections/{connection_id}/tables")
        tables = payload.get("tables") if isinstance(payload, dict) else None
        assert_true(isinstance(tables, dict), f"{dialect}: inspector must return tables object")
        normalized_tables = {str(name).lower(): columns for name, columns in tables.items()}
        for table_name, columns in expected.items():
            assert_true(table_name in normalized_tables, f"{dialect}: inspector missing {table_name}")
            by_name = {str(column.get("name")).lower(): str(column.get("type", "")) for column in normalized_tables[table_name]}
            assert_true(columns <= set(by_name), f"{dialect}: inspector missing columns for {table_name}: {by_name}")
        geom_types = {
            str(column.get("name")).lower(): str(column.get("type", "")).lower()
            for column in normalized_tables["cities"] + normalized_tables["regions"]
        }
        assert_true("geometry" in geom_types.get("geom", ""), f"{dialect}: city geom type must be geometry-like")
        assert_true("geometry" in geom_types.get("boundary", ""), f"{dialect}: region boundary type must be geometry-like")


def _extract_active_canvas(connection_id: str, dialect: str) -> dict[str, Any]:
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
                fileName=f"e2e_{dialect}_active_customers",
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


def _deep_manipola_canvas(source_project_id: str, contract: dict[str, Any], dialect: str) -> dict[str, Any]:
    source = project_source_node_from_contract(
        node_id="source-1",
        source_project_id=source_project_id,
        contract=contract,
    )
    columns = source["columns"]
    by_name = {column["name"]: column for column in columns}
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={
            "formula": "age*2",
            "new_column": "double_age",
            "formula_column_refs": [{"id": by_name["age"]["id"], "name": "age"}],
        },
        input_columns=columns,
    )
    rename = transform_node(
        node_id="rename-1",
        transform_op="rename",
        config={
            "renames": [{"old_name": "customer_name", "old_name_id": by_name["customer_name"]["id"], "new_name": "name_public"}],
        },
        position={"x": 520, "y": 0},
    )
    remove = transform_node(
        node_id="remove-1",
        transform_op="drop_columns",
        config={
            "columns": ["status"],
            "columns_column_ids": [by_name["status"]["id"]],
        },
        position={"x": 760, "y": 0},
    )
    sort = transform_node(
        node_id="sort-1",
        transform_op="sort",
        config={
            "columns": ["double_age"],
            "columns_column_ids": [],
            "ascending": False,
        },
        position={"x": 1000, "y": 0},
    )
    return {
        "nodes": [
            source,
            formula,
            rename,
            remove,
            sort,
            result_node(format="csv", fileName=f"e2e_{dialect}_deep_manipola"),
        ],
        "edges": [
            edge("e-source-formula", "source-1", "formula-1"),
            edge("e-formula-rename", "formula-1", "rename-1"),
            edge("e-rename-remove", "rename-1", "remove-1"),
            edge("e-remove-sort", "remove-1", "sort-1"),
            edge("e-sort-result", "sort-1", "result-1"),
        ],
    }


def scenario_manipola_extended(ctx: E2EContext) -> None:
    """Run rename/drop/formula/sort through output_contract for every DB."""

    for dialect, connection_id in _loop_dialects(ctx, "manipola_extended"):
        upstream_id = ctx.create_project(
            name=f"e2e_v2_cross_db_{dialect}_manipola_ext_upstream",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=_extract_active_canvas(connection_id, dialect),
        )
        ctx.execute_project(upstream_id)
        contract = ctx.get_project(upstream_id).get("output_contract") or {}
        downstream_id = ctx.create_project(
            name=f"e2e_v2_cross_db_{dialect}_manipola_ext",
            project_type="manipola",
            canvas=_deep_manipola_canvas(upstream_id, contract, dialect),
        )
        rows = ctx.preview_project(downstream_id, limit=20).get("rows") or []
        assert_true(rows, f"{dialect}: manipola extended must return rows")
        first = rows[0]
        assert_true("name_public" in first, f"{dialect}: rename must expose name_public")
        assert_true("customer_name" not in first, f"{dialect}: old alias must be removed after rename")
        assert_true("status" not in first, f"{dialect}: remove_columns must remove status")
        assert_true("double_age" in first, f"{dialect}: formula must expose double_age")
        ages = [int(row["double_age"]) for row in rows if row.get("double_age") is not None]
        assert_true(ages, f"{dialect}: formula must produce non-null double_age values")
        assert_equal(ages, sorted(ages, reverse=True), f"{dialect}: sort must order formula output")


def _carica_node(connection_id: str, schema_name: str, table_name: str) -> dict[str, Any]:
    data = {
        "dest_type": "db",
        "connection_id": connection_id,
        "schema_name": schema_name,
        "table_name": table_name,
        "write_mode": "create",
        "if_table_exists": "replace",
        "batch_size": 100,
        "transaction_mode": "all_or_nothing",
        "column_mappings": [
            {"source_column": "customer_id", "dest_column": "customer_id", "dest_type": "INTEGER"},
            {"source_column": "customer_name", "dest_column": "customer_name", "dest_type": "VARCHAR(128)"},
            {"source_column": "age", "dest_column": "age", "dest_type": "INTEGER"},
        ],
    }
    return {
        "id": "carica-1",
        "type": "carica",
        "position": {"x": 940, "y": 0},
        "data": data,
        **data,
    }


def _wait_carica_tasks(ctx: E2EContext, execute_result: dict[str, Any], dialect: str) -> None:
    tasks = ((execute_result.get("carica") or {}).get("tasks") or [])
    assert_true(tasks, f"{dialect}: execute result must include CARICA task ids")
    for task in tasks:
        status = ctx.wait_task(str(task["task_id"]))
        result = status.get("result") or {}
        assert_true(result.get("success"), f"{dialect}: CARICA task must succeed: {result!r}")


def _verify_table_canvas(connection_id: str, table_name: str, dialect: str) -> dict[str, Any]:
    return {
        "nodes": [
            source_node(
                node_id="loaded",
                table_name=table_name,
                alias="l",
                connection_id=connection_id,
                columns=[
                    source_column("customer_id", logical_type="int"),
                    source_column("customer_name", logical_type="str"),
                    source_column("age", logical_type="int"),
                ],
            ),
            result_node(
                format="csv",
                fileName=f"e2e_{dialect}_carica_verify",
                order_by_enabled=True,
                order_by_columns=[{"column": "customer_id", "direction": "ASC"}],
                data={
                    "order_by_enabled": True,
                    "order_by_columns": [{"column": "customer_id", "direction": "ASC"}],
                },
            ),
        ],
        "edges": [edge("e-loaded-result", "loaded", "result-1")],
    }


def scenario_carica_db_roundtrip(ctx: E2EContext) -> None:
    """ESTRAI from each DB, CARICA back to each DB, then read the created table."""

    schemas = {
        "mysql": os.environ.get("MYSQL_DATABASE", "plenora_test"),
        "mssql": os.environ.get("PLENORA_E2E_MSSQL_SCHEMA", "dbo"),
        "oracle": os.environ.get("ORACLE_APP_USER", "plenora"),
    }
    for dialect, connection_id in _loop_dialects(ctx, "carica_db"):
        table_name = unique_name(f"e2e_load_{dialect}").lower()[:48]
        base_canvas = _extract_active_canvas(connection_id, dialect)
        expected_id = ctx.create_project(
            name=f"e2e_v2_cross_db_{dialect}_carica_expected",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=base_canvas,
        )
        expected_rows = ctx.preview_project(expected_id, limit=50).get("rows") or []
        assert_true(expected_rows, f"{dialect}: source active customer extract must return rows")
        canvas = json.loads(json.dumps(base_canvas))
        canvas["nodes"].append(_carica_node(connection_id, schemas[dialect], table_name))
        canvas["edges"].append(edge("e-result-carica", "result-1", "carica-1"))
        project_id = ctx.create_project(
            name=f"e2e_v2_cross_db_{dialect}_carica_db",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=canvas,
        )
        result = ctx.execute_project(project_id)
        _wait_carica_tasks(ctx, result, dialect)

        verify_id = ctx.create_project(
            name=f"e2e_v2_cross_db_{dialect}_carica_verify",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=_verify_table_canvas(connection_id, table_name, dialect),
        )
        rows = ctx.preview_project(verify_id, limit=50).get("rows") or []
        assert_equal(len(rows), len(expected_rows), f"{dialect}: CARICA table must contain active customers")
        assert_equal(rows[0]["customer_id"], expected_rows[0]["customer_id"], f"{dialect}: CARICA table must be readable by ESTRAI")


def _extract_active_fidelity_canvas(connection_id: str, dialect: str) -> dict[str, Any]:
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
                    source_column("email", logical_type="str"),
                    source_column("age", logical_type="int"),
                    source_column("status", logical_type="str"),
                ],
                filters=[{"column": "status", "operator": "=", "value": "active"}],
            ),
            result_node(
                format="csv",
                fileName=f"e2e_{dialect}_active_customers_fidelity",
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


def _carica_fidelity_node(connection_id: str, schema_name: str, table_name: str) -> dict[str, Any]:
    data = {
        "dest_type": "db",
        "connection_id": connection_id,
        "schema_name": schema_name,
        "table_name": table_name,
        "write_mode": "create",
        "if_table_exists": "replace",
        "batch_size": 100,
        "transaction_mode": "all_or_nothing",
        "column_mappings": [
            {"source_column": "customer_id", "dest_column": "customer_id", "dest_type": "INT"},
            {"source_column": "customer_name", "dest_column": "customer_name", "dest_type": "STR"},
            {"source_column": "email", "dest_column": "email", "dest_type": "STR"},
            {"source_column": "age", "dest_column": "age", "dest_type": "INT"},
            {"source_column": "status", "dest_column": "status", "dest_type": "STR"},
        ],
    }
    return {
        "id": "carica-1",
        "type": "carica",
        "position": {"x": 940, "y": 0},
        "data": data,
        **data,
    }


def _verify_fidelity_canvas(connection_id: str, table_name: str, dialect: str) -> dict[str, Any]:
    return {
        "nodes": [
            source_node(
                node_id="loaded",
                table_name=table_name,
                alias="l",
                connection_id=connection_id,
                columns=[
                    source_column("customer_id", logical_type="int"),
                    source_column("customer_name", logical_type="str"),
                    source_column("email", logical_type="str"),
                    source_column("age", logical_type="int"),
                    source_column("status", logical_type="str"),
                ],
            ),
            result_node(
                format="csv",
                fileName=f"e2e_{dialect}_carica_fidelity_verify",
                order_by_enabled=True,
                order_by_columns=[{"column": "customer_id", "direction": "ASC"}],
                data={
                    "order_by_enabled": True,
                    "order_by_columns": [{"column": "customer_id", "direction": "ASC"}],
                },
            ),
        ],
        "edges": [edge("e-loaded-result", "loaded", "result-1")],
    }


def scenario_carica_db_fidelity(ctx: E2EContext) -> None:
    """Verify CARICA preserves text, unicode, emoji and NULL values per DB."""

    schemas = {
        "mysql": os.environ.get("MYSQL_DATABASE", "plenora_test"),
        "mssql": os.environ.get("PLENORA_E2E_MSSQL_SCHEMA", "dbo"),
        "oracle": os.environ.get("ORACLE_APP_USER", "plenora"),
    }
    for dialect, connection_id in _loop_dialects(ctx, "carica_db_fidelity"):
        table_name = unique_name(f"e2e_fidelity_{dialect}").lower()[:48]
        base_canvas = _extract_active_fidelity_canvas(connection_id, dialect)
        expected_id = ctx.create_project(
            name=f"e2e_v2_cross_db_{dialect}_carica_fidelity_expected",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=base_canvas,
        )
        expected_rows = ctx.preview_project(expected_id, limit=100).get("rows") or []
        expected_by_id = {int(row["customer_id"]): row for row in expected_rows}
        assert_true({13, 14, 15, 19, 21, 22} <= set(expected_by_id), f"{dialect}: source fixture must include fidelity rows")

        canvas = json.loads(json.dumps(base_canvas))
        canvas["nodes"].append(_carica_fidelity_node(connection_id, schemas[dialect], table_name))
        canvas["edges"].append(edge("e-result-carica", "result-1", "carica-1"))
        project_id = ctx.create_project(
            name=f"e2e_v2_cross_db_{dialect}_carica_fidelity_load",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=canvas,
        )
        result = ctx.execute_project(project_id)
        _wait_carica_tasks(ctx, result, dialect)

        verify_id = ctx.create_project(
            name=f"e2e_v2_cross_db_{dialect}_carica_fidelity_verify",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=_verify_fidelity_canvas(connection_id, table_name, dialect),
        )
        rows = ctx.preview_project(verify_id, limit=100).get("rows") or []
        loaded_by_id = {int(row["customer_id"]): row for row in rows}
        assert_equal(set(loaded_by_id), set(expected_by_id), f"{dialect}: CARICA fidelity table must preserve ids")
        for customer_id in (13, 14, 15, 19, 21, 22):
            expected = expected_by_id[customer_id]
            loaded = loaded_by_id[customer_id]
            assert_equal(loaded["customer_name"], expected["customer_name"], f"{dialect}: name fidelity for customer {customer_id}")
            assert_equal(loaded["email"], expected["email"], f"{dialect}: email fidelity for customer {customer_id}")
            assert_equal(loaded["age"], expected["age"], f"{dialect}: age fidelity for customer {customer_id}")
            assert_equal(loaded["status"], expected["status"], f"{dialect}: status fidelity for customer {customer_id}")


def _extract_customers_by_status_canvas(connection_id: str, dialect: str, status: str) -> dict[str, Any]:
    canvas = _extract_active_canvas(connection_id, dialect)
    source = canvas["nodes"][0]
    source["filters"] = [{"column": "status", "operator": "=", "value": status}]
    source["data"]["filters"] = [{"column": "status", "operator": "=", "value": status}]
    return canvas


def _carica_mode_node(
    connection_id: str,
    schema_name: str,
    table_name: str,
    *,
    write_mode: str,
    if_table_exists: str = "error",
    transaction_mode: str = "all_or_nothing",
    allow_unsafe_append: bool = False,
) -> dict[str, Any]:
    node = _carica_node(connection_id, schema_name, table_name)
    patch = {
        "write_mode": write_mode,
        "if_table_exists": if_table_exists,
        "transaction_mode": transaction_mode,
        "allow_unsafe_append": allow_unsafe_append,
    }
    node.update(patch)
    node["data"].update(patch)
    return node


def _execute_carica_canvas(
    ctx: E2EContext,
    *,
    dialect: str,
    connection_id: str,
    canvas: dict[str, Any],
    project_suffix: str,
) -> None:
    project_id = ctx.create_project(
        name=f"e2e_v2_cross_db_{dialect}_{project_suffix}",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    result = ctx.execute_project(project_id)
    _wait_carica_tasks(ctx, result, dialect)


def scenario_carica_db_write_modes(ctx: E2EContext) -> None:
    """Exercise supported CARICA database write modes around existing-table paths."""

    schemas = {
        "mysql": os.environ.get("MYSQL_DATABASE", "plenora_test"),
        "mssql": os.environ.get("PLENORA_E2E_MSSQL_SCHEMA", "dbo"),
        "oracle": os.environ.get("ORACLE_APP_USER", "plenora"),
    }
    for dialect, connection_id in _loop_dialects(ctx, "carica_db_write_modes"):
        schema_name = schemas[dialect]
        table_name = unique_name(f"e2e_modes_{dialect}").lower()[:48]

        active_canvas = _extract_active_canvas(connection_id, dialect)
        active_expected_id = ctx.create_project(
            name=f"e2e_v2_cross_db_{dialect}_write_modes_active_expected",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=active_canvas,
        )
        active_rows = ctx.preview_project(active_expected_id, limit=100).get("rows") or []
        assert_true(active_rows, f"{dialect}: active source rows required")

        create_canvas = json.loads(json.dumps(active_canvas))
        create_canvas["nodes"].append(
            _carica_mode_node(
                connection_id,
                schema_name,
                table_name,
                write_mode="create",
                if_table_exists="error",
            )
        )
        create_canvas["edges"].append(edge("e-result-create", "result-1", "carica-1"))
        _execute_carica_canvas(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            canvas=create_canvas,
            project_suffix="write_modes_create",
        )
        verify_id = ctx.create_project(
            name=f"e2e_v2_cross_db_{dialect}_write_modes_verify_create",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=_verify_table_canvas(connection_id, table_name, dialect),
        )
        rows_after_create = ctx.preview_project(verify_id, limit=200).get("rows") or []
        assert_equal(len(rows_after_create), len(active_rows), f"{dialect}: create must write active rows")

        append_canvas = json.loads(json.dumps(active_canvas))
        append_canvas["nodes"].append(
            _carica_mode_node(
                connection_id,
                schema_name,
                table_name,
                write_mode="append",
                transaction_mode="all_or_nothing",
            )
        )
        append_canvas["edges"].append(edge("e-result-append", "result-1", "carica-1"))
        _execute_carica_canvas(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            canvas=append_canvas,
            project_suffix="write_modes_append",
        )
        rows_after_append = ctx.preview_project(verify_id, limit=400).get("rows") or []
        assert_equal(len(rows_after_append), len(active_rows) * 2, f"{dialect}: append must add rows")

        truncate_canvas = json.loads(json.dumps(active_canvas))
        truncate_canvas["nodes"].append(
            _carica_mode_node(
                connection_id,
                schema_name,
                table_name,
                write_mode="truncate_insert",
                transaction_mode="all_or_nothing",
            )
        )
        truncate_canvas["edges"].append(edge("e-result-truncate", "result-1", "carica-1"))
        _execute_carica_canvas(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            canvas=truncate_canvas,
            project_suffix="write_modes_truncate_insert",
        )
        rows_after_truncate = ctx.preview_project(verify_id, limit=200).get("rows") or []
        assert_equal(len(rows_after_truncate), len(active_rows), f"{dialect}: truncate_insert must replace table contents")

        pending_canvas = _extract_customers_by_status_canvas(connection_id, dialect, "pending")
        pending_expected_id = ctx.create_project(
            name=f"e2e_v2_cross_db_{dialect}_write_modes_pending_expected",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=pending_canvas,
        )
        pending_rows = ctx.preview_project(pending_expected_id, limit=100).get("rows") or []
        assert_true(pending_rows, f"{dialect}: pending source rows required")

        replace_canvas = json.loads(json.dumps(pending_canvas))
        replace_canvas["nodes"].append(
            _carica_mode_node(
                connection_id,
                schema_name,
                table_name,
                write_mode="replace",
                if_table_exists="replace",
            )
        )
        replace_canvas["edges"].append(edge("e-result-replace", "result-1", "carica-1"))
        _execute_carica_canvas(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            canvas=replace_canvas,
            project_suffix="write_modes_replace",
        )
        rows_after_replace = ctx.preview_project(verify_id, limit=200).get("rows") or []
        assert_equal(len(rows_after_replace), len(pending_rows), f"{dialect}: replace must swap table contents")
        assert_equal(
            {int(row["customer_id"]) for row in rows_after_replace},
            {int(row["customer_id"]) for row in pending_rows},
            f"{dialect}: replace must leave only pending customer ids",
        )


def _http_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    expected: tuple[int, ...] = (200,),
) -> tuple[int, dict[str, Any]]:
    data = None
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
            body = response.read().decode("utf-8") if response.length != 0 else ""
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8")
    if status not in expected:
        fail(f"{method} {url} returned HTTP {status}: {body}")
    return status, json.loads(body) if body else {}


def scenario_rest_sink_contract(ctx: E2EContext) -> None:
    """Exercise fake REST sink error and idempotency behavior used by CARICA."""

    ensure_plenora_test_network_access()
    base = os.environ.get("PLENORA_E2E_FAKE_REST_HOST_URL", "http://127.0.0.1:58080")
    headers = {"X-API-Key": "PLENORA_TEST_KEY"}

    status, reset = _http_json(
        "POST",
        f"{base}/data/sink/records",
        payload={"records": []},
        headers={**headers, "X-Test-Reset": "1"},
        expected=(201,),
    )
    assert_equal(status, 201, "REST sink reset call must create an empty batch")
    assert_true(reset.get("ok"), "REST sink reset call must acknowledge reset")

    _http_json(
        "POST",
        f"{base}/data/sink/records",
        payload={"records": [{"name": "no auth"}]},
        headers={"X-API-Key": "WRONG"},
        expected=(401,),
    )
    _http_json(
        "POST",
        f"{base}/data/sink/records",
        payload={"not_records": []},
        headers=headers,
        expected=(400,),
    )

    _, created = _http_json(
        "POST",
        f"{base}/data/sink/records",
        payload={"records": [{"name": "Anna"}, {"name": "Muller"}]},
        headers=headers,
        expected=(201,),
    )
    assert_equal(created.get("created"), 2, "REST sink must create two records")
    ids = created.get("ids") or []
    assert_equal(ids, [1000, 1001], "REST sink ids must restart deterministically after reset")

    _, patched = _http_json(
        "PATCH",
        f"{base}/data/sink/by_id/{ids[0]}",
        payload={"status": "patched"},
        headers=headers,
        expected=(200,),
    )
    assert_equal(patched.get("name"), "Anna", "REST sink PATCH must preserve existing fields")
    assert_equal(patched.get("status"), "patched", "REST sink PATCH must merge new fields")

    _, put = _http_json(
        "PUT",
        f"{base}/data/sink/by_id/{ids[1]}",
        payload={"name": "Replaced"},
        headers=headers,
        expected=(200,),
    )
    assert_equal(put.get("updated"), ids[1], "REST sink PUT must update existing id")

    batch_payload = {
        "batch_records_key": "e2e-rest-idempotency-key",
        "records": [{"x": 1}, {"x": 2}],
    }
    _, batch_1 = _http_json(
        "POST",
        f"{base}/data/sink/batch",
        payload=batch_payload,
        headers=headers,
        expected=(202,),
    )
    _, batch_2 = _http_json(
        "POST",
        f"{base}/data/sink/batch",
        payload=batch_payload,
        headers=headers,
        expected=(202,),
    )
    assert_equal(batch_1, batch_2, "REST sink idempotent batch replay must return identical payload")

    delete_request = urllib.request.Request(
        f"{base}/data/sink/by_id/{ids[0]}",
        headers=headers,
        method="DELETE",
    )
    with urllib.request.urlopen(delete_request, timeout=15) as response:
        assert_equal(response.status, 204, "REST sink DELETE existing record must return 204")
    _http_json(
        "DELETE",
        f"{base}/data/sink/by_id/{ids[0]}",
        headers=headers,
        expected=(404,),
    )


def _datasource_format_canvas(
    datasource_id: str,
    path: str,
    kind: str,
    columns: list[dict[str, Any]],
    *,
    fmt: str | None = None,
    parse_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node_columns = []
    for idx, column in enumerate(columns):
        name = str(column.get("name") or "")
        if not name:
            continue
        node_columns.append({
            "id": f"{kind}-{idx}-{name}",
            "name": name,
            "type": str(column.get("type") or "str"),
            "visible": True,
        })
    node = {
        "id": "datasource-1",
        "type": "datasource_node",
        "position": {"x": 0, "y": 0},
        "datasource_id": datasource_id,
        "file_path": path,
        "columns": node_columns,
        "data": {
            "datasource_id": datasource_id,
            "file_path": path,
            "columns": node_columns,
        },
    }
    if fmt:
        node["format"] = fmt
        node["data"]["format"] = fmt
    if parse_options:
        node["parse_options"] = parse_options
        node["data"]["parse_options"] = parse_options
    return {
        "nodes": [node, result_node(format="csv", fileName=f"e2e_{kind}_{Path(path).stem}")],
        "edges": [edge("e-ds-result", "datasource-1", "result-1")],
    }


def scenario_datasource_multiformat(ctx: E2EContext) -> None:
    """Read several remote file formats through FTP and SFTP datasource_node."""

    ensure_plenora_test_network_access()
    cases = [
        ("fixtures/csv/customers_unicode.csv", 16, {"full_name": "EmojiUser 🎉"}, None, None),
        ("fixtures/json/customers.json", 12, {"full_name": "Anna Rossi"}, None, None),
        ("fixtures/json/with_nulls.json", 5, {"full_name": "Anna Rossi"}, None, None),
        ("fixtures/parquet/customers.parquet", 12, {"full_name": "Anna Rossi"}, None, None),
        ("fixtures/xlsx/single_sheet.xlsx", 12, {"full_name": "Anna Rossi"}, None, None),
        ("fixtures/geojson/cities_points.geojson", 9, {"name": "Milano"}, "geojson", None),
    ]
    for kind in ("ftp", "sftp"):
        datasource_id = ctx.create_datasource(_datasource_payload(kind, unique_name(f"e2e_v2_{kind}_formats")))
        tested = ctx.api_json("POST", f"/datasources/{datasource_id}/test")
        assert_true(tested.get("success"), f"{kind}: datasource test must succeed")
        for path, expected_rows, expected_values, fmt, parse_options in cases:
            schema = ctx.api_json(
                "GET",
                f"/datasources/{datasource_id}/files/schema?path={quote(path)}",
            )
            columns = schema.get("columns") or []
            assert_true(columns, f"{kind}: schema must return columns for {path}")
            project_id = ctx.create_project(
                name=f"e2e_v2_{kind}_{Path(path).stem}_datasource_format",
                project_type="manipola",
                canvas=_datasource_format_canvas(
                    datasource_id,
                    path,
                    kind,
                    columns,
                    fmt=fmt,
                    parse_options=parse_options,
                ),
            )
            rows = ctx.preview_project(project_id, limit=50).get("rows") or []
            assert_equal(len(rows), expected_rows, f"{kind}: datasource_node row count for {path}")
            for column_name, expected_value in expected_values.items():
                assert_true(
                    any(str(row.get(column_name)) == expected_value for row in rows),
                    f"{kind}: datasource_node must preserve {column_name}={expected_value!r} for {path}",
                )


def _datasource_payload(kind: str, name: str) -> dict[str, Any]:
    if kind == "ftp":
        return {
            "name": name,
            "description": "E2E FTP fixture",
            "type": "ftp",
            "host": os.environ.get("PLENORA_E2E_FTP_HOST", "plenora-test-ftp"),
            "port": int(os.environ.get("PLENORA_E2E_FTP_PORT", "21")),
            "username": os.environ.get("FTP_USER", "tester"),
            "password": os.environ.get("FTP_PASSWORD", "plenora_ftp_pwd"),
            "root_path": "",
            "tls": False,
            "passive": True,
        }
    if kind == "sftp":
        return {
            "name": name,
            "description": "E2E SFTP fixture",
            "type": "sftp",
            "host": os.environ.get("PLENORA_E2E_SFTP_HOST", "plenora-test-sftp"),
            "port": int(os.environ.get("PLENORA_E2E_SFTP_PORT", "22")),
            "username": os.environ.get("SFTP_USER", "tester"),
            "password": os.environ.get("SFTP_PASSWORD", "plenora_sftp_pwd"),
            "root_path": "",
            "host_key_policy": "auto_add",
        }
    raise AssertionError(f"Unsupported datasource kind: {kind}")


def _datasource_canvas(datasource_id: str, path: str, kind: str) -> dict[str, Any]:
    columns = [
        {"id": f"{kind}-id", "name": "id", "type": "int", "visible": True},
        {"id": f"{kind}-name", "name": "full_name", "type": "str", "visible": True},
        {"id": f"{kind}-age", "name": "age", "type": "int", "visible": True},
    ]
    node = {
        "id": "datasource-1",
        "type": "datasource_node",
        "position": {"x": 0, "y": 0},
        "datasource_id": datasource_id,
        "file_path": path,
        "columns": columns,
        "data": {
            "datasource_id": datasource_id,
            "file_path": path,
            "columns": columns,
        },
    }
    return {
        "nodes": [node, result_node(format="csv", fileName=f"e2e_{kind}_datasource")],
        "edges": [edge("e-ds-result", "datasource-1", "result-1")],
    }


def scenario_datasources_and_fake_rest(ctx: E2EContext) -> None:
    """Cover FTP/SFTP datasource reads plus fake REST read/write endpoints."""

    ensure_plenora_test_network_access()
    for kind in ("ftp", "sftp"):
        datasource_id = ctx.create_datasource(_datasource_payload(kind, unique_name(f"e2e_v2_{kind}")))
        tested = ctx.api_json("POST", f"/datasources/{datasource_id}/test")
        assert_true(tested.get("success"), f"{kind}: datasource test must succeed: {tested!r}")
        files = ctx.api_json("GET", f"/datasources/{datasource_id}/files?path=fixtures/csv&pattern=customers_basic.csv")
        assert_true(any(item.get("name") == "customers_basic.csv" for item in files), f"{kind}: must list fixture CSV")
        schema = ctx.api_json(
            "GET",
            f"/datasources/{datasource_id}/files/schema?path={quote('fixtures/csv/customers_basic.csv')}",
        )
        columns = {str(column.get("name")) for column in schema.get("columns", [])}
        assert_true({"id", "full_name", "age"} <= columns, f"{kind}: schema must expose CSV columns")

        project_id = ctx.create_project(
            name=f"e2e_v2_{kind}_datasource_to_manipola",
            project_type="manipola",
            canvas=_datasource_canvas(datasource_id, "fixtures/csv/customers_basic.csv", kind),
        )
        rows = ctx.preview_project(project_id, limit=20).get("rows") or []
        assert_equal(len(rows), 12, f"{kind}: datasource_node preview must read CSV rows")
        assert_equal(rows[0]["full_name"], "Anna Rossi", f"{kind}: datasource_node must parse CSV content")

    fake_host_url = os.environ.get("PLENORA_E2E_FAKE_REST_HOST_URL", "http://127.0.0.1:58080")
    with urllib.request.urlopen(f"{fake_host_url}/page/offset?offset=0&limit=5", timeout=15) as response:
        page = json.loads(response.read().decode("utf-8"))
    assert_equal(len(page["data"]), 5, "fake REST offset pagination must return first page")
    with urllib.request.urlopen(f"{fake_host_url}/file/csv/customers?api_key=PLENORA_TEST_KEY", timeout=15) as response:
        csv_text = response.read().decode("utf-8")
    assert_true("Anna Rossi" in csv_text, "fake REST file endpoint must return CSV fixture")

    connection_id = ctx.create_connection(DIALECTS["mysql"](unique_name("e2e_v2_rest_sink_mysql")))
    canvas = _extract_active_canvas(connection_id, "rest_sink")
    rest_data = {
        "dest_type": "rest",
        "rest_url": os.environ.get(
            "PLENORA_E2E_FAKE_REST_CONTAINER_SINK_URL",
            "http://plenora-test-fake-rest:8080/data/sink/records",
        ),
        "rest_method": "POST",
        "rest_headers": [{"name": "X-API-Key", "value": "PLENORA_TEST_KEY", "secret": True}],
        "rest_batch_mode": "batch",
        "rest_batch_size": 50,
        "rest_timeout_seconds": 15,
    }
    canvas["nodes"].append({
        "id": "carica-rest-1",
        "type": "carica",
        "position": {"x": 940, "y": 0},
        "data": rest_data,
        **rest_data,
    })
    canvas["edges"].append(edge("e-result-rest", "result-1", "carica-rest-1"))
    project_id = ctx.create_project(
        name="e2e_v2_fake_rest_carica_sink",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    result = ctx.execute_project(project_id)
    _wait_carica_tasks(ctx, result, "fake-rest")


def run_scenario(name: str) -> None:
    scenarios = {
        "spatial_predicates": scenario_spatial_predicates,
        "spatial_exact_pairs": scenario_spatial_exact_pairs,
        "spatial_inverse_predicates": scenario_spatial_inverse_predicates,
        "spatial_null_boundaries": scenario_spatial_null_boundaries,
        "schema_inspector": scenario_schema_inspector,
        "manipola_extended": scenario_manipola_extended,
        "carica_db_roundtrip": scenario_carica_db_roundtrip,
        "carica_db_fidelity": scenario_carica_db_fidelity,
        "carica_db_write_modes": scenario_carica_db_write_modes,
        "rest_sink_contract": scenario_rest_sink_contract,
        "datasource_multiformat": scenario_datasource_multiformat,
        "datasources_and_fake_rest": scenario_datasources_and_fake_rest,
    }
    if name not in scenarios:
        raise AssertionError(f"Unknown scenario: {name}")
    run_test(scenarios[name])
