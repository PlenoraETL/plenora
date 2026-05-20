"""ARRICCHISCI fake REST API coverage.

These cases exercise real ARRICCHISCI api_enrich nodes against the E2E fake
REST container, including output_contract exchange with ESTRAI/MANIPOLA and
contract-driven remapping of API input columns.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Callable

from _e2e import (
    E2EContext,
    assert_equal,
    assert_true,
    edge,
    ensure_plenora_test_network_access,
    project_source_node_from_contract,
    result_node,
    run_test,
    source_column,
    source_node,
    transform_node,
    unique_name,
)
from _arricchisci_contract_final import _create_executed_arricchisci
from _estrai_manipola_final import (
    _columns_by_physical,
    _connection,
    _create_executed_estrai,
    _execute_project_raw,
    _node_by_id,
)


CaseFn = Callable[[], None]
FAKE_REST_BASE_URL = os.environ.get(
    "PLENORA_E2E_FAKE_REST_BASE_URL",
    "http://plenora-test-fake-rest:8080",
).rstrip("/")
API_KEY = os.environ.get("PLENORA_E2E_FAKE_REST_API_KEY", "PLENORA_TEST_KEY")


def _ctx_case(fn: Callable[[E2EContext], None]) -> CaseFn:
    return lambda: run_test(fn)


def _fake_url(path: str) -> str:
    return f"{FAKE_REST_BASE_URL}{path}"


def _output_mapping(*columns: tuple[str, str]) -> list[dict[str, str]]:
    return [{"path": path, "column": column} for path, column in columns]


def _api_payload(
    *,
    name: str,
    path: str,
    output_mapping: list[dict[str, str]],
    auth_location: str = "query",
    parameters: list[dict[str, Any]] | None = None,
    static_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    auth = {
        "type": "api_key",
        "location": auth_location,
        "key_name": "api_key" if auth_location == "query" else "X-API-Key",
        "key_value": API_KEY,
    }
    return {
        "name": unique_name(name),
        "description": "E2E fake REST ARRICCHISCI coverage",
        "api_type": "enricher",
        "category": "e2e",
        "connection_type": "manual",
        "url": _fake_url(path),
        "method": "GET",
        "auth": auth,
        "parameters": parameters or [],
        "static_parameters": static_parameters or {},
        "output_mapping": output_mapping,
        "custom_headers": {},
        "options": {"rate_limit": 0},
    }


def _create_api(
    ctx: E2EContext,
    *,
    name: str,
    path: str = "/data/by_id/{item_id}",
    output_mapping: list[dict[str, str]] | None = None,
    auth_location: str = "query",
    parameters: list[dict[str, Any]] | None = None,
    static_parameters: dict[str, Any] | None = None,
) -> str:
    ensure_plenora_test_network_access()
    api_id = ctx.create_api_connection(
        _api_payload(
            name=name,
            path=path,
            output_mapping=output_mapping
            or _output_mapping(("email", "api_email"), ("city_id", "api_city_id"), ("status", "api_status")),
            auth_location=auth_location,
            parameters=parameters,
            static_parameters=static_parameters,
        )
    )
    if ("{" not in path or static_parameters) and path.startswith("/data/"):
        tested = ctx.api_json("POST", f"/api-connections/{api_id}/test")
        assert_true(tested.get("success"), f"Fake REST API connection must test cleanly: {tested!r}")
    return api_id


def _api_node(
    api_id: str,
    *,
    node_id: str = "api-1",
    param_mapping: dict[str, str] | None = None,
    output_mapping: list[dict[str, str]] | None = None,
    position: dict[str, int] | None = None,
) -> dict[str, Any]:
    node = {
        "id": node_id,
        "type": "api_enrich",
        "position": position or {"x": 280, "y": 0},
        "api_id": api_id,
        "param_mapping": {"item_id": "customer_id"} if param_mapping is None else param_mapping,
        "output_mapping": output_mapping
        or _output_mapping(("email", "api_email"), ("city_id", "api_city_id"), ("status", "api_status")),
    }
    node["data"] = {
        "api_id": node["api_id"],
        "param_mapping": copy.deepcopy(node["param_mapping"]),
        "output_mapping": copy.deepcopy(node["output_mapping"]),
    }
    return node


def _customers_canvas(
    connection_id: str,
    *,
    id_alias: str = "customer_id",
    extra_columns: list[dict[str, Any]] | None = None,
    value_filter: str = "1 AND 3",
) -> dict[str, Any]:
    columns = [
        source_column("id", logical_type="int", alias=id_alias),
        source_column("full_name", logical_type="str", alias="customer_name"),
        source_column("city_id", logical_type="int", alias="city_id"),
        source_column("status", logical_type="str", alias="source_status"),
    ]
    columns.extend(extra_columns or [])
    return {
        "nodes": [
            source_node(
                node_id="customers",
                table_name="customers",
                alias="c",
                connection_id=connection_id,
                columns=columns,
                filters=[{"column": "id", "operator": "BETWEEN", "value": value_filter}],
            ),
            result_node(format="csv", fileName="e2e_arr_api_customers"),
        ],
        "edges": [edge("e-customers-result", "customers", "result-1")],
    }


def _arr_api_canvas(
    source_project_id: str,
    contract: dict[str, Any],
    api_id: str,
    *,
    api_node: dict[str, Any] | None = None,
    file_name: str = "e2e_arr_api_result",
) -> dict[str, Any]:
    source = project_source_node_from_contract(
        node_id="source-1",
        source_project_id=source_project_id,
        contract=contract,
    )
    api = api_node or _api_node(api_id)
    return {
        "nodes": [
            source,
            api,
            result_node(format="csv", fileName=file_name),
        ],
        "edges": [
            edge("e-source-api", "source-1", api["id"]),
            edge("e-api-result", api["id"], "result-1"),
        ],
    }


def _create_upstream(ctx: E2EContext, suffix: str, *, id_alias: str = "customer_id") -> tuple[str, str, dict[str, Any]]:
    connection_id = _connection(ctx, suffix)
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name=unique_name(f"e2e_v2_{suffix}_estrai"),
        canvas=_customers_canvas(connection_id, id_alias=id_alias),
    )
    return connection_id, upstream_id, contract


def _preview_rows(ctx: E2EContext, project_id: str) -> list[dict[str, Any]]:
    rows = ctx.preview_project(project_id, limit=10).get("rows") or []
    assert_true(rows, "Project preview must return rows")
    return rows


def _cell(row: dict[str, Any], name: str) -> str:
    return str(row.get(name))


def test_474(ctx: E2EContext) -> None:
    _, upstream_id, contract = _create_upstream(ctx, "arr_api_basic")
    api_id = _create_api(ctx, name="e2e_v2_test_474_by_id")
    arr_id, arr_contract, result = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_474_arricchisci_api_by_id",
        canvas=_arr_api_canvas(upstream_id, contract, api_id),
    )
    assert_equal(result.get("rows_count"), 3, "api_enrich must preserve upstream row count")
    by_physical = _columns_by_physical(arr_contract)
    assert_true({"customer_id", "customer_name", "api_email", "api_city_id", "api_status"} <= set(by_physical), "api_enrich output_contract must include source and API columns")
    rows = _preview_rows(ctx, arr_id)
    assert_equal(rows[0]["api_email"], "anna.rossi@example.com", "api_enrich must map by_id email for row 1")
    assert_equal(_cell(rows[0], "api_city_id"), "1", "api_enrich must map numeric JSON output")


def test_475(ctx: E2EContext) -> None:
    _, upstream_id, contract = _create_upstream(ctx, "arr_api_to_man")
    api_id = _create_api(ctx, name="e2e_v2_test_475_by_id")
    arr_id, arr_contract, _ = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_475_arricchisci_api_producer",
        canvas=_arr_api_canvas(upstream_id, contract, api_id),
    )
    api_city_id = _columns_by_physical(arr_contract)["api_city_id"]["id"]
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={
            "formula": "api_city_id + 10",
            "new_column": "city_plus_ten",
            "output_type": "int",
            "formula_column_refs": [{"id": api_city_id, "name": "api_city_id"}],
        },
    )
    manipola_id = ctx.create_project(
        name="e2e_v2_test_475_manipola_consumes_arricchisci_api",
        project_type="manipola",
        canvas={
            "nodes": [
                project_source_node_from_contract(node_id="source-1", source_project_id=arr_id, contract=arr_contract),
                formula,
                result_node(format="csv", fileName="e2e_arr_api_to_manipola"),
            ],
            "edges": [
                edge("e-source-formula", "source-1", "formula-1"),
                edge("e-formula-result", "formula-1", "result-1"),
            ],
        },
    )
    ctx.execute_project(manipola_id)
    rows = _preview_rows(ctx, manipola_id)
    assert_equal(_cell(rows[0], "city_plus_ten"), "11", "MANIPOLA must calculate from ARRICCHISCI API output")


def test_476(ctx: E2EContext) -> None:
    connection_id, upstream_id, contract = _create_upstream(ctx, "arr_api_param_rename")
    api_id = _create_api(ctx, name="e2e_v2_test_476_by_id")
    arr_id = ctx.create_project(
        name="e2e_v2_test_476_arricchisci_param_mapping_rename",
        project_type="arricchisci",
        canvas=_arr_api_canvas(upstream_id, contract, api_id),
    )
    next_canvas = _customers_canvas(connection_id, id_alias="public_id")
    ctx.update_canvas(upstream_id, next_canvas)
    ctx.execute_project(upstream_id, force_recalculate=True)
    result = _execute_project_raw(ctx, arr_id, force_recalculate=True)
    assert_true(result.get("success"), f"api_enrich param_mapping must follow upstream rename: {result!r}")
    project = ctx.get_project(arr_id)
    api_node = _node_by_id(project["canvas"])["api-1"]
    assert_equal(api_node["param_mapping"]["item_id"], "public_id", "api_enrich param_mapping must be rewritten to renamed upstream column")
    rows = _preview_rows(ctx, arr_id)
    assert_equal(rows[0]["api_email"], "anna.rossi@example.com", "api_enrich must still call API after input column rename")


def test_477(ctx: E2EContext) -> None:
    connection_id, upstream_id, contract = _create_upstream(ctx, "arr_api_param_removed")
    api_id = _create_api(ctx, name="e2e_v2_test_477_by_id")
    arr_id = ctx.create_project(
        name="e2e_v2_test_477_arricchisci_param_mapping_removed",
        project_type="arricchisci",
        canvas=_arr_api_canvas(upstream_id, contract, api_id),
    )
    next_canvas = _customers_canvas(connection_id, id_alias="customer_id")
    next_canvas["nodes"][0]["columns"] = [c for c in next_canvas["nodes"][0]["columns"] if c.get("alias") != "customer_id"]
    next_canvas["nodes"][0]["data"]["columns"] = copy.deepcopy(next_canvas["nodes"][0]["columns"])
    ctx.update_canvas(upstream_id, next_canvas)
    ctx.execute_project(upstream_id, force_recalculate=True)
    result = _execute_project_raw(ctx, arr_id, force_recalculate=True)
    assert_true(result.get("success"), f"api_enrich must not crash when mapped input column is removed: {result!r}")
    project = ctx.get_project(arr_id)
    api_node = _node_by_id(project["canvas"])["api-1"]
    assert_equal(api_node["param_mapping"]["item_id"], "", "api_enrich removed input column must clear stale param_mapping value")
    rows = _preview_rows(ctx, arr_id)
    assert_true(all(row.get("api_email") in (None, "") for row in rows), "Removed API input column must leave API output empty")


def test_478(ctx: E2EContext) -> None:
    _, upstream_id, contract = _create_upstream(ctx, "arr_api_output_rename")
    api_id = _create_api(ctx, name="e2e_v2_test_478_by_id")
    arr_id, contract1, _ = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_478_arricchisci_api_output_rename",
        canvas=_arr_api_canvas(upstream_id, contract, api_id),
    )
    first = _columns_by_physical(contract1)["api_email"]
    ctx.update_api_connection(api_id, {"output_mapping": _output_mapping(("email", "api_email_public"), ("city_id", "api_city_id"), ("status", "api_status"))})
    ctx.execute_project(arr_id, force_recalculate=True)
    contract2 = ctx.get_project(arr_id).get("output_contract") or {}
    by_physical = _columns_by_physical(contract2)
    assert_true("api_email_public" in by_physical and "api_email" not in by_physical, "API output mapping rename must update output_contract")
    assert_equal(by_physical["api_email_public"]["id"], first["id"], "API output mapping rename must preserve stable output column id")


def test_479(ctx: E2EContext) -> None:
    _, upstream_id, contract = _create_upstream(ctx, "arr_api_header_auth")
    mapping = _output_mapping(("email", "api_email_header"), ("status", "api_status_header"))
    api_id = _create_api(
        ctx,
        name="e2e_v2_test_479_header_auth",
        output_mapping=mapping,
        auth_location="header",
    )
    arr_id, arr_contract, _ = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_479_arricchisci_header_auth",
        canvas=_arr_api_canvas(upstream_id, contract, api_id, api_node=_api_node(api_id, output_mapping=mapping), file_name="e2e_arr_api_header"),
    )
    assert_true({"api_email_header", "api_status_header"} <= set(_columns_by_physical(arr_contract)), "Header-auth API columns must enter output_contract")
    rows = _preview_rows(ctx, arr_id)
    assert_equal(rows[1]["api_email_header"], "marco.bianchi@example.com", "Header API key auth must enrich row 2")


def test_480(ctx: E2EContext) -> None:
    _, upstream_id, contract = _create_upstream(ctx, "arr_api_static_param")
    mapping = _output_mapping(("email", "fixed_email"), ("id", "fixed_id"))
    api_id = _create_api(
        ctx,
        name="e2e_v2_test_480_static_param",
        path="/data/by_id/{item_id}",
        output_mapping=mapping,
        static_parameters={"item_id": 2},
    )
    arr_id, _, _ = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_480_arricchisci_static_param",
        canvas=_arr_api_canvas(upstream_id, contract, api_id, api_node=_api_node(api_id, param_mapping={}, output_mapping=mapping)),
    )
    rows = _preview_rows(ctx, arr_id)
    assert_true(all(row["fixed_email"] == "marco.bianchi@example.com" for row in rows), "Static API parameters must be applied to every row")
    assert_true(all(_cell(row, "fixed_id") == "2" for row in rows), "Static API path parameter must survive URL substitution")


def test_481(ctx: E2EContext) -> None:
    _, upstream_id, contract = _create_upstream(ctx, "arr_api_parallel")
    email_mapping = _output_mapping(("email", "api_email"))
    status_mapping = _output_mapping(("status", "api_status_from_second"))
    api_email = _create_api(ctx, name="e2e_v2_test_481_email", output_mapping=email_mapping)
    api_status = _create_api(ctx, name="e2e_v2_test_481_status", output_mapping=status_mapping)
    source = project_source_node_from_contract(node_id="source-1", source_project_id=upstream_id, contract=contract)
    email_node = _api_node(api_email, node_id="api-email", output_mapping=email_mapping)
    status_node = _api_node(api_status, node_id="api-status", output_mapping=status_mapping, position={"x": 520, "y": 0})
    arr_id, arr_contract, _ = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_481_arricchisci_two_api_nodes",
        canvas={
            "nodes": [source, email_node, status_node, result_node(format="csv", fileName="e2e_arr_api_two_nodes")],
            "edges": [
                edge("e-source-email", "source-1", "api-email"),
                edge("e-email-status", "api-email", "api-status"),
                edge("e-status-result", "api-status", "result-1"),
            ],
        },
    )
    assert_true({"api_email", "api_status_from_second"} <= set(_columns_by_physical(arr_contract)), "Chained API nodes must expose both output mappings")
    rows = _preview_rows(ctx, arr_id)
    assert_equal(rows[2]["api_status_from_second"], "pending", "Second API node must enrich from chained API input")


def test_482(ctx: E2EContext) -> None:
    _, upstream_id, contract = _create_upstream(ctx, "arr_api_chain")
    first_mapping = _output_mapping(("city_id", "api_city_id"))
    second_mapping = _output_mapping(("email", "city_lookup_email"))
    api_first = _create_api(ctx, name="e2e_v2_test_482_first", output_mapping=first_mapping)
    api_second = _create_api(ctx, name="e2e_v2_test_482_second", output_mapping=second_mapping)
    source = project_source_node_from_contract(node_id="source-1", source_project_id=upstream_id, contract=contract)
    first = _api_node(api_first, node_id="api-first", output_mapping=first_mapping)
    second = _api_node(api_second, node_id="api-second", param_mapping={"item_id": "api_city_id"}, output_mapping=second_mapping, position={"x": 520, "y": 0})
    arr_id, _, _ = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_482_arricchisci_api_chain",
        canvas={
            "nodes": [source, first, second, result_node(format="csv", fileName="e2e_arr_api_chain")],
            "edges": [
                edge("e-source-first", "source-1", "api-first"),
                edge("e-first-second", "api-first", "api-second"),
                edge("e-second-result", "api-second", "result-1"),
            ],
        },
    )
    rows = _preview_rows(ctx, arr_id)
    assert_equal(rows[0]["city_lookup_email"], "anna.rossi@example.com", "Second API node must read column produced by first API node")


def test_483(ctx: E2EContext) -> None:
    _, upstream_id, contract = _create_upstream(ctx, "arr_api_preview")
    api_id = _create_api(ctx, name="e2e_v2_test_483_by_id")
    arr_id = ctx.create_project(
        name="e2e_v2_test_483_arricchisci_preview_execute_parity",
        project_type="arricchisci",
        canvas=_arr_api_canvas(upstream_id, contract, api_id),
    )
    preview_rows = _preview_rows(ctx, arr_id)
    ctx.execute_project(arr_id, force_recalculate=True)
    contract_after = ctx.get_project(arr_id).get("output_contract") or {}
    assert_true("api_email" in _columns_by_physical(contract_after), "Execute output_contract must include preview-visible API column")
    assert_equal(preview_rows[0]["api_email"], "anna.rossi@example.com", "Preview must enrich fake REST rows before execute")


def test_484(ctx: E2EContext) -> None:
    _, upstream_id, contract = _create_upstream(ctx, "arr_api_http_error")
    mapping = _output_mapping(("email", "api_email_from_error"))
    api_id = _create_api(
        ctx,
        name="e2e_v2_test_484_http_error",
        path="/error/500",
        output_mapping=mapping,
    )
    arr_id, arr_contract, result = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_484_arricchisci_http_error_isolated",
        canvas=_arr_api_canvas(upstream_id, contract, api_id, api_node=_api_node(api_id, output_mapping=mapping)),
    )
    assert_equal(result.get("rows_count"), 3, "HTTP error rows must not drop upstream rows")
    assert_true("api_email_from_error" in _columns_by_physical(arr_contract), "HTTP error output column must remain declared in contract")
    rows = _preview_rows(ctx, arr_id)
    assert_true(all(row.get("api_email_from_error") in (None, "") for row in rows), "HTTP error API output must stay empty without corrupting source rows")


def test_485(ctx: E2EContext) -> None:
    _, upstream_id, contract = _create_upstream(ctx, "arr_api_bad_auth")
    mapping = _output_mapping(("email", "api_email_bad_auth"))
    api_id = ctx.create_api_connection(
        _api_payload(
            name="e2e_v2_test_485_bad_auth",
            path="/data/by_id/{item_id}",
            output_mapping=mapping,
            static_parameters={},
        )
        | {"auth": {"type": "api_key", "location": "query", "key_name": "api_key", "key_value": "WRONG"}}
    )
    arr_id, _, result = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_485_arricchisci_bad_auth_isolated",
        canvas=_arr_api_canvas(upstream_id, contract, api_id, api_node=_api_node(api_id, output_mapping=mapping)),
    )
    assert_equal(result.get("rows_count"), 3, "Bad external auth must not drop upstream rows in continue-on-error mode")
    rows = _preview_rows(ctx, arr_id)
    assert_true(all(row.get("api_email_bad_auth") in (None, "") for row in rows), "Bad external auth must leave mapped API output empty")


CASES: dict[str, CaseFn] = {
    "test_474": _ctx_case(test_474),
    "test_475": _ctx_case(test_475),
    "test_476": _ctx_case(test_476),
    "test_477": _ctx_case(test_477),
    "test_478": _ctx_case(test_478),
    "test_479": _ctx_case(test_479),
    "test_480": _ctx_case(test_480),
    "test_481": _ctx_case(test_481),
    "test_482": _ctx_case(test_482),
    "test_483": _ctx_case(test_483),
    "test_484": _ctx_case(test_484),
    "test_485": _ctx_case(test_485),
}


def run_case(case_id: str) -> None:
    case = CASES.get(case_id)
    assert_true(case is not None, f"Unknown arricchisci API case: {case_id}")
    case()


__all__ = ["run_case"]
