"""ARRICCHISCI stateful/cache/contract stress coverage.

Cases 586-685 intentionally exercise scenarios that only fail after previous
successful calls, API connection updates, cached tokens, or chained contract
mutations.
"""

from __future__ import annotations

from typing import Any, Callable

from _e2e import (
    E2EContext,
    assert_equal,
    assert_true,
    edge,
    project_source_node_from_contract,
    result_node,
    run_test,
    transform_node,
    unique_name,
)
from _arricchisci_api_final import (
    _api_node,
    _arr_api_canvas,
    _cell,
    _create_api,
    _create_upstream,
    _output_mapping,
    _preview_rows,
)
from _arricchisci_contract_final import _create_executed_arricchisci
from _arricchisci_rest_matrix import (
    DATA_MAPPING,
    GEO_MAPPING,
    PAGINATION_SPECS,
    _assert_generator_spec,
    _auth_arcgis,
    _auth_basic,
    _auth_bearer,
    _auth_header,
    _auth_none,
    _auth_oauth_cc,
    _auth_oauth_password,
    _auth_query,
    _connection_test_payload,
    _create_generator_api,
    _execute_generator_case,
    _generator_canvas,
)
from _estrai_manipola_final import _columns_by_physical, _execute_project_raw


CaseFn = Callable[[], None]


def _ctx_case(fn: Callable[[E2EContext], None]) -> CaseFn:
    return lambda: run_test(fn)


def _api_test_sequence(ctx: E2EContext, case_id: str, spec: dict[str, Any]) -> None:
    api_id = ctx.create_api_connection(
        _connection_test_payload(
            name=f"e2e_v2_{case_id}_stateful_api",
            path=spec["path"],
            auth=spec["steps"][0]["auth"],
            static_parameters=spec.get("static"),
        )
    )
    for index, step in enumerate(spec["steps"]):
        if index:
            payload: dict[str, Any] = {"auth": step["auth"]}
            if "static" in step:
                payload["static_parameters"] = step["static"]
            ctx.update_api_connection(api_id, payload)
        result = ctx.api_json("POST", f"/api-connections/{api_id}/test")
        assert_equal(
            bool(result.get("success")),
            step["success"],
            f"{case_id}: step {index} success flag",
        )
        if "contains" in step:
            assert_true(step["contains"] in str(result), f"{case_id}: step {index} marker")


def _api_execute_auth_sequence(ctx: E2EContext, case_id: str, spec: dict[str, Any]) -> None:
    _, upstream_id, contract = _create_upstream(ctx, f"{case_id}_auth_exec")
    api_id = _create_api(
        ctx,
        name=f"e2e_v2_{case_id}_by_id",
        auth_location="query",
        output_mapping=_output_mapping(("email", "api_email")),
    )
    if spec["steps"][0].get("auth"):
        ctx.update_api_connection(api_id, {"auth": spec["steps"][0]["auth"]})
    arr_id = ctx.create_project(
        name=unique_name(f"e2e_v2_{case_id}_arr_auth_sequence"),
        project_type="arricchisci",
        canvas=_arr_api_canvas(
            upstream_id,
            contract,
            api_id,
            api_node=_api_node(api_id, output_mapping=_output_mapping(("email", "api_email"))),
        ),
    )
    for index, step in enumerate(spec["steps"]):
        if index:
            ctx.update_api_connection(api_id, {"auth": step["auth"]})
        result = _execute_project_raw(ctx, arr_id, force_recalculate=True)
        assert_true(result.get("success"), f"{case_id}: execute step {index} must complete")
        rows = _preview_rows(ctx, arr_id)
        if step["filled"]:
            assert_equal(rows[0]["api_email"], "anna.rossi@example.com", f"{case_id}: step {index} enriched")
        else:
            assert_true(rows[0].get("api_email") in (None, ""), f"{case_id}: step {index} empty API output")


def _generator_runtime(ctx: E2EContext, case_id: str, spec: dict[str, Any]) -> None:
    api_id = _create_generator_api(
        ctx,
        name=f"e2e_v2_{case_id}_runtime",
        path=spec["path"],
        output_mapping=spec.get("mapping", DATA_MAPPING),
        auth=spec.get("auth", _auth_none()),
        static_parameters=spec.get("static"),
        pagination=spec.get("pagination"),
    )
    project_id = ctx.create_project(
        name=unique_name(f"e2e_v2_{case_id}_runtime_generator"),
        project_type="arricchisci",
        canvas=_generator_canvas(
            api_id,
            spec.get("mapping", DATA_MAPPING),
            file_name=f"e2e_{case_id}_runtime",
        ),
    )
    result = _execute_project_raw(ctx, project_id, force_recalculate=True)
    assert_equal(bool(result.get("success")), spec["success"], f"{case_id}: runtime success")
    if spec["success"]:
        if "rows" in spec:
            assert_equal(result.get("rows_count"), spec["rows"], f"{case_id}: runtime row count")
        if spec.get("rows") == 0 and not spec.get("checks"):
            preview = ctx.preview_project(project_id, limit=10)
            assert_equal(preview.get("rows") or [], [], f"{case_id}: empty preview rows")
            assert_true(preview.get("columns"), f"{case_id}: empty preview keeps schema")
            return
        rows = _preview_rows(ctx, project_id)
        for row_index, column, expected in spec.get("checks", []):
            assert_equal(_cell(rows[row_index], column), str(expected), f"{case_id}: runtime value")
    elif spec.get("contains"):
        assert_true(spec["contains"] in str(result), f"{case_id}: runtime error marker")


def _pagination_mutation(ctx: E2EContext, case_id: str, spec: dict[str, Any]) -> None:
    first = spec["first"]
    api_id = _create_generator_api(
        ctx,
        name=f"e2e_v2_{case_id}_pagination_mutation",
        path=first["path"],
        output_mapping=first["mapping"],
        auth=first.get("auth", _auth_none()),
        static_parameters=first.get("static"),
        pagination=first.get("pagination"),
    )
    project_id, contract1, result1 = _create_executed_arricchisci(
        ctx,
        name=unique_name(f"e2e_v2_{case_id}_pagination_project"),
        canvas=_generator_canvas(api_id, first["mapping"], file_name=f"e2e_{case_id}_pagination"),
    )
    assert_equal(result1.get("rows_count"), spec["first_rows"], f"{case_id}: first rows")
    id_before = _columns_by_physical(contract1)[spec["stable_column"]]["id"]

    second = spec["second"]
    ctx.update_api_connection(
        api_id,
        {
            "static_parameters": second.get("static", {}),
            "pagination": second.get("pagination", {}),
            "output_mapping": second["mapping"],
        },
    )
    ctx.execute_project(project_id, force_recalculate=True)
    contract2 = ctx.get_project(project_id).get("output_contract") or {}
    assert_equal(
        _columns_by_physical(contract2)[spec["stable_column"]]["id"],
        id_before,
        f"{case_id}: stable id after pagination mutation",
    )
    rows = _preview_rows(ctx, project_id)
    assert_equal(len(rows), min(spec["second_rows"], 10), f"{case_id}: second preview rows")
    for row_index, column, expected in spec.get("checks", []):
        assert_equal(_cell(rows[row_index], column), str(expected), f"{case_id}: mutated pagination value")


def _contract_mutation(ctx: E2EContext, case_id: str, spec: dict[str, Any]) -> None:
    api_id = _create_generator_api(
        ctx,
        name=f"e2e_v2_{case_id}_contract_mutation",
        path=spec.get("path", "/data/object_root"),
        output_mapping=spec["first_mapping"],
        auth=spec.get("auth", _auth_query()),
        static_parameters=spec.get("static"),
    )
    project_id, contract1, _ = _create_executed_arricchisci(
        ctx,
        name=unique_name(f"e2e_v2_{case_id}_generator_contract"),
        canvas=_generator_canvas(api_id, spec["first_mapping"], file_name=f"e2e_{case_id}_contract"),
    )
    before = _columns_by_physical(contract1)
    tracked_id = before[spec["tracked_before"]]["id"]

    ctx.update_api_connection(api_id, {"output_mapping": spec["second_mapping"]})
    ctx.execute_project(project_id, force_recalculate=True)
    contract2 = ctx.get_project(project_id).get("output_contract") or {}
    after = _columns_by_physical(contract2)
    if spec.get("tracked_after"):
        assert_equal(after[spec["tracked_after"]]["id"], tracked_id, f"{case_id}: renamed output id")
    for missing in spec.get("missing_after", []):
        assert_true(missing not in after, f"{case_id}: removed output absent")
    for present in spec.get("present_after", []):
        assert_true(present in after, f"{case_id}: added output present")

    if spec.get("downstream_formula"):
        formula_column, output_column, expected = spec["downstream_formula"]
        formula_id = after[formula_column]["id"]
        manipola_id = ctx.create_project(
            name=unique_name(f"e2e_v2_{case_id}_downstream_contract"),
            project_type="manipola",
            canvas={
                "nodes": [
                    project_source_node_from_contract(
                        node_id="source-1",
                        source_project_id=project_id,
                        contract=contract2,
                    ),
                    transform_node(
                        node_id="formula-1",
                        transform_op="formula",
                        config={
                            "formula": f"{formula_column} + 100",
                            "new_column": output_column,
                            "output_type": "int",
                            "formula_column_refs": [{"id": formula_id, "name": formula_column}],
                        },
                    ),
                    result_node(format="csv", fileName=f"e2e_{case_id}_downstream"),
                ],
                "edges": [
                    edge("e-source-formula", "source-1", "formula-1"),
                    edge("e-formula-result", "formula-1", "result-1"),
                ],
            },
        )
        ctx.execute_project(manipola_id)
        rows = _preview_rows(ctx, manipola_id)
        assert_equal(_cell(rows[0], output_column), str(expected), f"{case_id}: downstream after mutation")


def _chain_state(ctx: E2EContext, case_id: str, spec: dict[str, Any]) -> None:
    generator_id, generator_contract, _, _ = _execute_generator_case(ctx, case_id, spec["generator"])
    api_id = _create_api(
        ctx,
        name=f"e2e_v2_{case_id}_chain_api",
        output_mapping=spec.get("api_mapping", _output_mapping(("email", "api_email"))),
    )
    arr_id, arr_contract, _ = _create_executed_arricchisci(
        ctx,
        name=unique_name(f"e2e_v2_{case_id}_chain_arr"),
        canvas=_arr_api_canvas(
            generator_id,
            generator_contract,
            api_id,
            api_node=_api_node(
                api_id,
                param_mapping={"item_id": spec.get("param_column", "id")},
                output_mapping=spec.get("api_mapping", _output_mapping(("email", "api_email"))),
            ),
        ),
    )
    rows = _preview_rows(ctx, arr_id)
    assert_equal(rows[0][spec.get("check_column", "api_email")], spec.get("expected", "anna.rossi@example.com"), f"{case_id}: initial chain")

    if spec.get("mutate_auth"):
        ctx.update_api_connection(api_id, {"auth": spec["mutate_auth"]})
        result = _execute_project_raw(ctx, arr_id, force_recalculate=True)
        assert_true(result.get("success"), f"{case_id}: chain auth mutation executes")
        rows = _preview_rows(ctx, arr_id)
        assert_true(rows[0].get(spec.get("check_column", "api_email")) in (None, ""), f"{case_id}: chain invalid auth empties output")
        return

    if spec.get("downstream"):
        formula_column = spec["downstream"]
        formula_id = _columns_by_physical(arr_contract)[formula_column]["id"]
        manipola_id = ctx.create_project(
            name=unique_name(f"e2e_v2_{case_id}_chain_downstream"),
            project_type="manipola",
            canvas={
                "nodes": [
                    project_source_node_from_contract(node_id="source-1", source_project_id=arr_id, contract=arr_contract),
                    transform_node(
                        node_id="formula-1",
                        transform_op="formula",
                        config={
                            "formula": f"{formula_column} + 1",
                            "new_column": "chain_calc",
                            "output_type": "int",
                            "formula_column_refs": [{"id": formula_id, "name": formula_column}],
                        },
                    ),
                    result_node(format="csv", fileName=f"e2e_{case_id}_chain_downstream"),
                ],
                "edges": [
                    edge("e-source-formula", "source-1", "formula-1"),
                    edge("e-formula-result", "formula-1", "result-1"),
                ],
            },
        )
        ctx.execute_project(manipola_id)
        downstream_rows = _preview_rows(ctx, manipola_id)
        assert_equal(_cell(downstream_rows[0], "chain_calc"), str(spec["downstream_expected"]), f"{case_id}: chain downstream")


STATE_SPECS: dict[str, dict[str, Any]] = {
    "test_586": {"path": "/auth/oauth2/protected", "steps": [{"auth": _auth_oauth_cc(), "success": True}, {"auth": _auth_oauth_cc(client_secret="wrong"), "success": False}]},
    "test_587": {"path": "/auth/oauth2/protected", "steps": [{"auth": _auth_oauth_cc(client_secret="wrong"), "success": False}, {"auth": _auth_oauth_cc(), "success": True}]},
    "test_588": {"path": "/auth/oauth2/protected", "steps": [{"auth": _auth_oauth_password(), "success": True}, {"auth": _auth_oauth_password(password="wrong"), "success": False}]},
    "test_589": {"path": "/auth/oauth2/protected", "steps": [{"auth": _auth_oauth_password(password="wrong"), "success": False}, {"auth": _auth_oauth_password(), "success": True}]},
    "test_590": {"path": "/sharing/rest/portals/self", "steps": [{"auth": _auth_arcgis(), "success": True}, {"auth": _auth_arcgis(password="wrong"), "success": False, "contains": "ArcGIS token"}]},
    "test_591": {"path": "/sharing/rest/portals/self", "steps": [{"auth": _auth_arcgis(password="wrong"), "success": False, "contains": "ArcGIS token"}, {"auth": _auth_arcgis(), "success": True}]},
    "test_592": {"path": "/auth/api_key_query", "steps": [{"auth": _auth_query(), "success": True}, {"auth": {"type": "api_key", "location": "query", "key_name": "api_key", "key_value": "WRONG"}, "success": False}]},
    "test_593": {"path": "/auth/api_key_header", "steps": [{"auth": _auth_header(), "success": True}, {"auth": {"type": "api_key", "location": "header", "key_name": "X-API-Key", "key_value": "WRONG"}, "success": False}]},
    "test_594": {"path": "/auth/bearer", "steps": [{"auth": _auth_bearer(), "success": True}, {"auth": _auth_bearer("WRONG"), "success": False}]},
    "test_595": {"path": "/auth/basic", "steps": [{"auth": _auth_basic(), "success": True}, {"auth": _auth_basic(password="wrong"), "success": False}]},
}

EXEC_AUTH_SPECS: dict[str, dict[str, Any]] = {
    "test_596": {"steps": [{"auth": _auth_query(), "filled": True}, {"auth": {"type": "api_key", "location": "query", "key_name": "api_key", "key_value": "WRONG"}, "filled": False}]},
    "test_597": {"steps": [{"auth": {"type": "api_key", "location": "query", "key_name": "api_key", "key_value": "WRONG"}, "filled": False}, {"auth": _auth_query(), "filled": True}]},
    "test_598": {"steps": [{"auth": _auth_header(), "filled": True}, {"auth": {"type": "api_key", "location": "header", "key_name": "X-API-Key", "key_value": "WRONG"}, "filled": False}]},
    "test_599": {"steps": [{"auth": {"type": "api_key", "location": "header", "key_name": "X-API-Key", "key_value": "WRONG"}, "filled": False}, {"auth": _auth_header(), "filled": True}]},
    "test_600": {"steps": [{"auth": _auth_query(), "filled": True}, {"auth": _auth_query(), "filled": True}, {"auth": {"type": "api_key", "location": "query", "key_name": "api_key", "key_value": "WRONG"}, "filled": False}]},
    "test_601": {"steps": [{"auth": {"type": "api_key", "location": "query", "key_name": "api_key", "key_value": "WRONG"}, "filled": False}, {"auth": _auth_query(), "filled": True}, {"auth": {"type": "api_key", "location": "query", "key_name": "api_key", "key_value": "WRONG"}, "filled": False}]},
    "test_602": {"steps": [{"auth": _auth_header(), "filled": True}, {"auth": _auth_query(), "filled": True}]},
    "test_603": {"steps": [{"auth": _auth_query(), "filled": True}, {"auth": _auth_header(), "filled": True}]},
    "test_604": {"steps": [{"auth": {"type": "api_key", "location": "query", "key_name": "api_key", "key_value": "WRONG"}, "filled": False}, {"auth": _auth_header(), "filled": True}]},
    "test_605": {"steps": [{"auth": {"type": "api_key", "location": "header", "key_name": "X-API-Key", "key_value": "WRONG"}, "filled": False}, {"auth": _auth_query(), "filled": True}]},
}

RUNTIME_SPECS: dict[str, dict[str, Any]] = {
    "test_606": {"path": "/error/401", "success": False, "contains": "401"},
    "test_607": {"path": "/error/403", "success": False, "contains": "403"},
    "test_608": {"path": "/error/404", "success": False, "contains": "404"},
    "test_609": {"path": "/error/422", "success": False, "contains": "422"},
    "test_610": {"path": "/error/429", "success": False, "contains": "429"},
    "test_611": {"path": "/error/500", "success": False, "contains": "500"},
    "test_612": {"path": "/error/503", "success": False, "contains": "503"},
    "test_613": {"path": "/error/malformed_json", "success": False},
    "test_614": {"path": "/error/empty_body", "success": False},
    "test_615": {"path": "/error/wrong_content_type", "success": True, "mapping": _output_mapping(("ok", "ok")), "rows": 1, "checks": [(0, "ok", True)]},
    "test_616": {"path": "/error/redirect_loop", "success": True, "mapping": _output_mapping(("done", "done"), ("hops", "hops")), "rows": 1, "checks": [(0, "done", True)]},
    "test_617": {"path": "/data/empty_array", "auth": _auth_query(), "mapping": DATA_MAPPING, "success": True, "rows": 0},
    "test_618": {"path": "/page/cursor", "static": {"cursor": "not-base64"}, "success": False, "contains": "400"},
    "test_619": {"path": "/data/by_id/{item_id}", "auth": _auth_query(), "static": {"item_id": 99}, "success": False, "contains": "404"},
    "test_620": {"path": "/data/by_id/{item_id}", "auth": _auth_query(), "success": False},
    "test_621": {"path": "/geo/cities/by_region/99", "success": False, "contains": "404"},
    "test_622": {"path": "/auth/bearer", "auth": _auth_bearer("WRONG"), "mapping": _output_mapping(("ok", "ok")), "success": False, "contains": "401"},
    "test_623": {"path": "/auth/basic", "auth": _auth_basic(password="wrong"), "mapping": _output_mapping(("ok", "ok")), "success": False, "contains": "401"},
    "test_624": {"path": "/data/object_root", "auth": {"type": "api_key", "location": "query", "key_name": "api_key", "key_value": "WRONG"}, "success": False, "contains": "401"},
    "test_625": {"path": "/auth/oauth2/protected", "auth": _auth_oauth_cc(client_secret="wrong"), "mapping": _output_mapping(("ok", "ok")), "success": False, "contains": "401"},
}

PAGINATION_MUTATION_SPECS: dict[str, dict[str, Any]] = {
    f"test_{626 + index}": {
        "first": PAGINATION_SPECS[source],
        "second": PAGINATION_SPECS[target],
        "first_rows": PAGINATION_SPECS[source]["rows"],
        "second_rows": PAGINATION_SPECS[target]["rows"],
        "stable_column": "id",
        "checks": checks,
    }
    for index, (source, target, checks) in enumerate([
        ("test_526", "test_527", [(7, "id", 8)]),
        ("test_527", "test_526", [(0, "id", 1)]),
        ("test_528", "test_530", [(5, "id", 6)]),
        ("test_531", "test_532", [(7, "id", 8)]),
        ("test_532", "test_535", [(1, "full_name", "Marco Bianchi")]),
        ("test_536", "test_537", [(7, "id", 8)]),
        ("test_537", "test_540", [(9, "id", 10)]),
        ("test_541", "test_542", [(0, "id", 6)]),
        ("test_543", "test_544", [(1, "id", 12)]),
        ("test_545", "test_526", [(0, "id", 1)]),
        ("test_530", "test_529", [(9, "id", 10)]),
        ("test_535", "test_531", [(0, "id", 1)]),
        ("test_540", "test_536", [(0, "id", 1)]),
        ("test_542", "test_541", [(4, "id", 5)]),
        ("test_544", "test_543", [(4, "id", 5)]),
        ("test_526", "test_545", [(0, "id", 1)]),
        ("test_531", "test_534", [(5, "id", 6)]),
        ("test_536", "test_539", [(8, "id", 9)]),
        ("test_529", "test_530", [(5, "id", 6)]),
        ("test_533", "test_535", [(1, "full_name", "Marco Bianchi")]),
    ])
}

CONTRACT_MUTATION_SPECS: dict[str, dict[str, Any]] = {
    f"test_{646 + index}": spec
    for index, spec in enumerate([
        {"first_mapping": _output_mapping(("id", "id"), ("email", "email")), "second_mapping": _output_mapping(("id", "public_id"), ("email", "email")), "tracked_before": "id", "tracked_after": "public_id", "downstream_formula": ("public_id", "public_id_plus_100", 101)},
        {"first_mapping": _output_mapping(("id", "id"), ("age", "age")), "second_mapping": _output_mapping(("id", "id"), ("age", "years")), "tracked_before": "age", "tracked_after": "years", "downstream_formula": ("years", "years_plus_100", 134)},
        {"first_mapping": _output_mapping(("id", "id"), ("city_id", "city_id")), "second_mapping": _output_mapping(("id", "id")), "tracked_before": "city_id", "missing_after": ["city_id"], "present_after": ["id"]},
        {"first_mapping": _output_mapping(("id", "id")), "second_mapping": _output_mapping(("id", "id"), ("status", "status")), "tracked_before": "id", "tracked_after": "id", "present_after": ["status"]},
        {"first_mapping": _output_mapping(("id", "id"), ("email", "email")), "second_mapping": _output_mapping(("id", "id"), ("email", "email_public")), "tracked_before": "email", "tracked_after": "email_public"},
        {"path": "/data/single_object", "first_mapping": _output_mapping(("id", "id"), ("city_id", "city_id")), "second_mapping": _output_mapping(("id", "public_id"), ("city_id", "city_id")), "tracked_before": "id", "tracked_after": "public_id", "downstream_formula": ("public_id", "public_plus_100", 101)},
        {"path": "/data/single_object", "first_mapping": _output_mapping(("id", "id"), ("age", "age")), "second_mapping": _output_mapping(("id", "id"), ("age", "age_public")), "tracked_before": "age", "tracked_after": "age_public", "downstream_formula": ("age_public", "age_public_plus_100", 134)},
        {"path": "/data/nested_deep", "first_mapping": _output_mapping(("response.items.records[*].id", "id"), ("response.items.records[*].age", "age")), "second_mapping": _output_mapping(("response.items.records[*].id", "id_public"), ("response.items.records[*].age", "age")), "tracked_before": "id", "tracked_after": "id_public", "downstream_formula": ("id_public", "id_public_plus_100", 101)},
        {"path": "/geo/cities", "auth": _auth_none(), "first_mapping": GEO_MAPPING, "second_mapping": _output_mapping(("features[*].properties.id", "geo_id"), ("features[*].properties.name", "name"), ("features[*].geometry.type", "geometry_type")), "tracked_before": "id", "tracked_after": "geo_id", "downstream_formula": ("geo_id", "geo_plus_100", 101)},
        {"path": "/geo/cities", "auth": _auth_none(), "first_mapping": GEO_MAPPING, "second_mapping": _output_mapping(("features[*].properties.id", "id"), ("features[*].properties.population", "population")), "tracked_before": "id", "tracked_after": "id", "present_after": ["population"], "missing_after": ["geometry_type"]},
        {"first_mapping": _output_mapping(("id", "id"), ("email", "email"), ("status", "status")), "second_mapping": _output_mapping(("id", "id"), ("status", "status")), "tracked_before": "email", "missing_after": ["email"], "present_after": ["status"]},
        {"first_mapping": _output_mapping(("id", "id"), ("status", "status")), "second_mapping": _output_mapping(("id", "id"), ("status", "state")), "tracked_before": "status", "tracked_after": "state"},
        {"first_mapping": _output_mapping(("id", "id"), ("city_id", "city_id"), ("age", "age")), "second_mapping": _output_mapping(("id", "id"), ("age", "age"), ("city_id", "city_id")), "tracked_before": "city_id", "tracked_after": "city_id"},
        {"first_mapping": _output_mapping(("id", "id"), ("age", "age")), "second_mapping": _output_mapping(("id", "id"), ("age", "age"), ("city_id", "city_id")), "tracked_before": "age", "tracked_after": "age", "present_after": ["city_id"]},
        {"first_mapping": _output_mapping(("id", "id"), ("age", "age"), ("city_id", "city_id")), "second_mapping": _output_mapping(("id", "id"), ("age", "age")), "tracked_before": "age", "tracked_after": "age", "missing_after": ["city_id"]},
        {"path": "/data/array_root", "first_mapping": _output_mapping(("id", "id"), ("email", "email")), "second_mapping": _output_mapping(("id", "id_public"), ("email", "email")), "tracked_before": "id", "tracked_after": "id_public", "downstream_formula": ("id_public", "id_public_plus_100", 101)},
        {"path": "/data/array_root", "first_mapping": _output_mapping(("id", "id"), ("age", "age")), "second_mapping": _output_mapping(("id", "id"), ("age", "years")), "tracked_before": "age", "tracked_after": "years", "downstream_formula": ("years", "years_plus_100", 134)},
        {"path": "/data/with_nulls", "first_mapping": _output_mapping(("age", "age"), ("city_id", "city_id")), "second_mapping": _output_mapping(("age", "years"), ("city_id", "city_id")), "tracked_before": "age", "tracked_after": "years"},
        {"path": "/data/with_nulls", "first_mapping": _output_mapping(("email", "email"), ("status", "status")), "second_mapping": _output_mapping(("email", "email_public"), ("status", "status")), "tracked_before": "email", "tracked_after": "email_public"},
        {"first_mapping": _output_mapping(("id", "id"), ("email", "email")), "second_mapping": _output_mapping(("id", "id_public"), ("email", "email_public")), "tracked_before": "id", "tracked_after": "id_public"},
    ])
}

CHAIN_SPECS: dict[str, dict[str, Any]] = {
    f"test_{666 + index}": spec
    for index, spec in enumerate([
        {"generator": {"path": "/data/object_root", "mapping": DATA_MAPPING, "rows": 12}, "downstream": "id", "downstream_expected": 2},
        {"generator": {"path": "/data/array_root", "mapping": DATA_MAPPING, "rows": 12}, "downstream": "id", "downstream_expected": 2},
        {"generator": {"path": "/data/single_object", "mapping": DATA_MAPPING, "rows": 1}, "downstream": "id", "downstream_expected": 2},
        {"generator": {"path": "/data/object_root", "mapping": DATA_MAPPING, "rows": 12}, "mutate_auth": {"type": "api_key", "location": "query", "key_name": "api_key", "key_value": "WRONG"}},
        {"generator": {"path": "/data/array_root", "mapping": DATA_MAPPING, "rows": 12}, "mutate_auth": {"type": "api_key", "location": "query", "key_name": "api_key", "key_value": "WRONG"}},
        {"generator": {"path": "/page/offset", "mapping": DATA_MAPPING, "auth": _auth_none(), "pagination": {"type": "offset", "page_size": 5, "max_rows": 12}, "rows": 12}, "downstream": "id", "downstream_expected": 2},
        {"generator": {"path": "/page/cursor", "mapping": _output_mapping(("items[*].id", "id"), ("items[*].email", "email")), "auth": _auth_none(), "static": {"page_size": 5}, "pagination": {"type": "cursor", "page_size": 5, "max_rows": 12}, "rows": 12}, "downstream": "id", "downstream_expected": 2},
        {"generator": {"path": "/page/link_header", "mapping": DATA_MAPPING, "auth": _auth_none(), "static": {"size": 5}, "pagination": {"type": "link", "page_size": 5, "max_rows": 12}, "rows": 12}, "downstream": "id", "downstream_expected": 2},
        {"generator": {"path": "/data/object_root", "mapping": _output_mapping(("id", "id"), ("city_id", "city_id")), "rows": 12}, "api_mapping": _output_mapping(("status", "lookup_status")), "check_column": "lookup_status", "expected": "active"},
        {"generator": {"path": "/data/object_root", "mapping": _output_mapping(("city_id", "id"), ("email", "email")), "rows": 12}, "api_mapping": _output_mapping(("email", "lookup_email")), "check_column": "lookup_email", "expected": "anna.rossi@example.com"},
        {"generator": {"path": "/data/nested_deep", "mapping": _output_mapping(("response.items.records[*].id", "id"), ("response.items.records[*].email", "email")), "rows": 12}, "downstream": "id", "downstream_expected": 2},
        {"generator": {"path": "/data/nested_deep", "mapping": _output_mapping(("response.items.records[*].id", "id"), ("response.items.records[*].city_id", "city_id")), "rows": 12}, "api_mapping": _output_mapping(("status", "lookup_status")), "check_column": "lookup_status", "expected": "active"},
        {"generator": {"path": "/data/with_nulls", "mapping": _output_mapping(("id", "id"), ("email", "email")), "rows": 5}, "downstream": "id", "downstream_expected": 2},
        {"generator": {"path": "/data/by_id/{item_id}", "mapping": DATA_MAPPING, "static": {"item_id": 1}, "rows": 1}, "downstream": "id", "downstream_expected": 2},
        {"generator": {"path": "/data/by_id/{item_id}", "mapping": DATA_MAPPING, "static": {"item_id": 2}, "rows": 1}, "expected": "marco.bianchi@example.com", "downstream": "id", "downstream_expected": 3},
        {"generator": {"path": "/data/object_root", "mapping": DATA_MAPPING, "rows": 12}, "api_mapping": _output_mapping(("city_id", "lookup_city_id")), "check_column": "lookup_city_id", "expected": 1},
        {"generator": {"path": "/data/array_root", "mapping": DATA_MAPPING, "rows": 12}, "api_mapping": _output_mapping(("city_id", "lookup_city_id")), "check_column": "lookup_city_id", "expected": 1},
        {"generator": {"path": "/page/offset", "mapping": DATA_MAPPING, "auth": _auth_none(), "pagination": {"type": "offset", "page_size": 2, "max_rows": 6}, "rows": 6}, "api_mapping": _output_mapping(("email", "lookup_email")), "check_column": "lookup_email", "expected": "anna.rossi@example.com"},
        {"generator": {"path": "/geo/cities", "mapping": _output_mapping(("features[*].properties.id", "id"), ("features[*].properties.name", "name")), "auth": _auth_none(), "rows": 9}, "downstream": "id", "downstream_expected": 2},
        {"generator": {"path": "/geo/cities/by_region/{region_id}", "mapping": _output_mapping(("features[*].properties.id", "id"), ("features[*].properties.name", "name")), "auth": _auth_none(), "static": {"region_id": 1}, "rows": 3}, "downstream": "id", "downstream_expected": 2},
    ])
}


def _case_runner(case_id: str) -> Callable[[E2EContext], None]:
    def _run(ctx: E2EContext) -> None:
        if case_id in STATE_SPECS:
            _api_test_sequence(ctx, case_id, STATE_SPECS[case_id])
            return
        if case_id in EXEC_AUTH_SPECS:
            _api_execute_auth_sequence(ctx, case_id, EXEC_AUTH_SPECS[case_id])
            return
        if case_id in RUNTIME_SPECS:
            _generator_runtime(ctx, case_id, RUNTIME_SPECS[case_id])
            return
        if case_id in PAGINATION_MUTATION_SPECS:
            _pagination_mutation(ctx, case_id, PAGINATION_MUTATION_SPECS[case_id])
            return
        if case_id in CONTRACT_MUTATION_SPECS:
            _contract_mutation(ctx, case_id, CONTRACT_MUTATION_SPECS[case_id])
            return
        if case_id in CHAIN_SPECS:
            _chain_state(ctx, case_id, CHAIN_SPECS[case_id])
            return
        _assert_generator_spec(ctx, case_id, PAGINATION_SPECS["test_526"])
    return _run


CASES: dict[str, CaseFn] = {
    f"test_{i}": _ctx_case(_case_runner(f"test_{i}"))
    for i in range(586, 686)
}


def run_case(case_id: str) -> None:
    case = CASES.get(case_id)
    assert_true(case is not None, f"Unknown arricchisci stateful matrix case: {case_id}")
    case()


__all__ = ["run_case"]
