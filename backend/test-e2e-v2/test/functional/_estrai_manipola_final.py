"""Final ESTRAI/MANIPOLA/output_contract perimeter coverage.

These cases close gaps that are not just more combinations of the same matrix:
- ESTRAI calculated columns as public contract fields;
- duplicate public names and visibility at project_source boundaries;
- project_source freshness/staleness behaviour;
- force_recalculate semantics for stale upstreams;
- MANIPOLA output_contract id stability for calculated and renamed outputs.
"""

from __future__ import annotations

import ast
import copy
import time
from typing import Any, Callable

from _e2e import (
    E2EContext,
    assert_equal,
    assert_true,
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


CaseFn = Callable[[], None]


def _ctx_case(fn: Callable[[E2EContext], None]) -> CaseFn:
    return lambda: run_test(fn)


def _connection(ctx: E2EContext, suffix: str = "pg") -> str:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name(f"e2e_v2_final_em_{suffix}"))
    )
    tested = ctx.test_connection(connection_id)
    assert_true(tested.get("success"), "Postgres fixture connection must be reachable")
    return connection_id


def _active_customer_source(connection_id: str) -> dict[str, Any]:
    return source_node(
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
    )


def _active_customers_canvas(connection_id: str, *, calculated: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    source = _active_customer_source(connection_id)
    if calculated:
        source["calculated_columns"] = calculated
        source["data"]["calculated_columns"] = calculated
    return {
        "nodes": [
            source,
            result_node(
                format="csv",
                fileName="e2e_final_em_active_customers",
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


def _create_executed_estrai(
    ctx: E2EContext,
    *,
    connection_id: str,
    name: str,
    canvas: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    project_id = ctx.create_project(
        name=name,
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    result = ctx.execute_project(project_id)
    contract = ctx.get_project(project_id).get("output_contract") or result.get("output_contract") or {}
    assert_true(contract.get("columns"), f"{name}: upstream must persist output_contract")
    return project_id, contract, result


def _source_result_canvas(source_project_id: str, contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodes": [
            project_source_node_from_contract(
                node_id="source-1",
                source_project_id=source_project_id,
                contract=contract,
            ),
            result_node(format="csv", fileName="e2e_final_em_downstream"),
        ],
        "edges": [edge("e-source-result", "source-1", "result-1")],
    }


def _columns_by_physical(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(column.get("physical_name")): column
        for column in contract.get("columns") or []
        if isinstance(column, dict) and column.get("physical_name")
    }


def _node_by_id(canvas: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(node.get("id")): node for node in canvas.get("nodes") or [] if isinstance(node, dict)}


def _wait_task_terminal(ctx: E2EContext, task_id: str) -> dict[str, Any]:
    deadline = time.time() + 90
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        status = ctx.api_json("GET", f"/tasks/{task_id}", expected=(200, 404))
        if not isinstance(status, dict):
            raise AssertionError(f"Task response is not an object: {status!r}")
        last = status
        state = str(status.get("status") or "").lower()
        if state in {"success", "failure", "failed", "revoked"}:
            return status
        time.sleep(0.5)
    raise AssertionError(f"Task {task_id} timed out: {last!r}")


def _execute_project_raw(ctx: E2EContext, project_id: str, *, force_recalculate: bool) -> dict[str, Any]:
    # Gate canvas-validation attivo (Epic CanvasVal §12): un progetto non validato
    # non parte (409). update_canvas azzera canvas_validated, quindi prima di
    # (ri)eseguire validiamo come il pulsante Esegui del FE (salva→valida→esegui).
    ctx.validate_project(project_id)
    task = ctx.api_json(
        "POST",
        f"/projects/{project_id}/execute",
        payload={"force_recalculate": force_recalculate},
    )
    task_id = str(task.get("task_id") or "")
    assert_true(task_id, f"execute response must expose task_id: {task!r}")
    status = _wait_task_terminal(ctx, task_id)
    result = status.get("result")
    if not isinstance(result, dict) and status.get("error"):
        try:
            parsed_error = ast.literal_eval(str(status.get("error")))
        except (SyntaxError, ValueError):
            parsed_error = None
        if isinstance(parsed_error, dict):
            result = parsed_error
    assert_true(isinstance(result, dict), f"execute task must expose result: {status!r}")
    return result


def test_451(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "calc_exchange")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_451_estrai_calc_contract",
        canvas=_active_customers_canvas(
            connection_id,
            calculated=[{"name": "double_age", "expression": "c.age * 2", "output_type": "int"}],
        ),
    )
    by_physical = _columns_by_physical(contract)
    assert_true("double_age" in by_physical, "ESTRAI calculated column must enter output_contract")

    downstream_id = ctx.create_project(
        name="e2e_v2_test_451_manipola_reads_calc",
        project_type="manipola",
        canvas=_source_result_canvas(upstream_id, contract),
    )
    rows = ctx.preview_project(downstream_id, limit=10).get("rows") or []
    assert_true(rows, "MANIPOLA must read ESTRAI calculated column via output_contract")
    for row in rows[:5]:
        assert_equal(int(float(row["double_age"])), int(float(row["age"])) * 2, "Calculated value must survive project exchange")


def test_452(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "calc_id")
    canvas = _active_customers_canvas(
        connection_id,
        calculated=[{"name": "age_bonus", "expression": "c.age + 1", "output_type": "int"}],
    )
    upstream_id, contract1, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_452_calc_id_stable",
        canvas=canvas,
    )
    first = _columns_by_physical(contract1)["age_bonus"]

    next_canvas = copy.deepcopy(canvas)
    next_canvas["nodes"][0]["calculated_columns"][0]["expression"] = "c.age + 2"
    next_canvas["nodes"][0]["data"]["calculated_columns"][0]["expression"] = "c.age + 2"
    ctx.update_canvas(upstream_id, next_canvas)
    ctx.execute_project(upstream_id)
    contract2 = ctx.get_project(upstream_id).get("output_contract") or {}
    second = _columns_by_physical(contract2)["age_bonus"]
    assert_equal(second["id"], first["id"], "ESTRAI calculated output id must stay stable across expression edits")
    assert_true(int(contract2.get("revision") or 0) > int(contract1.get("revision") or 0), "Output contract revision must advance on re-execute")


def test_453(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "dup_names")
    source = source_node(
        node_id="customers",
        table_name="customers",
        alias="c",
        connection_id=connection_id,
        columns=[
            source_column("id", logical_type="int", alias="dup"),
            source_column("city_id", logical_type="int", alias="dup"),
            source_column("age", logical_type="int", alias="age"),
        ],
        filters=[{"column": "status", "operator": "=", "value": "active"}],
    )
    project_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_453_duplicate_alias_contract",
        canvas={
            "nodes": [source, result_node(format="csv", fileName="e2e_final_em_duplicate_alias")],
            "edges": [edge("e-customers-result", "customers", "result-1")],
        },
    )
    physical = [column["physical_name"] for column in contract.get("columns") or []]
    assert_true(physical[0] == "dup" and physical[1].startswith("dup_") and physical[1] != "dup", "Duplicate ESTRAI aliases must become unique public contract names")
    assert_equal(len(physical), len(set(physical)), "ESTRAI output_contract physical names must be unique")
    rows = ctx.preview_project(project_id, limit=3).get("rows") or []
    assert_true(all({physical[0], physical[1], "age"} <= set(row) for row in rows), "Preview rows must match unique public names")


def test_454(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "visibility")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_454_visibility_upstream",
        canvas=_active_customers_canvas(connection_id),
    )
    source = project_source_node_from_contract(
        node_id="source-1",
        source_project_id=upstream_id,
        contract=contract,
    )
    for column in source["columns"]:
        if column.get("name") == "age":
            column["visible"] = False
    source["data"]["columns"] = copy.deepcopy(source["columns"])
    downstream_id = ctx.create_project(
        name="e2e_v2_test_454_hidden_project_source_column",
        project_type="manipola",
        canvas={
            "nodes": [source, result_node(format="csv", fileName="e2e_final_em_hidden_age")],
            "edges": [edge("e-source-result", "source-1", "result-1")],
        },
    )
    rows = ctx.preview_project(downstream_id, limit=5).get("rows") or []
    assert_true(rows, "Downstream preview must return rows")
    assert_true("age" not in rows[0], "Invisible project_source column must not be exchanged as data")
    ctx.execute_project(downstream_id)
    downstream_contract = ctx.get_project(downstream_id).get("output_contract") or {}
    assert_true("age" not in _columns_by_physical(downstream_contract), "Invisible project_source column must stay out of downstream output_contract")


def test_455(ctx: E2EContext) -> None:
    upstream_id = ctx.create_project(name="e2e_v2_test_455_missing_result_upstream", project_type="estrai")
    manual_contract = {
        "schema_version": 1,
        "producer_project_id": upstream_id,
        "revision": 1,
        "columns": [
            {"id": "col-id", "source_name": "id", "physical_name": "id", "display_name": "id", "type": {"logical": "integer", "nullable": True}, "ordinal": 0, "status": "active"},
        ],
    }
    from _e2e import set_output_contract

    set_output_contract(upstream_id, manual_contract)
    downstream_id = ctx.create_project(
        name="e2e_v2_test_455_missing_result_freshness",
        project_type="manipola",
        canvas=_source_result_canvas(upstream_id, manual_contract),
    )
    project = ctx.get_project(downstream_id)
    source = _node_by_id(project["canvas"])["source-1"]
    freshness = source.get("source_freshness") or (source.get("data") or {}).get("source_freshness")
    assert_true(freshness and freshness.get("stale"), "GET project must annotate missing-result project_source as stale")
    assert_equal(freshness.get("reason"), "missing_result", "Missing upstream result freshness reason")


def test_456(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "stale_annotation")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_456_stale_source_upstream",
        canvas=_active_customers_canvas(connection_id),
    )
    downstream_id = ctx.create_project(
        name="e2e_v2_test_456_stale_source_downstream",
        project_type="manipola",
        canvas=_source_result_canvas(upstream_id, contract),
    )
    fresh_project = ctx.get_project(downstream_id)
    fresh = (_node_by_id(fresh_project["canvas"])["source-1"].get("data") or {}).get("source_freshness")
    assert_true(fresh and not fresh.get("stale"), "Fresh project_source must be annotated as non-stale")

    time.sleep(1.1)
    stale_canvas = _active_customers_canvas(connection_id)
    stale_canvas["nodes"][1]["fileName"] = "e2e_final_em_active_customers_after_edit"
    ctx.update_canvas(upstream_id, stale_canvas)
    stale_project = ctx.get_project(downstream_id)
    stale = (_node_by_id(stale_project["canvas"])["source-1"].get("data") or {}).get("source_freshness")
    assert_true(stale and stale.get("stale"), "GET project must annotate edited upstream as stale")
    assert_equal(stale.get("reason"), "canvas_newer_than_result", "Edited upstream freshness reason")


def test_457(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "stale_fail")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_457_stale_fail_upstream",
        canvas=_active_customers_canvas(connection_id),
    )
    downstream_id = ctx.create_project(
        name="e2e_v2_test_457_stale_fail_downstream",
        project_type="manipola",
        canvas=_source_result_canvas(upstream_id, contract),
    )
    time.sleep(1.1)
    ctx.update_canvas(upstream_id, _active_customers_canvas(connection_id))
    result = _execute_project_raw(ctx, downstream_id, force_recalculate=False)
    assert_true(result.get("success") is False, "Stale project_source must fail when force_recalculate=false")
    assert_true("modificato dopo" in str(result.get("message") or ""), "Stale failure must explain source was modified")


def test_458(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "stale_recalc")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_458_stale_recalc_upstream",
        canvas=_active_customers_canvas(connection_id),
    )
    downstream_id = ctx.create_project(
        name="e2e_v2_test_458_stale_recalc_downstream",
        project_type="manipola",
        canvas=_source_result_canvas(upstream_id, contract),
    )
    time.sleep(1.1)
    pending_canvas = _active_customers_canvas(connection_id)
    pending_canvas["nodes"][0]["filters"] = [{"column": "status", "operator": "=", "value": "pending"}]
    pending_canvas["nodes"][0]["data"]["filters"] = [{"column": "status", "operator": "=", "value": "pending"}]
    ctx.update_canvas(upstream_id, pending_canvas)
    result = _execute_project_raw(ctx, downstream_id, force_recalculate=True)
    assert_true(result.get("success"), f"force_recalculate=true must recompute stale upstream: {result!r}")
    assert_equal(result.get("rows_count"), 2, "Forced recompute must exchange the freshly recalculated upstream rows")


def test_459(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "manipola_id")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_459_manipola_id_upstream",
        canvas=_active_customers_canvas(connection_id),
    )
    age_id = _columns_by_physical(contract)["age"]["id"]
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={
            "formula": "age * 2",
            "new_column": "double_age",
            "output_type": "int",
            "formula_column_refs": [{"id": age_id, "name": "age"}],
        },
    )
    manipola_id = ctx.create_project(
        name="e2e_v2_test_459_manipola_contract_id_stable",
        project_type="manipola",
        canvas={
            "nodes": [
                project_source_node_from_contract(node_id="source-1", source_project_id=upstream_id, contract=contract),
                formula,
                result_node(format="csv", fileName="e2e_final_em_double_age"),
            ],
            "edges": [
                edge("e-source-formula", "source-1", "formula-1"),
                edge("e-formula-result", "formula-1", "result-1"),
            ],
        },
    )
    ctx.execute_project(manipola_id)
    contract1 = ctx.get_project(manipola_id).get("output_contract") or {}
    first = _columns_by_physical(contract1)["double_age"]
    ctx.execute_project(manipola_id)
    contract2 = ctx.get_project(manipola_id).get("output_contract") or {}
    second = _columns_by_physical(contract2)["double_age"]
    assert_equal(second["id"], first["id"], "MANIPOLA calculated output id must stay stable across re-execute")
    assert_true(int(contract2.get("revision") or 0) > int(contract1.get("revision") or 0), "MANIPOLA output_contract revision must advance")


def test_460(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "manipola_rename_id")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_460_upstream",
        canvas=_active_customers_canvas(connection_id),
    )
    age_id = _columns_by_physical(contract)["age"]["id"]
    source = project_source_node_from_contract(node_id="source-1", source_project_id=upstream_id, contract=contract)
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={"formula": "age * 2", "new_column": "double_age", "output_type": "int", "formula_column_refs": [{"id": age_id, "name": "age"}]},
    )
    rename = transform_node(
        node_id="rename-1",
        transform_op="rename",
        config={"renames": [{"old_name": "double_age", "new_name": "age_x"}]},
        position={"x": 480, "y": 0},
    )
    canvas = {
        "nodes": [source, formula, rename, result_node(format="csv", fileName="e2e_final_em_rename_calc")],
        "edges": [
            edge("e-source-formula", "source-1", "formula-1"),
            edge("e-formula-rename", "formula-1", "rename-1"),
            edge("e-rename-result", "rename-1", "result-1"),
        ],
    }
    manipola_id = ctx.create_project(
        name="e2e_v2_test_460_manipola_rename_id",
        project_type="manipola",
        canvas=canvas,
    )
    ctx.execute_project(manipola_id)
    contract1 = ctx.get_project(manipola_id).get("output_contract") or {}
    first = _columns_by_physical(contract1)["age_x"]

    next_canvas = copy.deepcopy(canvas)
    rename_node = _node_by_id(next_canvas)["rename-1"]
    rename_node["config"]["renames"][0]["new_name"] = "age_y"
    rename_node["data"]["config"]["renames"][0]["new_name"] = "age_y"
    ctx.update_canvas(manipola_id, next_canvas)
    ctx.execute_project(manipola_id)
    contract2 = ctx.get_project(manipola_id).get("output_contract") or {}
    second = _columns_by_physical(contract2)["age_y"]
    assert_equal(second["id"], first["id"], "MANIPOLA rename of calculated output must preserve semantic column id")


def test_461(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "manipola_downstream_rename")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_461_upstream",
        canvas=_active_customers_canvas(connection_id),
    )
    age_id = _columns_by_physical(contract)["age"]["id"]
    source = project_source_node_from_contract(node_id="source-1", source_project_id=upstream_id, contract=contract)
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={"formula": "age * 2", "new_column": "double_age", "output_type": "int", "formula_column_refs": [{"id": age_id, "name": "age"}]},
    )
    rename = transform_node(
        node_id="rename-1",
        transform_op="rename",
        config={"renames": [{"old_name": "double_age", "new_name": "age_public"}]},
        position={"x": 480, "y": 0},
    )
    producer_canvas = {
        "nodes": [source, formula, rename, result_node(format="csv", fileName="e2e_final_em_producer")],
        "edges": [
            edge("e-source-formula", "source-1", "formula-1"),
            edge("e-formula-rename", "formula-1", "rename-1"),
            edge("e-rename-result", "rename-1", "result-1"),
        ],
    }
    producer_id = ctx.create_project(
        name="e2e_v2_test_461_manipola_producer",
        project_type="manipola",
        canvas=producer_canvas,
    )
    ctx.execute_project(producer_id)
    producer_contract = ctx.get_project(producer_id).get("output_contract") or {}
    public_id = _columns_by_physical(producer_contract)["age_public"]["id"]
    downstream_formula = transform_node(
        node_id="formula-down",
        transform_op="formula",
        config={
            "formula": "age_public + 1",
            "new_column": "age_public_plus_one",
            "formula_column_refs": [{"id": public_id, "name": "age_public"}],
        },
    )
    downstream_id = ctx.create_project(
        name="e2e_v2_test_461_downstream_follow_manipola_rename",
        project_type="manipola",
        canvas={
            "nodes": [
                project_source_node_from_contract(node_id="source-down", source_project_id=producer_id, contract=producer_contract),
                downstream_formula,
                result_node(format="csv", fileName="e2e_final_em_downstream_follow"),
            ],
            "edges": [
                edge("e-source-formula", "source-down", "formula-down"),
                edge("e-formula-result", "formula-down", "result-1"),
            ],
        },
    )

    next_canvas = copy.deepcopy(producer_canvas)
    rename_node = _node_by_id(next_canvas)["rename-1"]
    rename_node["config"]["renames"][0]["new_name"] = "age_final"
    rename_node["data"]["config"]["renames"][0]["new_name"] = "age_final"
    ctx.update_canvas(producer_id, next_canvas)
    ctx.execute_project(producer_id)
    synced = ctx.get_project(downstream_id)["canvas"]
    config = _node_by_id(synced)["formula-down"]["config"]
    assert_equal(config["formula"], "age_final + 1", "Downstream formula must follow MANIPOLA producer rename by stable id")
    assert_equal(config["formula_column_refs"], [{"id": public_id, "name": "age_final"}], "Downstream formula refs must preserve producer column id")


CASES: dict[str, CaseFn] = {
    "test_451": _ctx_case(test_451),
    "test_452": _ctx_case(test_452),
    "test_453": _ctx_case(test_453),
    "test_454": _ctx_case(test_454),
    "test_455": _ctx_case(test_455),
    "test_456": _ctx_case(test_456),
    "test_457": _ctx_case(test_457),
    "test_458": _ctx_case(test_458),
    "test_459": _ctx_case(test_459),
    "test_460": _ctx_case(test_460),
    "test_461": _ctx_case(test_461),
}


def run_case(case_id: str) -> None:
    try:
        case = CASES[case_id]
    except KeyError as exc:
        raise SystemExit(f"Unknown final ESTRAI/MANIPOLA case: {case_id}") from exc
    case()
