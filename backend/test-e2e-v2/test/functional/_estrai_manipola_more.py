"""Additional ESTRAI/MANIPOLA E2E coverage beyond the join matrix."""

from __future__ import annotations

import csv
from io import StringIO
from typing import Any, Callable

from _e2e import (
    E2EContext,
    assert_equal,
    assert_true,
    contract_column,
    edge,
    ensure_plenora_test_network_access,
    fail,
    output_contract,
    postgres_fixture_connection_payload,
    project_source_node,
    project_source_node_from_contract,
    result_node,
    run_backend_python,
    run_test,
    set_output_contract,
    source_column,
    source_node,
    transform_node,
    unique_name,
)


def _connection(ctx: E2EContext, suffix: str) -> str:
    ensure_plenora_test_network_access()
    return ctx.create_connection(
        postgres_fixture_connection_payload(unique_name(f"e2e_v2_{suffix}_postgres"))
    )


def _customers_canvas(connection_id: str, *, aliases: bool = True, fmt: str = "csv") -> dict:
    return {
        "nodes": [
            source_node(
                node_id="customers",
                table_name="customers",
                alias="c",
                connection_id=connection_id,
                columns=[
                    source_column("id", logical_type="int", alias="customer_id" if aliases else None),
                    source_column("city_id", logical_type="int"),
                    source_column("full_name", logical_type="str", alias="customer_name" if aliases else None),
                    source_column("age", logical_type="int"),
                    source_column("status", logical_type="str"),
                ],
            ),
            result_node(format=fmt, fileName=f"e2e_{fmt}_customers"),
        ],
        "edges": [edge("e-customers-result", "customers", "result-1")],
    }


def _customers_active_canvas(connection_id: str) -> dict:
    canvas = _customers_canvas(connection_id)
    canvas["nodes"][0]["filters"] = [{"column": "status", "operator": "=", "value": "active"}]
    canvas["nodes"][0]["data"]["filters"] = canvas["nodes"][0]["filters"]
    return canvas


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
                    source_column("region_id", logical_type="int"),
                ],
            ),
            result_node(format="csv", fileName="e2e_cities"),
        ],
        "edges": [edge("e-cities-result", "cities", "result-1")],
    }


def _read_csv_bytes(raw: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(StringIO(raw.decode("utf-8-sig"))))


def test_071(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "test_071")
    tables = ctx.api_json("GET", f"/connections/{connection_id}/tables")
    table_map = tables.get("tables") or {}
    assert_true("customers" in table_map, "Inspector must list customers table")
    assert_true("cities" in table_map, "Inspector must list cities table")
    cities_columns = {col["name"]: col["type"] for col in table_map["cities"]}
    assert_equal(cities_columns.get("geom"), "geometry:point:4326", "Inspector must expose PostGIS point metadata")
    regions_columns = {col["name"]: col["type"] for col in table_map["regions"]}
    assert_equal(regions_columns.get("boundary"), "geometry:multipolygon:4326", "Inspector must expose multipolygon metadata")


def test_072(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "test_072")
    response = ctx.api_json("POST", f"/connections/{connection_id}/test")
    assert_equal(response.get("success"), True, "Connection test endpoint must validate fixture DB")


def test_073(ctx: E2EContext) -> None:
    del ctx
    script = (
        "from app.core.estrai.query_generator import QueryGenerator\n"
        "from app.core.projects.canvas_models import Node, Edge\n"
        "nodes=["
        "Node(id='customers',type='source',position={'x':0,'y':0},table_name='customers',alias='c',columns=[{'name':'id','type':'int'}]),"
        "Node(id='cities',type='source',position={'x':0,'y':0},table_name='cities',alias='ci',columns=[{'name':'id','type':'int'}]),"
        "Node(id='result-1',type='result',position={'x':0,'y':0})"
        "]\n"
        "edges=[Edge(id='e-empty',source='customers',target='cities',join_type='left'),Edge(id='e-out',source='cities',target='result-1')]\n"
        "try:\n"
        "    QueryGenerator().generate_sql(nodes,edges,'result-1')\n"
        "except ValueError as exc:\n"
        "    print(str(exc))\n"
        "    raise SystemExit(0 if 'JOIN senza condizioni' in str(exc) else 2)\n"
        "raise SystemExit(1)\n"
    )
    result = run_backend_python(script)
    if result.returncode != 0:
        fail(
            "QueryGenerator must reject empty attribute joins\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def test_074(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "test_074")
    canvas = {
        "nodes": [
            source_node(
                node_id="customers",
                table_name="customers",
                alias="c",
                connection_id=connection_id,
                columns=[source_column("id", logical_type="int")],
            ),
            source_node(
                node_id="cities",
                table_name="cities",
                alias="ci",
                connection_id=connection_id,
                columns=[source_column("id", logical_type="int")],
            ),
            result_node(),
        ],
        "edges": [
            {"id": "e-bad", "source": "customers", "target": "cities", "join_type": "inner", "source_column": "bad;drop", "target_column": "id"},
            edge("e-cities-result", "cities", "result-1"),
        ],
    }
    project_id = ctx.create_project(
        name="e2e_v2_test_074_bad_join_identifier",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    try:
        ctx.preview_project(project_id, limit=10)
    except AssertionError as exc:
        assert_true("Colonna JOIN non valida" in str(exc), "Invalid join identifier must be rejected")
        return
    fail("Invalid join identifier preview unexpectedly succeeded")


def test_075(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "test_075")
    canvas = {
        "nodes": [
            source_node(
                node_id="customers",
                table_name="customers",
                alias="bad;alias",
                connection_id=connection_id,
                columns=[source_column("id", logical_type="int")],
            ),
            result_node(),
        ],
        "edges": [edge("e-customers-result", "customers", "result-1")],
    }
    project_id = ctx.create_project(
        name="e2e_v2_test_075_bad_alias",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    try:
        ctx.preview_project(project_id, limit=10)
    except AssertionError as exc:
        assert_true("Alias tabella" in str(exc), "Unsafe table alias must be rejected")
        return
    fail("Unsafe alias preview unexpectedly succeeded")


def test_076(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "test_076")
    canvas = _customers_canvas(connection_id)
    canvas["nodes"][0]["columns"][1]["visible"] = False
    canvas["nodes"][0]["data"]["columns"][1]["visible"] = False
    project_id = ctx.create_project(
        name="e2e_v2_test_076_hidden_columns",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    rows = ctx.preview_project(project_id, limit=3).get("rows") or []
    assert_true(rows, "Preview must return rows")
    assert_true("city_id" not in rows[0], "Hidden source column must not appear in preview rows")
    ctx.execute_project(project_id)
    contract_names = [c["physical_name"] for c in (ctx.get_project(project_id).get("output_contract") or {}).get("columns", [])]
    assert_true("city_id" not in contract_names, "Hidden source column must not appear in output_contract")


def test_077(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "test_077")
    canvas = {
        "nodes": [
            source_node(
                node_id="customers",
                table_name="customers",
                alias="c",
                connection_id=connection_id,
                columns=[source_column("id", logical_type="int"), source_column("city_id", logical_type="int")],
            ),
            source_node(
                node_id="cities",
                table_name="cities",
                alias="ci",
                connection_id=connection_id,
                columns=[source_column("id", logical_type="int"), source_column("name", logical_type="str")],
            ),
            result_node(format="csv", fileName="e2e_duplicate_join_names"),
        ],
        "edges": [
            {"id": "e-c-ci", "source": "customers", "target": "cities", "join_type": "inner", "source_column": "city_id", "target_column": "id"},
            edge("e-cities-result", "cities", "result-1"),
        ],
    }
    project_id = ctx.create_project(
        name="e2e_v2_test_077_duplicate_names",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    rows = ctx.preview_project(project_id, limit=2).get("rows") or []
    assert_true(rows, "Duplicate-name join preview must return rows")
    assert_equal(len(rows[0].keys()), len(set(rows[0].keys())), "Preview row keys must be unique after duplicate SQL names")


def test_078(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "test_078")
    project_id = ctx.create_project(
        name="e2e_v2_test_078_csv_download",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_customers_canvas(connection_id),
    )
    ctx.execute_project(project_id)
    rows = _read_csv_bytes(ctx.api_raw("GET", f"/projects/{project_id}/download"))
    assert_equal(len(rows), 22, "CSV project download must contain every fixture customer row")
    assert_equal(list(rows[0].keys()), ["customer_id", "city_id", "customer_name", "age", "status"], "CSV header must match output contract")


def test_079(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "test_079")
    project_id = ctx.create_project(
        name="e2e_v2_test_079_xlsx_download",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_customers_canvas(connection_id, fmt="xlsx"),
    )
    ctx.execute_project(project_id)
    raw = ctx.api_raw("GET", f"/projects/{project_id}/download")
    assert_equal(raw[:2], b"PK", "XLSX download must be an OOXML zip payload")


def test_080(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "test_080")
    project_id = ctx.create_project(
        name="e2e_v2_test_080_contract_download_alignment",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_customers_active_canvas(connection_id),
    )
    ctx.execute_project(project_id)
    contract_names = [c["physical_name"] for c in (ctx.get_project(project_id).get("output_contract") or {}).get("columns", [])]
    rows = _read_csv_bytes(ctx.api_raw("GET", f"/projects/{project_id}/download"))
    assert_equal(list(rows[0].keys()), contract_names, "Downloaded CSV header must align with output_contract")
    assert_equal(len(rows), 17, "Downloaded CSV must respect the executed ESTRAI source filter")


def test_081(ctx: E2EContext) -> None:
    upstream_id = ctx.create_project(name="e2e_v2_test_081_upstream", project_type="manipola")
    set_output_contract(
        upstream_id,
        output_contract(
            [contract_column(column_id="col-081-id", source_name="id", physical_name="id")],
            producer_project_id=upstream_id,
        ),
    )
    source = project_source_node(
        node_id="source-1",
        source_project_id=upstream_id,
        column_id="col-081-id",
        name="id",
        source_name="id",
    )
    rename = transform_node(
        node_id="rename-1",
        transform_op="rename",
        config={"renames": [{"old_name": "id", "old_name_id": "col-081-id", "new_name": "myid"}]},
    )
    downstream_id = ctx.create_project(
        name="e2e_v2_test_081_downstream",
        project_type="manipola",
        canvas={"nodes": [source, rename, result_node()], "edges": [edge("e1", "source-1", "rename-1"), edge("e2", "rename-1", "result-1")]},
    )
    set_output_contract(
        upstream_id,
        output_contract(
            [contract_column(column_id="col-081-id", source_name="id", physical_name="customer_id")],
            producer_project_id=upstream_id,
        ),
    )
    ctx.update_canvas(downstream_id, ctx.get_project(downstream_id)["canvas"])
    rename_config = {n["id"]: n for n in ctx.get_project(downstream_id)["canvas"]["nodes"]}["rename-1"]["config"]
    assert_equal(rename_config["renames"][0]["old_name"], "customer_id", "Rename input must follow upstream contract rename by column id")


def test_082(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "test_082")
    upstream_id = ctx.create_project(
        name="e2e_v2_test_082_upstream_estrai",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_customers_active_canvas(connection_id),
    )
    ctx.execute_project(upstream_id)
    contract = ctx.get_project(upstream_id).get("output_contract") or {}
    source = project_source_node_from_contract(node_id="source-1", source_project_id=upstream_id, contract=contract)
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={"formula": "age*3", "new_column": "age_x3", "formula_column_refs": [{"id": next(c["id"] for c in source["columns"] if c["name"] == "age"), "name": "age"}]},
    )
    manipola_id = ctx.create_project(
        name="e2e_v2_test_082_mid_manipola",
        project_type="manipola",
        canvas={"nodes": [source, formula, result_node(format="csv", fileName="e2e_mid")], "edges": [edge("e1", "source-1", "formula-1"), edge("e2", "formula-1", "result-1")]},
    )
    ctx.execute_project(manipola_id)
    mid_contract = ctx.get_project(manipola_id).get("output_contract") or {}
    downstream = project_source_node_from_contract(node_id="source-2", source_project_id=manipola_id, contract=mid_contract)
    downstream_id = ctx.create_project(
        name="e2e_v2_test_082_downstream_manipola",
        project_type="manipola",
        canvas={"nodes": [downstream, result_node()], "edges": [edge("e1", "source-2", "result-1")]},
    )
    rows = ctx.preview_project(downstream_id, limit=3).get("rows") or []
    assert_true("age_x3" in rows[0], "Manipola -> Manipola must exchange output_contract and materialized data")


def test_083(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "test_083")
    upstream_id = ctx.create_project(
        name="e2e_v2_test_083_join_upstream",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas={
            "nodes": [
                source_node(node_id="customers", table_name="customers", alias="c", connection_id=connection_id, columns=[source_column("id", logical_type="int", alias="customer_id"), source_column("city_id", logical_type="int"), source_column("age", logical_type="int")]),
                source_node(node_id="cities", table_name="cities", alias="ci", connection_id=connection_id, columns=[source_column("id", logical_type="int", visible=False), source_column("name", logical_type="str", alias="city_name")]),
                result_node(format="csv", fileName="e2e_join_upstream"),
            ],
            "edges": [{"id": "e-c-ci", "source": "customers", "target": "cities", "join_type": "inner", "source_column": "city_id", "target_column": "id"}, edge("e-ci-result", "cities", "result-1")],
        },
    )
    ctx.execute_project(upstream_id)
    contract = ctx.get_project(upstream_id).get("output_contract") or {}
    source = project_source_node_from_contract(node_id="source-1", source_project_id=upstream_id, contract=contract)
    age_id = next(c["id"] for c in source["columns"] if c["name"] == "age")
    formula = transform_node(node_id="formula-1", transform_op="formula", config={"formula": "age*2", "new_column": "double_age", "formula_column_refs": [{"id": age_id, "name": "age"}]})
    downstream_id = ctx.create_project(
        name="e2e_v2_test_083_join_to_manipola",
        project_type="manipola",
        canvas={"nodes": [source, formula, result_node()], "edges": [edge("e1", "source-1", "formula-1"), edge("e2", "formula-1", "result-1")]},
    )
    rows = ctx.preview_project(downstream_id, limit=5).get("rows") or []
    assert_equal([(r["customer_id"], r["city_name"], r["double_age"]) for r in rows[:3]], [(1, "Milano", 68), (2, "Milano", 82), (3, "Milano", 56)], "EstrAi joined output must feed Manipola formula deterministically")


def test_084(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "test_084")
    upstream_id = ctx.create_project(name="e2e_v2_test_084_upstream", project_type="estrai", default_connection_id=connection_id, canvas=_customers_active_canvas(connection_id))
    ctx.execute_project(upstream_id)
    contract = ctx.get_project(upstream_id).get("output_contract") or {}
    source = project_source_node_from_contract(node_id="source-1", source_project_id=upstream_id, contract=contract)
    status_id = next(c["id"] for c in source["columns"] if c["name"] == "status")
    aggregate = transform_node(
        node_id="aggregate-1",
        transform_op="aggregate",
        config={"group_by": ["status"], "group_by_columns_column_ids": [status_id], "aggregations": [{"column": "age", "function": "mean", "alias": "avg_age"}]},
    )
    project_id = ctx.create_project(
        name="e2e_v2_test_084_manipola_aggregate_contract",
        project_type="manipola",
        canvas={"nodes": [source, aggregate, result_node()], "edges": [edge("e1", "source-1", "aggregate-1"), edge("e2", "aggregate-1", "result-1")]},
    )
    ctx.execute_project(project_id)
    names = [c["physical_name"] for c in (ctx.get_project(project_id).get("output_contract") or {}).get("columns", [])]
    assert_equal(names, ["status", "avg_age"], "Manipola aggregate execute must persist compact output_contract")


def test_085(ctx: E2EContext) -> None:
    _join_type_case(ctx, "test_085", "inner", 20)


def test_086(ctx: E2EContext) -> None:
    _join_type_case(ctx, "test_086", "right", 32)


def test_087(ctx: E2EContext) -> None:
    _join_type_case(ctx, "test_087", "outer", 34)


def _join_type_case(ctx: E2EContext, suffix: str, how: str, expected_count: int) -> None:
    connection_id = _connection(ctx, suffix)
    customers_id = ctx.create_project(name=f"e2e_v2_{suffix}_customers", project_type="estrai", default_connection_id=connection_id, canvas=_customers_canvas(connection_id))
    cities_id = ctx.create_project(name=f"e2e_v2_{suffix}_cities", project_type="estrai", default_connection_id=connection_id, canvas=_cities_canvas(connection_id))
    ctx.execute_project(customers_id)
    ctx.execute_project(cities_id)
    customers = project_source_node_from_contract(node_id="customers-source", source_project_id=customers_id, contract=ctx.get_project(customers_id).get("output_contract") or {})
    cities = project_source_node_from_contract(node_id="cities-source", source_project_id=cities_id, contract=ctx.get_project(cities_id).get("output_contract") or {})
    join = transform_node(node_id="join-1", transform_op="join", config={"left_on": "city_id", "right_on": "city_id", "how": how})
    project_id = ctx.create_project(
        name=f"e2e_v2_{suffix}_manipola_join_{how}",
        project_type="manipola",
        canvas={"nodes": [customers, cities, join, result_node()], "edges": [edge("e1", "customers-source", "join-1"), edge("e2", "cities-source", "join-1"), edge("e3", "join-1", "result-1")]},
    )
    rows = ctx.preview_project(project_id, limit=100).get("rows") or []
    assert_equal(len(rows), expected_count, f"Manipola {how} join must return deterministic fixture row count")


def test_088(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "test_088")
    upstream_id = ctx.create_project(name="e2e_v2_test_088_upstream", project_type="estrai", default_connection_id=connection_id, canvas=_customers_canvas(connection_id))
    ctx.execute_project(upstream_id)
    source = project_source_node_from_contract(node_id="source-1", source_project_id=upstream_id, contract=ctx.get_project(upstream_id).get("output_contract") or {})
    formula = transform_node(node_id="formula-1", transform_op="formula", config={"formula": "missing_col*2", "new_column": "bad_calc"})
    project_id = ctx.create_project(
        name="e2e_v2_test_088_bad_formula",
        project_type="manipola",
        canvas={"nodes": [source, formula, result_node()], "edges": [edge("e1", "source-1", "formula-1"), edge("e2", "formula-1", "result-1")]},
    )
    try:
        ctx.preview_project(project_id, limit=5)
    except AssertionError as exc:
        assert_true("missing_col" in str(exc) or "Formula" in str(exc), "Bad formula must fail with a readable message")
        return
    fail("Bad formula preview unexpectedly succeeded")


def test_089(ctx: E2EContext) -> None:
    upstream_id = ctx.create_project(name="e2e_v2_test_089_upstream", project_type="estrai")
    set_output_contract(
        upstream_id,
        output_contract([
            contract_column(column_id="col-089-id", source_name="id", physical_name="id"),
            contract_column(column_id="col-089-z", source_name="z", physical_name="z"),
        ], producer_project_id=upstream_id),
    )
    source = project_source_node(node_id="source-1", source_project_id=upstream_id, column_id="col-089-id", name="id", source_name="id")
    source["columns"].append({"id": "col-089-z", "name": "z", "source_name": "z", "display_name": "z", "type": "integer", "visible": True})
    source["data"]["columns"] = source["columns"]
    drop = transform_node(node_id="drop-1", transform_op="drop_columns", config={"columns": ["z"], "columns_column_ids": ["col-089-z"]})
    downstream_id = ctx.create_project(name="e2e_v2_test_089_downstream", project_type="manipola", canvas={"nodes": [source, drop, result_node()], "edges": [edge("e1", "source-1", "drop-1"), edge("e2", "drop-1", "result-1")]})
    set_output_contract(upstream_id, output_contract([contract_column(column_id="col-089-id", source_name="id", physical_name="id")], producer_project_id=upstream_id))
    ctx.update_canvas(downstream_id, ctx.get_project(downstream_id)["canvas"])
    nodes = {n["id"]: n for n in ctx.get_project(downstream_id)["canvas"]["nodes"]}
    assert_equal([c["name"] for c in nodes["source-1"]["columns"]], ["id"], "Removed upstream column must disappear from project_source")
    assert_equal(nodes["drop-1"]["config"]["columns"], [], "Transform config must not keep stale removed column")


def test_090(ctx: E2EContext) -> None:
    upstream_id = ctx.create_project(name="e2e_v2_test_090_upstream", project_type="estrai")
    set_output_contract(upstream_id, output_contract([contract_column(column_id="col-090-id", source_name="id", physical_name="id")], producer_project_id=upstream_id))
    source = project_source_node(node_id="source-1", source_project_id=upstream_id, column_id="col-090-id", name="id", source_name="id")
    formula = transform_node(node_id="formula-1", transform_op="formula", config={"formula": "id*2", "new_column": "double_id", "formula_column_refs": [{"id": "col-090-id", "name": "id"}]})
    downstream_id = ctx.create_project(name="e2e_v2_test_090_downstream", project_type="manipola", canvas={"nodes": [source, formula, result_node()], "edges": [edge("e1", "source-1", "formula-1"), edge("e2", "formula-1", "result-1")]})
    set_output_contract(upstream_id, output_contract([contract_column(column_id="col-090-id", source_name="id", physical_name="myid")], producer_project_id=upstream_id))
    ctx.update_canvas(downstream_id, ctx.get_project(downstream_id)["canvas"])
    formula_config = {n["id"]: n for n in ctx.get_project(downstream_id)["canvas"]["nodes"]}["formula-1"]["config"]
    assert_equal(formula_config["formula"], "myid*2", "Formula text must follow upstream alias rename by stable column id")
    assert_equal(formula_config["formula_column_refs"], [{"id": "col-090-id", "name": "myid"}], "Formula refs must retain id and current display name")


TESTS: dict[str, Callable[[E2EContext], None]] = {
    f"test_{i:03d}": globals()[f"test_{i:03d}"]
    for i in range(71, 91)
}


def run_case(case_id: str) -> None:
    run_test(TESTS[case_id])
