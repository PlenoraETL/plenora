"""ARRICCHISCI REST matrix coverage.

Cases 486-585 cover the remaining ARRICCHISCI REST perimeter:
- generator response shapes;
- auth schemes;
- pagination modes;
- error handling;
- GeoJSON response handling;
- contract exchange chains involving generator/enricher nodes.
"""

from __future__ import annotations

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
    transform_node,
    unique_name,
)
from _arricchisci_api_final import (
    API_KEY,
    FAKE_REST_BASE_URL,
    _api_node,
    _api_payload,
    _arr_api_canvas,
    _cell,
    _create_api,
    _create_upstream,
    _fake_url,
    _output_mapping,
    _preview_rows,
)
from _arricchisci_contract_final import _create_executed_arricchisci
from _estrai_manipola_final import _columns_by_physical


CaseFn = Callable[[], None]


def _ctx_case(fn: Callable[[E2EContext], None]) -> CaseFn:
    return lambda: run_test(fn)


def _generator_payload(
    *,
    name: str,
    path: str,
    output_mapping: list[dict[str, str]],
    auth: dict[str, Any] | None = None,
    static_parameters: dict[str, Any] | None = None,
    pagination: dict[str, Any] | None = None,
    response_config: dict[str, Any] | None = None,
    response_preset: str | None = None,
) -> dict[str, Any]:
    payload = _api_payload(
        name=name,
        path=path,
        output_mapping=output_mapping,
        static_parameters=static_parameters,
    )
    payload["api_type"] = "generator"
    if auth is not None:
        payload["auth"] = auth
    if pagination is not None:
        payload["pagination"] = pagination
    if response_config is not None:
        payload["response_config"] = response_config
    if response_preset:
        payload["response_preset"] = response_preset
    return payload


def _create_generator_api(
    ctx: E2EContext,
    *,
    name: str,
    path: str,
    output_mapping: list[dict[str, str]],
    auth: dict[str, Any] | None = None,
    static_parameters: dict[str, Any] | None = None,
    pagination: dict[str, Any] | None = None,
    response_config: dict[str, Any] | None = None,
    response_preset: str | None = None,
) -> str:
    ensure_plenora_test_network_access()
    return ctx.create_api_connection(
        _generator_payload(
            name=name,
            path=path,
            output_mapping=output_mapping,
            auth=auth,
            static_parameters=static_parameters,
            pagination=pagination,
            response_config=response_config,
            response_preset=response_preset,
        )
    )


def _generator_canvas(
    api_id: str,
    output_mapping: list[dict[str, str]],
    *,
    file_name: str,
) -> dict[str, Any]:
    api = _api_node(
        api_id,
        node_id="api-generator",
        param_mapping={},
        output_mapping=output_mapping,
    )
    return {
        "nodes": [
            api,
            result_node(format="csv", fileName=file_name),
        ],
        "edges": [edge("e-api-result", "api-generator", "result-1")],
    }


def _execute_generator_case(
    ctx: E2EContext,
    case_id: str,
    spec: dict[str, Any],
) -> tuple[str, dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    mapping = spec["mapping"]
    api_id = _create_generator_api(
        ctx,
        name=f"e2e_v2_{case_id}_generator",
        path=spec["path"],
        output_mapping=mapping,
        auth=spec.get("auth"),
        static_parameters=spec.get("static"),
        pagination=spec.get("pagination"),
        response_config=spec.get("response_config"),
        response_preset=spec.get("response_preset"),
    )
    project_id, contract, result = _create_executed_arricchisci(
        ctx,
        name=unique_name(f"e2e_v2_{case_id}_arr_generator"),
        canvas=_generator_canvas(api_id, mapping, file_name=f"e2e_{case_id}_generator"),
    )
    rows = _preview_rows(ctx, project_id)
    return project_id, contract, rows, result


def _assert_generator_spec(ctx: E2EContext, case_id: str, spec: dict[str, Any]) -> None:
    project_id, contract, rows, result = _execute_generator_case(ctx, case_id, spec)
    expected_rows = spec.get("rows")
    if expected_rows is not None:
        assert_equal(result.get("rows_count"), expected_rows, f"{case_id}: generator row count")
        assert_equal(len(rows), min(expected_rows, 10), f"{case_id}: preview row count")
    expected_columns = spec.get("columns") or [m["column"] for m in spec["mapping"]]
    by_physical = _columns_by_physical(contract)
    assert_true(set(expected_columns) <= set(by_physical), f"{case_id}: output_contract columns")
    for row_index, column, expected in spec.get("checks", []):
        assert_equal(_cell(rows[row_index], column), str(expected), f"{case_id}: {column} value")
    for row_index, column in spec.get("null_checks", []):
        assert_true(rows[row_index].get(column) in (None, ""), f"{case_id}: {column} must be null/empty")
    if spec.get("contract_downstream_formula"):
        formula_column, output_column, expected = spec["contract_downstream_formula"]
        formula_id = by_physical[formula_column]["id"]
        formula = transform_node(
            node_id="formula-1",
            transform_op="formula",
            config={
                "formula": f"{formula_column} + 100",
                "new_column": output_column,
                "output_type": "int",
                "formula_column_refs": [{"id": formula_id, "name": formula_column}],
            },
        )
        manipola_id = ctx.create_project(
            name=unique_name(f"e2e_v2_{case_id}_manipola_downstream"),
            project_type="manipola",
            canvas={
                "nodes": [
                    project_source_node_from_contract(
                        node_id="source-1",
                        source_project_id=project_id,
                        contract=contract,
                    ),
                    formula,
                    result_node(format="csv", fileName=f"e2e_{case_id}_downstream"),
                ],
                "edges": [
                    edge("e-source-formula", "source-1", "formula-1"),
                    edge("e-formula-result", "formula-1", "result-1"),
                ],
            },
        )
        ctx.execute_project(manipola_id)
        downstream_rows = _preview_rows(ctx, manipola_id)
        assert_equal(_cell(downstream_rows[0], output_column), str(expected), f"{case_id}: downstream formula")


def _auth_query() -> dict[str, Any]:
    return {
        "type": "api_key",
        "location": "query",
        "key_name": "api_key",
        "key_value": API_KEY,
    }


def _auth_header() -> dict[str, Any]:
    return {
        "type": "api_key",
        "location": "header",
        "key_name": "X-API-Key",
        "key_value": API_KEY,
    }


def _auth_none() -> dict[str, Any]:
    return {"type": "none"}


def _auth_bearer(token: str = "PLENORA_TEST_BEARER") -> dict[str, Any]:
    return {"type": "bearer", "token": token}


def _auth_basic(username: str = "tester", password: str = "tester_pass") -> dict[str, Any]:
    return {"type": "basic", "username": username, "password": password}


def _auth_oauth_cc(client_secret: str = "plenora_secret") -> dict[str, Any]:
    return {
        "type": "oauth2_client_credentials",
        "token_url": _fake_url("/auth/oauth2/token"),
        "client_id": "plenora",
        "client_secret": client_secret,
    }


def _auth_oauth_password(password: str = "tester_pass") -> dict[str, Any]:
    return {
        "type": "oauth2_password",
        "token_url": _fake_url("/auth/oauth2/token"),
        "client_id": "plenora",
        "client_secret": "plenora_secret",
        "username": "tester",
        "password": password,
    }


def _auth_arcgis(password: str = "tester_pass") -> dict[str, Any]:
    return {
        "type": "arcgis_token",
        "token_url": _fake_url("/auth/arcgis/generateToken"),
        "username": "tester",
        "password": password,
        "client": "referer",
        "referer": "https://plenora.test",
    }


def _connection_test_payload(
    *,
    name: str,
    path: str,
    auth: dict[str, Any],
    method: str = "GET",
    static_parameters: dict[str, Any] | None = None,
    output_mapping: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "name": unique_name(name),
        "description": "E2E fake REST API connection matrix",
        "api_type": "enricher",
        "category": "e2e",
        "connection_type": "manual",
        "url": _fake_url(path),
        "method": method,
        "auth": auth,
        "parameters": [],
        "static_parameters": static_parameters or {},
        "output_mapping": output_mapping or _output_mapping(("ok", "ok")),
        "custom_headers": {},
        "options": {"rate_limit": 0},
    }


def _assert_connection_test_spec(ctx: E2EContext, case_id: str, spec: dict[str, Any]) -> None:
    ensure_plenora_test_network_access()
    api_id = ctx.create_api_connection(
        _connection_test_payload(
            name=f"e2e_v2_{case_id}_api_test",
            path=spec["path"],
            auth=spec.get("auth", _auth_none()),
            method=spec.get("method", "GET"),
            static_parameters=spec.get("static"),
            output_mapping=spec.get("mapping"),
        )
    )
    result = ctx.api_json("POST", f"/api-connections/{api_id}/test")
    expected_success = spec.get("success", True)
    assert_equal(bool(result.get("success")), expected_success, f"{case_id}: API test success flag")
    if "status_code" in spec:
        assert_equal(result.get("status_code"), spec["status_code"], f"{case_id}: API test status")
    if spec.get("contains"):
        assert_true(spec["contains"] in str(result), f"{case_id}: API test response contains marker")


DATA_MAPPING = _output_mapping(("id", "id"), ("email", "email"), ("status", "status"))
DATA_ALT_MAPPING = _output_mapping(("id", "id"), ("city_id", "city_id"), ("age", "age"))
NESTED_MAPPING = _output_mapping(
    ("response.items.records[*].id", "id"),
    ("response.items.records[*].email", "email"),
    ("response.items.records[*].status", "status"),
)
NULLS_MAPPING = _output_mapping(("email", "email"), ("age", "age"), ("status", "status"), ("city_id", "city_id"))
GEO_MAPPING = _output_mapping(
    ("features[*].properties.id", "id"),
    ("features[*].properties.name", "name"),
    ("features[*].geometry.type", "geometry_type"),
)


GENERATOR_SPECS: dict[str, dict[str, Any]] = {
    "test_486": {"path": "/data/object_root", "mapping": DATA_MAPPING, "rows": 12, "checks": [(0, "email", "anna.rossi@example.com")]},
    "test_487": {"path": "/data/object_root", "mapping": DATA_ALT_MAPPING, "rows": 12, "checks": [(1, "city_id", 1)]},
    "test_488": {"path": "/data/array_root", "mapping": DATA_MAPPING, "rows": 12, "checks": [(2, "status", "pending")]},
    "test_489": {"path": "/data/array_root", "mapping": DATA_ALT_MAPPING, "rows": 12, "checks": [(3, "age", 52)]},
    "test_490": {"path": "/data/nested_deep", "mapping": NESTED_MAPPING, "rows": 12, "checks": [(0, "id", 1)]},
    "test_491": {"path": "/data/nested_deep", "mapping": _output_mapping(("response.items.records[*].full_name", "full_name"), ("response.items.records[*].city_id", "city_id")), "rows": 12, "checks": [(1, "full_name", "Marco Bianchi")]},
    "test_492": {"path": "/data/with_nulls", "mapping": NULLS_MAPPING, "rows": 5, "null_checks": [(0, "email")]},
    "test_493": {"path": "/data/with_nulls", "mapping": NULLS_MAPPING, "rows": 5, "null_checks": [(1, "age")]},
    "test_494": {"path": "/data/with_nulls", "mapping": NULLS_MAPPING, "rows": 5, "null_checks": [(2, "status")]},
    "test_495": {"path": "/data/single_object", "mapping": DATA_MAPPING, "rows": 1, "checks": [(0, "email", "anna.rossi@example.com")]},
    "test_496": {"path": "/data/single_object", "mapping": DATA_ALT_MAPPING, "rows": 1, "checks": [(0, "age", 34)]},
    "test_497": {"path": "/data/object_root", "mapping": DATA_MAPPING, "auth": _auth_header(), "rows": 12, "checks": [(0, "id", 1)]},
    "test_498": {"path": "/data/array_root", "mapping": DATA_MAPPING, "auth": _auth_header(), "rows": 12, "checks": [(4, "status", "inactive")]},
    "test_499": {"path": "/data/by_id/{item_id}", "mapping": DATA_MAPPING, "static": {"item_id": 1}, "rows": 1, "checks": [(0, "email", "anna.rossi@example.com")]},
    "test_500": {"path": "/data/by_id/{item_id}", "mapping": DATA_MAPPING, "static": {"item_id": 2}, "rows": 1, "checks": [(0, "email", "marco.bianchi@example.com")]},
    "test_501": {"path": "/data/by_id/{item_id}", "mapping": DATA_ALT_MAPPING, "static": {"item_id": 4}, "rows": 1, "checks": [(0, "city_id", 3)]},
    "test_502": {"path": "/data/object_root", "mapping": _output_mapping(("id", "id"), ("full_name", "full_name")), "rows": 12, "contract_downstream_formula": ("id", "id_plus_100", 101)},
    "test_503": {"path": "/data/array_root", "mapping": _output_mapping(("id", "id"), ("age", "age")), "rows": 12, "contract_downstream_formula": ("age", "age_plus_100", 134)},
    "test_504": {"path": "/data/single_object", "mapping": _output_mapping(("id", "id"), ("city_id", "city_id")), "rows": 1, "contract_downstream_formula": ("city_id", "city_plus_100", 101)},
    "test_505": {"path": "/data/nested_deep", "mapping": _output_mapping(("response.items.records[*].id", "id"), ("response.items.records[*].age", "age")), "rows": 12, "contract_downstream_formula": ("id", "id_plus_100", 101)},
}


AUTH_SPECS: dict[str, dict[str, Any]] = {
    "test_506": {"path": "/auth/api_key_query", "auth": _auth_query(), "success": True, "status_code": 200},
    "test_507": {"path": "/auth/api_key_header", "auth": _auth_header(), "success": True, "status_code": 200},
    "test_508": {"path": "/auth/bearer", "auth": _auth_bearer(), "success": True, "status_code": 200},
    "test_509": {"path": "/auth/basic", "auth": _auth_basic(), "success": True, "status_code": 200},
    "test_510": {"path": "/auth/oauth2/protected", "auth": _auth_oauth_cc(), "success": True, "status_code": 200},
    "test_511": {"path": "/auth/oauth2/protected", "auth": _auth_oauth_password(), "success": True, "status_code": 200},
    "test_512": {"path": "/sharing/rest/portals/self", "auth": _auth_arcgis(), "success": True, "status_code": 200},
    "test_513": {"path": "/health", "auth": _auth_none(), "success": True, "status_code": 200},
    "test_514": {"path": "/error/redirect_loop", "auth": _auth_none(), "success": True, "status_code": 200},
    "test_515": {"path": "/error/wrong_content_type", "auth": _auth_none(), "success": True, "status_code": 200},
    "test_516": {"path": "/auth/api_key_query", "auth": {"type": "api_key", "location": "query", "key_name": "api_key", "key_value": "WRONG"}, "success": False, "status_code": 401},
    "test_517": {"path": "/auth/api_key_header", "auth": {"type": "api_key", "location": "header", "key_name": "X-API-Key", "key_value": "WRONG"}, "success": False, "status_code": 401},
    "test_518": {"path": "/auth/bearer", "auth": _auth_bearer("WRONG"), "success": False, "status_code": 401},
    "test_519": {"path": "/auth/basic", "auth": _auth_basic(password="wrong"), "success": False, "status_code": 401},
    "test_520": {"path": "/auth/oauth2/protected", "auth": _auth_oauth_cc(client_secret="wrong"), "success": False, "status_code": 401},
    "test_521": {"path": "/auth/oauth2/protected", "auth": _auth_oauth_password(password="wrong"), "success": False, "status_code": 401},
    "test_522": {"path": "/sharing/rest/portals/self", "auth": _auth_arcgis(password="wrong"), "success": False, "contains": "ArcGIS token"},
    "test_523": {"path": "/data/object_root", "auth": _auth_none(), "success": False, "status_code": 401},
    "test_524": {"path": "/auth/basic", "auth": _auth_none(), "success": False, "status_code": 401},
    "test_525": {"path": "/auth/bearer", "auth": _auth_none(), "success": False, "status_code": 401},
}


PAGINATION_SPECS: dict[str, dict[str, Any]] = {
    "test_526": {"path": "/page/offset", "mapping": DATA_MAPPING, "auth": _auth_none(), "pagination": {"type": "offset", "page_size": 5, "max_rows": 12}, "rows": 12, "checks": [(0, "id", 1)]},
    "test_527": {"path": "/page/offset", "mapping": DATA_MAPPING, "auth": _auth_none(), "pagination": {"type": "offset", "page_size": 4, "max_rows": 8}, "rows": 8, "checks": [(7, "id", 8)]},
    "test_528": {"path": "/page/offset", "mapping": DATA_ALT_MAPPING, "auth": _auth_none(), "pagination": {"type": "offset", "page_size": 3, "max_rows": 9}, "rows": 9, "checks": [(8, "city_id", 7)]},
    "test_529": {"path": "/page/offset", "mapping": DATA_MAPPING, "auth": _auth_none(), "pagination": {"type": "offset", "page_size": 20, "max_rows": 20}, "rows": 12, "checks": [(9, "id", 10)]},
    "test_530": {"path": "/page/offset", "mapping": DATA_MAPPING, "auth": _auth_none(), "pagination": {"type": "offset", "page_size": 2, "max_rows": 6}, "rows": 6, "checks": [(5, "id", 6)]},
    "test_531": {"path": "/page/cursor", "mapping": _output_mapping(("items[*].id", "id"), ("items[*].email", "email")), "auth": _auth_none(), "static": {"page_size": 5}, "pagination": {"type": "cursor", "page_size": 5, "max_rows": 12}, "rows": 12, "checks": [(0, "id", 1)]},
    "test_532": {"path": "/page/cursor", "mapping": _output_mapping(("items[*].id", "id"), ("items[*].city_id", "city_id")), "auth": _auth_none(), "static": {"page_size": 4}, "pagination": {"type": "cursor", "page_size": 4, "max_rows": 8}, "rows": 8, "checks": [(7, "id", 8)]},
    "test_533": {"path": "/page/cursor", "mapping": _output_mapping(("items[*].id", "id"), ("items[*].status", "status")), "auth": _auth_none(), "static": {"page_size": 3}, "pagination": {"type": "cursor", "page_size": 3, "max_rows": 9}, "rows": 9, "checks": [(2, "status", "pending")]},
    "test_534": {"path": "/page/cursor", "mapping": _output_mapping(("items[*].id", "id"), ("items[*].age", "age")), "auth": _auth_none(), "static": {"page_size": 6}, "pagination": {"type": "cursor", "page_size": 6, "max_rows": 6}, "rows": 6, "checks": [(5, "id", 6)]},
    "test_535": {"path": "/page/cursor", "mapping": _output_mapping(("items[*].id", "id"), ("items[*].full_name", "full_name")), "auth": _auth_none(), "static": {"page_size": 20}, "pagination": {"type": "cursor", "page_size": 20, "max_rows": 20}, "rows": 12, "checks": [(1, "full_name", "Marco Bianchi")]},
    "test_536": {"path": "/page/link_header", "mapping": DATA_MAPPING, "auth": _auth_none(), "static": {"size": 5}, "pagination": {"type": "link", "page_size": 5, "max_rows": 12}, "rows": 12, "checks": [(0, "id", 1)]},
    "test_537": {"path": "/page/link_header", "mapping": DATA_MAPPING, "auth": _auth_none(), "static": {"size": 4}, "pagination": {"type": "link", "page_size": 4, "max_rows": 8}, "rows": 8, "checks": [(7, "id", 8)]},
    "test_538": {"path": "/page/link_header", "mapping": DATA_ALT_MAPPING, "auth": _auth_none(), "static": {"size": 6}, "pagination": {"type": "link", "page_size": 6, "max_rows": 12}, "rows": 12, "checks": [(3, "age", 52)]},
    "test_539": {"path": "/page/link_header", "mapping": DATA_MAPPING, "auth": _auth_none(), "static": {"size": 3}, "pagination": {"type": "link", "page_size": 3, "max_rows": 9}, "rows": 9, "checks": [(8, "id", 9)]},
    "test_540": {"path": "/page/link_header", "mapping": DATA_MAPPING, "auth": _auth_none(), "static": {"size": 20}, "pagination": {"type": "link", "page_size": 20, "max_rows": 20}, "rows": 12, "checks": [(9, "id", 10)]},
    "test_541": {"path": "/page/page_size", "mapping": _output_mapping(("items[*].id", "id"), ("items[*].email", "email")), "auth": _auth_none(), "static": {"page": 1, "size": 5}, "rows": 5, "checks": [(4, "id", 5)]},
    "test_542": {"path": "/page/page_size", "mapping": _output_mapping(("items[*].id", "id"), ("items[*].status", "status")), "auth": _auth_none(), "static": {"page": 2, "size": 5}, "rows": 5, "checks": [(0, "id", 6)]},
    "test_543": {"path": "/page/no_meta", "mapping": DATA_MAPPING, "auth": _auth_none(), "static": {"page": 1, "size": 5}, "rows": 5, "checks": [(4, "id", 5)]},
    "test_544": {"path": "/page/no_meta", "mapping": DATA_MAPPING, "auth": _auth_none(), "static": {"page": 3, "size": 5}, "rows": 2, "checks": [(1, "id", 12)]},
    "test_545": {"path": "/page/offset", "mapping": DATA_MAPPING, "auth": _auth_none(), "pagination": {"type": "unknown", "page_size": 5, "max_rows": 5}, "rows": 5, "checks": [(0, "id", 1)]},
}


ERROR_SPECS: dict[str, dict[str, Any]] = {
    "test_546": {"path": "/error/401", "success": False, "status_code": 401},
    "test_547": {"path": "/error/403", "success": False, "status_code": 403},
    "test_548": {"path": "/error/404", "success": False, "status_code": 404},
    "test_549": {"path": "/error/422", "success": False, "status_code": 422},
    "test_550": {"path": "/error/429", "success": False, "status_code": 429},
    "test_551": {"path": "/error/500", "success": False, "status_code": 500},
    "test_552": {"path": "/error/503", "success": False, "status_code": 503},
    "test_553": {"path": "/error/malformed_json", "success": False, "status_code": 200, "contains": "JSON"},
    "test_554": {"path": "/error/empty_body", "success": False, "status_code": 200, "contains": "JSON"},
    "test_555": {"path": "/error/wrong_content_type", "success": True, "status_code": 200},
    "test_556": {"path": "/error/redirect_loop", "success": True, "status_code": 200},
    "test_557": {"path": "/page/cursor", "static": {"cursor": "not-base64"}, "success": False, "status_code": 400},
    "test_558": {"path": "/data/by_id/{item_id}", "auth": _auth_query(), "static": {"item_id": 99}, "success": False, "status_code": 404},
    "test_559": {"path": "/data/by_id/{item_id}", "auth": _auth_query(), "success": False, "status_code": 422},
    "test_560": {"path": "/data/object_root", "auth": {"type": "api_key", "location": "query", "key_name": "api_key", "key_value": "WRONG"}, "success": False, "status_code": 401},
    "test_561": {"path": "/data/object_root", "auth": {"type": "api_key", "location": "header", "key_name": "X-API-Key", "key_value": "WRONG"}, "success": False, "status_code": 401},
    "test_562": {"path": "/geo/cities/by_region/99", "success": False, "status_code": 404},
    "test_563": {"path": "/auth/oauth2/protected", "auth": _auth_oauth_cc(client_secret="wrong"), "success": False, "status_code": 401},
    "test_564": {"path": "/sharing/rest/portals/self", "auth": _auth_arcgis(password="wrong"), "success": False, "contains": "ArcGIS token"},
    "test_565": {"path": "/error/422", "success": False, "status_code": 422, "contains": "city_id"},
}


GEO_SPECS: dict[str, dict[str, Any]] = {
    "test_566": {"path": "/geo/cities", "mapping": GEO_MAPPING, "auth": _auth_none(), "rows": 9, "checks": [(0, "name", "Milano")]},
    "test_567": {"path": "/geo/cities", "mapping": _output_mapping(("features[*].properties.id", "id"), ("features[*].properties.population", "population")), "auth": _auth_none(), "rows": 9, "checks": [(0, "population", 1396000)]},
    "test_568": {"path": "/geo/cities/by_region/{region_id}", "mapping": GEO_MAPPING, "auth": _auth_none(), "static": {"region_id": 1}, "rows": 3, "checks": [(1, "name", "Bergamo")]},
    "test_569": {"path": "/geo/cities/by_region/{region_id}", "mapping": GEO_MAPPING, "auth": _auth_none(), "static": {"region_id": 2}, "rows": 3, "checks": [(0, "name", "Roma")]},
    "test_570": {"path": "/geo/cities/by_region/{region_id}", "mapping": GEO_MAPPING, "auth": _auth_none(), "static": {"region_id": 3}, "rows": 3, "checks": [(0, "name", "Napoli")]},
    "test_571": {"path": "/geo/null_geometry", "mapping": GEO_MAPPING, "auth": _auth_none(), "rows": 1, "checks": [(0, "name", "Ghost")], "null_checks": [(0, "geometry_type")]},
    "test_572": {"path": "/geo/mixed", "mapping": GEO_MAPPING, "auth": _auth_none(), "rows": 3, "checks": [(1, "geometry_type", "LineString")]},
    "test_573": {"path": "/geo/invalid", "mapping": GEO_MAPPING, "auth": _auth_none(), "rows": 1, "checks": [(0, "name", "MissingGeometry")], "null_checks": [(0, "geometry_type")]},
    "test_574": {"path": "/geo/cities", "mapping": _output_mapping(("features[*].properties.id", "id"), ("features[*].geometry.coordinates", "coordinates")), "auth": _auth_none(), "rows": 9, "checks": [(0, "id", 1)]},
    "test_575": {"path": "/geo/cities/by_region/{region_id}", "mapping": _output_mapping(("features[*].properties.id", "id"), ("features[*].properties.founded_year", "founded_year")), "auth": _auth_none(), "static": {"region_id": 2}, "rows": 3, "null_checks": [(2, "founded_year")]},
}


def _assert_contract_chain(ctx: E2EContext, case_id: str) -> None:
    if case_id in {"test_576", "test_577", "test_578"}:
        specs = {
            "test_576": GENERATOR_SPECS["test_486"] | {"contract_downstream_formula": ("id", "id_plus_100", 101)},
            "test_577": GENERATOR_SPECS["test_487"] | {"contract_downstream_formula": ("age", "age_plus_100", 134)},
            "test_578": GEO_SPECS["test_566"] | {"contract_downstream_formula": ("id", "id_plus_100", 101)},
        }
        _assert_generator_spec(ctx, case_id, specs[case_id])
        return

    if case_id in {"test_579", "test_580", "test_581"}:
        gen_specs = {
            "test_579": GENERATOR_SPECS["test_486"],
            "test_580": GENERATOR_SPECS["test_488"],
            "test_581": PAGINATION_SPECS["test_526"],
        }
        generator_id, generator_contract, _, _ = _execute_generator_case(ctx, case_id, gen_specs[case_id])
        api_id = _create_api(ctx, name=f"e2e_v2_{case_id}_by_id")
        arr_id, arr_contract, _ = _create_executed_arricchisci(
            ctx,
            name=unique_name(f"e2e_v2_{case_id}_arr_from_generator"),
            canvas=_arr_api_canvas(
                generator_id,
                generator_contract,
                api_id,
                api_node=_api_node(api_id, param_mapping={"item_id": "id"}),
            ),
        )
        assert_true("api_email" in _columns_by_physical(arr_contract), f"{case_id}: generator -> API contract")
        rows = _preview_rows(ctx, arr_id)
        assert_equal(rows[0]["api_email"], "anna.rossi@example.com", f"{case_id}: generator -> API enrichment")
        return

    if case_id in {"test_582", "test_583"}:
        _, upstream_id, contract = _create_upstream(ctx, f"{case_id}_em_to_arr")
        first_formula = transform_node(
            node_id="formula-1",
            transform_op="formula",
            config={
                "formula": "customer_id",
                "new_column": "id_copy",
                "output_type": "int",
                "formula_column_refs": [{"id": _columns_by_physical(contract)["customer_id"]["id"], "name": "customer_id"}],
            },
        )
        manipola_id = ctx.create_project(
            name=unique_name(f"e2e_v2_{case_id}_manipola_producer"),
            project_type="manipola",
            canvas={
                "nodes": [
                    project_source_node_from_contract(node_id="source-1", source_project_id=upstream_id, contract=contract),
                    first_formula,
                    result_node(format="csv", fileName=f"e2e_{case_id}_manipola_producer"),
                ],
                "edges": [
                    edge("e-source-formula", "source-1", "formula-1"),
                    edge("e-formula-result", "formula-1", "result-1"),
                ],
            },
        )
        ctx.execute_project(manipola_id)
        manipola_contract = ctx.get_project(manipola_id).get("output_contract") or {}
        api_id = _create_api(ctx, name=f"e2e_v2_{case_id}_by_id")
        arr_id, arr_contract, _ = _create_executed_arricchisci(
            ctx,
            name=unique_name(f"e2e_v2_{case_id}_arr_from_manipola"),
            canvas=_arr_api_canvas(
                manipola_id,
                manipola_contract,
                api_id,
                api_node=_api_node(api_id, param_mapping={"item_id": "id_copy"}),
            ),
        )
        assert_true("api_email" in _columns_by_physical(arr_contract), f"{case_id}: manipola -> arricchisci API")
        rows = _preview_rows(ctx, arr_id)
        assert_equal(rows[0]["api_email"], "anna.rossi@example.com", f"{case_id}: manipola output feeds API")
        return

    if case_id in {"test_584", "test_585"}:
        generator_id, generator_contract, _, _ = _execute_generator_case(ctx, case_id, GENERATOR_SPECS["test_486"])
        api_id = _create_api(ctx, name=f"e2e_v2_{case_id}_by_id")
        arr_id, arr_contract, _ = _create_executed_arricchisci(
            ctx,
            name=unique_name(f"e2e_v2_{case_id}_arr_first"),
            canvas=_arr_api_canvas(
                generator_id,
                generator_contract,
                api_id,
                api_node=_api_node(api_id, param_mapping={"item_id": "id"}),
            ),
        )
        second_api_id = _create_api(
            ctx,
            name=f"e2e_v2_{case_id}_second",
            output_mapping=_output_mapping(("status", "lookup_status")),
        )
        second_id, second_contract, _ = _create_executed_arricchisci(
            ctx,
            name=unique_name(f"e2e_v2_{case_id}_arr_second"),
            canvas=_arr_api_canvas(
                arr_id,
                arr_contract,
                second_api_id,
                api_node=_api_node(second_api_id, param_mapping={"item_id": "id"}, output_mapping=_output_mapping(("status", "lookup_status"))),
            ),
        )
        assert_true("lookup_status" in _columns_by_physical(second_contract), f"{case_id}: arricchisci -> arricchisci contract")
        rows = _preview_rows(ctx, second_id)
        assert_equal(rows[0]["lookup_status"], "active", f"{case_id}: second ARRICCHISCI API output")
        return

    raise AssertionError(f"Unknown contract chain case: {case_id}")


def _case_runner(case_id: str) -> Callable[[E2EContext], None]:
    def _run(ctx: E2EContext) -> None:
        if case_id in GENERATOR_SPECS:
            _assert_generator_spec(ctx, case_id, GENERATOR_SPECS[case_id])
            return
        if case_id in AUTH_SPECS:
            _assert_connection_test_spec(ctx, case_id, AUTH_SPECS[case_id])
            return
        if case_id in PAGINATION_SPECS:
            _assert_generator_spec(ctx, case_id, PAGINATION_SPECS[case_id])
            return
        if case_id in ERROR_SPECS:
            _assert_connection_test_spec(ctx, case_id, ERROR_SPECS[case_id])
            return
        if case_id in GEO_SPECS:
            _assert_generator_spec(ctx, case_id, GEO_SPECS[case_id])
            return
        _assert_contract_chain(ctx, case_id)
    return _run


CASES: dict[str, CaseFn] = {
    f"test_{i}": _ctx_case(_case_runner(f"test_{i}"))
    for i in range(486, 586)
}


def run_case(case_id: str) -> None:
    case = CASES.get(case_id)
    assert_true(case is not None, f"Unknown arricchisci REST matrix case: {case_id}")
    case()


__all__ = ["run_case"]
