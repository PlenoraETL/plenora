"""ARRICCHISCI/output_contract perimeter coverage.

These cases extend the project-to-project contract suite after the
ESTRAI/MANIPOLA boundary:
- ARRICCHISCI consumes project_source output_contracts;
- ARRICCHISCI produces output_contracts that downstream projects consume;
- project_source visibility and freshness are honored at ARRICCHISCI runtime;
- stable column ids survive ARRICCHISCI re-execution and producer renames.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Callable

from _e2e import (
    E2EContext,
    assert_equal,
    assert_true,
    edge,
    project_source_node_from_contract,
    result_node,
    run_test,
    set_output_contract,
    source_column,
    source_node,
    transform_node,
)
from _estrai_manipola_final import (
    _columns_by_physical,
    _connection,
    _create_executed_estrai,
    _execute_project_raw,
    _node_by_id,
    _source_result_canvas,
)
CaseFn = Callable[[], None]


def _noop_enrich_api(ctx: E2EContext, suffix: str) -> str:
    """Crea una connessione enricher 'no-op': path statico ``/data/single_object``
    (nessun param richiesto) che mappa una sola colonna costante ``_enrich``.

    Rende un canvas project_source/static -> result un arricchisci VALIDO
    (regola ARRICCHISCI_NO_ENRICHER) senza richiedere colonne dalla sorgente,
    preservando conteggio righe e id delle colonne upstream: aggiunge solo
    ``_enrich`` (stesso valore per ogni riga).
    """
    # import lazy: _arricchisci_api_final importa da questo modulo (ciclo).
    from _arricchisci_api_final import _create_api, _output_mapping
    return _create_api(
        ctx,
        name=f"e2e_v2_{suffix}_noop_enrich",
        path="/data/single_object",
        output_mapping=_output_mapping(("email", "_enrich")),
    )


def _with_noop_enricher(canvas: dict[str, Any], enrich_api_id: str) -> dict[str, Any]:
    """Inserisce un nodo api_enrich 'no-op' tra l'ultimo nodo non-result e il
    nodo result del canvas (source/static -> api_enrich -> result)."""
    from _arricchisci_api_final import _api_node, _output_mapping
    nodes = canvas["nodes"]
    edges = canvas["edges"]
    result = next(n for n in nodes if n.get("type") == "result")
    result_id = result["id"]
    # nodo che alimentava il result diventa upstream dell'enricher
    feeder_edge = next(e for e in edges if e["target"] == result_id)
    feeder_source = feeder_edge["source"]
    api = _api_node(
        enrich_api_id,
        param_mapping={},
        output_mapping=_output_mapping(("email", "_enrich")),
    )
    new_edges = [e for e in edges if e is not feeder_edge]
    new_edges.append(edge("e-feeder-enrich", feeder_source, api["id"]))
    new_edges.append(edge("e-enrich-result", api["id"], result_id))
    return {"nodes": [*nodes, api], "edges": new_edges}


def _ctx_case(fn: Callable[[E2EContext], None]) -> CaseFn:
    return lambda: run_test(fn)


def _arricchisci_source_canvas(
    source_project_id: str,
    contract: dict[str, Any],
    *,
    source_node: dict[str, Any] | None = None,
    file_name: str = "e2e_arricchisci_contract",
) -> dict[str, Any]:
    source = source_node or project_source_node_from_contract(
        node_id="source-1",
        source_project_id=source_project_id,
        contract=contract,
    )
    return {
        "nodes": [
            source,
            result_node(format="csv", fileName=file_name),
        ],
        "edges": [edge("e-source-result", source["id"], "result-1")],
    }


def _static_values_canvas(
    fields: list[dict[str, Any]],
    *,
    file_name: str = "e2e_arricchisci_static",
) -> dict[str, Any]:
    static = {
        "id": "static-1",
        "type": "staticValues",
        "position": {"x": 0, "y": 0},
        "fields": fields,
        "data": {"fields": fields},
    }
    return {
        "nodes": [
            static,
            result_node(format="csv", fileName=file_name),
        ],
        "edges": [edge("e-static-result", "static-1", "result-1")],
    }


def _active_customers_canvas_with_age_alias(
    connection_id: str,
    *,
    age_alias: str,
) -> dict[str, Any]:
    source = source_node(
        node_id="customers",
        table_name="customers",
        alias="c",
        connection_id=connection_id,
        columns=[
            source_column("id", logical_type="int", alias="customer_id"),
            source_column("full_name", logical_type="str", alias="customer_name"),
            source_column("age", logical_type="int", alias=age_alias),
            source_column("status", logical_type="str"),
        ],
        filters=[{"column": "status", "operator": "=", "value": "active"}],
    )
    return {
        "nodes": [
            source,
            result_node(format="csv", fileName="e2e_arricchisci_age_alias"),
        ],
        "edges": [edge("e-customers-result", "customers", "result-1")],
    }


def _create_executed_arricchisci(
    ctx: E2EContext,
    *,
    name: str,
    canvas: dict[str, Any],
    force_recalculate: bool = True,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    project_id = ctx.create_project(
        name=name,
        project_type="arricchisci",
        canvas=canvas,
    )
    result = ctx.execute_project(project_id, force_recalculate=force_recalculate)
    contract = ctx.get_project(project_id).get("output_contract") or result.get("output_contract") or {}
    assert_true(contract.get("columns"), f"{name}: ARRICCHISCI must persist output_contract")
    return project_id, contract, result


def test_462(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "arr_consume_estrai")
    upstream_id, upstream_contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_462_estrai_upstream",
        canvas=_active_customers_canvas_with_age_alias(connection_id, age_alias="years"),
    )
    arr_id, arr_contract, result = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_462_arricchisci_consumes_estrai",
        canvas=_with_noop_enricher(
            _arricchisci_source_canvas(upstream_id, upstream_contract),
            _noop_enrich_api(ctx, "test_462"),
        ),
    )
    assert_true(int(result.get("rows_count") or 0) > 0, "ARRICCHISCI must exchange upstream ESTRAI rows")
    by_physical = _columns_by_physical(arr_contract)
    assert_true({"customer_id", "customer_name", "years", "status"} <= set(by_physical), "ARRICCHISCI output_contract must expose upstream public schema")
    rows = ctx.preview_project(arr_id, limit=5).get("rows") or []
    assert_true(rows and "years" in rows[0], "ARRICCHISCI preview must use output_contract physical names")


def test_463(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "arr_visibility")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_463_visibility_upstream",
        canvas=_active_customers_canvas_with_age_alias(connection_id, age_alias="years"),
    )
    source = project_source_node_from_contract(
        node_id="source-1",
        source_project_id=upstream_id,
        contract=contract,
    )
    for column in source["columns"]:
        if column.get("name") == "years":
            column["visible"] = False
    source["data"]["columns"] = copy.deepcopy(source["columns"])
    arr_id, arr_contract, _ = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_463_arricchisci_visibility",
        canvas=_with_noop_enricher(
            _arricchisci_source_canvas(
                upstream_id,
                contract,
                source_node=source,
                file_name="e2e_arricchisci_hidden_years",
            ),
            _noop_enrich_api(ctx, "test_463"),
        ),
    )
    assert_true("years" not in _columns_by_physical(arr_contract), "ARRICCHISCI hidden project_source column must stay out of output_contract")
    rows = ctx.preview_project(arr_id, limit=3).get("rows") or []
    assert_true(rows and "years" not in rows[0], "ARRICCHISCI hidden project_source column must stay out of exchanged data")


def test_464(ctx: E2EContext) -> None:
    upstream_id = ctx.create_project(name="e2e_v2_test_464_missing_result_upstream", project_type="estrai")
    contract = {
        "schema_version": 1,
        "producer_project_id": upstream_id,
        "revision": 1,
        "columns": [
            {"id": "col-id", "source_name": "id", "physical_name": "id", "display_name": "id", "type": {"logical": "integer"}, "ordinal": 0, "status": "active"},
        ],
    }
    set_output_contract(upstream_id, contract)
    arr_id = ctx.create_project(
        name="e2e_v2_test_464_arricchisci_missing_result_freshness",
        project_type="arricchisci",
        canvas=_arricchisci_source_canvas(upstream_id, contract),
    )
    project = ctx.get_project(arr_id)
    source = _node_by_id(project["canvas"])["source-1"]
    freshness = source.get("source_freshness") or (source.get("data") or {}).get("source_freshness")
    assert_true(freshness and freshness.get("stale"), "ARRICCHISCI GET project must annotate missing-result project_source as stale")
    assert_equal(freshness.get("reason"), "missing_result", "ARRICCHISCI missing upstream result freshness reason")


def test_465(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "arr_stale_fail")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_465_stale_fail_upstream",
        canvas=_active_customers_canvas_with_age_alias(connection_id, age_alias="years"),
    )
    arr_id = ctx.create_project(
        name="e2e_v2_test_465_arricchisci_stale_fail",
        project_type="arricchisci",
        canvas=_with_noop_enricher(
            _arricchisci_source_canvas(upstream_id, contract),
            _noop_enrich_api(ctx, "test_465"),
        ),
    )
    time.sleep(1.1)
    ctx.update_canvas(upstream_id, _active_customers_canvas_with_age_alias(connection_id, age_alias="years"))
    result = _execute_project_raw(ctx, arr_id, force_recalculate=False)
    assert_true(result.get("success") is False, "ARRICCHISCI stale project_source must fail when force_recalculate=false")
    assert_true("modificato dopo" in str(result.get("message") or ""), "ARRICCHISCI stale failure must explain source was modified")


def test_466(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "arr_stale_recalc")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_466_stale_recalc_upstream",
        canvas=_active_customers_canvas_with_age_alias(connection_id, age_alias="years"),
    )
    arr_id = ctx.create_project(
        name="e2e_v2_test_466_arricchisci_stale_recalc",
        project_type="arricchisci",
        canvas=_with_noop_enricher(
            _arricchisci_source_canvas(upstream_id, contract),
            _noop_enrich_api(ctx, "test_466"),
        ),
    )
    time.sleep(1.1)
    pending_canvas = _active_customers_canvas_with_age_alias(connection_id, age_alias="years")
    pending_canvas["nodes"][0]["filters"] = [{"column": "status", "operator": "=", "value": "pending"}]
    pending_canvas["nodes"][0]["data"]["filters"] = [{"column": "status", "operator": "=", "value": "pending"}]
    ctx.update_canvas(upstream_id, pending_canvas)
    result = _execute_project_raw(ctx, arr_id, force_recalculate=True)
    assert_true(result.get("success"), f"ARRICCHISCI force_recalculate=true must recompute stale upstream: {result!r}")
    assert_equal(result.get("rows_count"), 2, "ARRICCHISCI forced recompute must exchange fresh upstream rows")


def test_467(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "arr_id_stable")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_467_id_upstream",
        canvas=_active_customers_canvas_with_age_alias(connection_id, age_alias="years"),
    )
    arr_id, contract1, _ = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_467_arricchisci_id_stable",
        canvas=_with_noop_enricher(
            _arricchisci_source_canvas(upstream_id, contract),
            _noop_enrich_api(ctx, "test_467"),
        ),
    )
    first = _columns_by_physical(contract1)["years"]
    ctx.execute_project(arr_id, force_recalculate=True)
    contract2 = ctx.get_project(arr_id).get("output_contract") or {}
    second = _columns_by_physical(contract2)["years"]
    assert_equal(second["id"], first["id"], "ARRICCHISCI output_contract id must stay stable across re-execute")
    assert_true(int(contract2.get("revision") or 0) > int(contract1.get("revision") or 0), "ARRICCHISCI output_contract revision must advance")


def test_468(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "arr_rename_chain")
    upstream_canvas = _active_customers_canvas_with_age_alias(connection_id, age_alias="age_public")
    upstream_id, upstream_contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_468_estrai_upstream",
        canvas=upstream_canvas,
    )
    arr_id, arr_contract, _ = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_468_arricchisci_producer",
        canvas=_with_noop_enricher(
            _arricchisci_source_canvas(upstream_id, upstream_contract),
            _noop_enrich_api(ctx, "test_468"),
        ),
    )
    public_id = _columns_by_physical(arr_contract)["age_public"]["id"]
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={
            "formula": "age_public + 1",
            "new_column": "age_plus_one",
            "formula_column_refs": [{"id": public_id, "name": "age_public"}],
        },
    )
    downstream_id = ctx.create_project(
        name="e2e_v2_test_468_manipola_follows_arricchisci",
        project_type="manipola",
        canvas={
            "nodes": [
                project_source_node_from_contract(node_id="source-down", source_project_id=arr_id, contract=arr_contract),
                formula,
                result_node(format="csv", fileName="e2e_arricchisci_rename_downstream"),
            ],
            "edges": [
                edge("e-source-formula", "source-down", "formula-1"),
                edge("e-formula-result", "formula-1", "result-1"),
            ],
        },
    )

    next_canvas = _active_customers_canvas_with_age_alias(connection_id, age_alias="age_final")
    ctx.update_canvas(upstream_id, next_canvas)
    ctx.execute_project(upstream_id, force_recalculate=True)
    ctx.execute_project(arr_id, force_recalculate=True)
    synced = ctx.get_project(downstream_id)["canvas"]
    config = _node_by_id(synced)["formula-1"]["config"]
    assert_equal(config["formula"], "age_final + 1", "MANIPOLA formula must follow ARRICCHISCI producer rename by stable id")
    assert_equal(config["formula_column_refs"], [{"id": public_id, "name": "age_final"}], "MANIPOLA formula refs must preserve ARRICCHISCI producer column id")


def test_469(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "man_to_arr")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_469_estrai_upstream",
        canvas=_active_customers_canvas_with_age_alias(connection_id, age_alias="years"),
    )
    years_id = _columns_by_physical(contract)["years"]["id"]
    manipola_id = ctx.create_project(
        name="e2e_v2_test_469_manipola_producer",
        project_type="manipola",
        canvas={
            "nodes": [
                project_source_node_from_contract(node_id="source-1", source_project_id=upstream_id, contract=contract),
                transform_node(
                    node_id="formula-1",
                    transform_op="formula",
                    config={
                        "formula": "years * 2",
                        "new_column": "double_years",
                        "formula_column_refs": [{"id": years_id, "name": "years"}],
                    },
                ),
                result_node(format="csv", fileName="e2e_manipola_to_arricchisci"),
            ],
            "edges": [
                edge("e-source-formula", "source-1", "formula-1"),
                edge("e-formula-result", "formula-1", "result-1"),
            ],
        },
    )
    ctx.execute_project(manipola_id)
    manipola_contract = ctx.get_project(manipola_id).get("output_contract") or {}
    arr_id, arr_contract, _ = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_469_arricchisci_consumes_manipola",
        canvas=_with_noop_enricher(
            _arricchisci_source_canvas(manipola_id, manipola_contract),
            _noop_enrich_api(ctx, "test_469"),
        ),
    )
    assert_true("double_years" in _columns_by_physical(arr_contract), "ARRICCHISCI must expose calculated MANIPOLA producer columns")
    rows = ctx.preview_project(arr_id, limit=5).get("rows") or []
    assert_true(rows, "ARRICCHISCI must preview MANIPOLA producer rows")
    for row in rows[:3]:
        assert_equal(int(float(row["double_years"])), int(float(row["years"])) * 2, "MANIPOLA calculated values must survive ARRICCHISCI exchange")


def test_470(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "arr_to_arr")
    upstream_id, contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_470_estrai_upstream",
        canvas=_active_customers_canvas_with_age_alias(connection_id, age_alias="years"),
    )
    arr_a_id, arr_a_contract, _ = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_470_arricchisci_a",
        canvas=_with_noop_enricher(
            _arricchisci_source_canvas(upstream_id, contract),
            _noop_enrich_api(ctx, "test_470a"),
        ),
    )
    arr_b_id, arr_b_contract, result = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_470_arricchisci_b",
        canvas=_with_noop_enricher(
            _arricchisci_source_canvas(arr_a_id, arr_a_contract),
            _noop_enrich_api(ctx, "test_470b"),
        ),
    )
    assert_true(int(result.get("rows_count") or 0) > 0, "ARRICCHISCI -> ARRICCHISCI must exchange rows")
    assert_true("years" in _columns_by_physical(arr_b_contract), "ARRICCHISCI -> ARRICCHISCI must exchange output_contract")
    rows = ctx.preview_project(arr_b_id, limit=3).get("rows") or []
    assert_true(rows and "years" in rows[0], "ARRICCHISCI chain preview must use public contract")


def test_471(ctx: E2EContext) -> None:
    arr_id, arr_contract, result = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_471_arricchisci_static_producer",
        canvas=_with_noop_enricher(
            _static_values_canvas([
                {"name": "seed", "value": "7"},
                {"name": "label", "value": "alpha"},
            ]),
            _noop_enrich_api(ctx, "test_471"),
        ),
    )
    assert_equal(result.get("rows_count"), 1, "ARRICCHISCI static producer must emit one row")
    assert_true({"seed", "label"} <= set(_columns_by_physical(arr_contract)), "ARRICCHISCI static output_contract must expose static fields")

    downstream_id = ctx.create_project(
        name="e2e_v2_test_471_manipola_consumes_arricchisci_static",
        project_type="manipola",
        canvas=_source_result_canvas(arr_id, arr_contract),
    )
    rows = ctx.preview_project(downstream_id, limit=3).get("rows") or []
    assert_true(len(rows) == 1 and set(rows[0]) == {"seed", "label", "_enrich"}, "MANIPOLA must consume ARRICCHISCI static output_contract columns")
    assert_equal(str(rows[0]["seed"]), "7", "MANIPOLA must consume ARRICCHISCI static seed value")
    assert_equal(rows[0]["label"], "alpha", "MANIPOLA must consume ARRICCHISCI static label value")


def test_472(ctx: E2EContext) -> None:
    enrich_api_id = _noop_enrich_api(ctx, "test_472")
    arr_id, contract1, _ = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_472_arricchisci_static_id_stable",
        canvas=_with_noop_enricher(
            _static_values_canvas([
                {"name": "seed", "value": "7"},
                {"name": "label", "value": "alpha"},
            ]),
            enrich_api_id,
        ),
    )
    first = _columns_by_physical(contract1)["seed"]
    next_canvas = _with_noop_enricher(
        _static_values_canvas([
            {"name": "seed", "value": "8"},
            {"name": "label", "value": "beta"},
        ]),
        enrich_api_id,
    )
    ctx.update_canvas(arr_id, next_canvas)
    ctx.execute_project(arr_id, force_recalculate=True)
    contract2 = ctx.get_project(arr_id).get("output_contract") or {}
    second = _columns_by_physical(contract2)["seed"]
    assert_equal(second["id"], first["id"], "ARRICCHISCI static output id must stay stable across value edits")


def test_473(ctx: E2EContext) -> None:
    connection_id = _connection(ctx, "arr_dup")
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
    upstream_id, upstream_contract, _ = _create_executed_estrai(
        ctx,
        connection_id=connection_id,
        name="e2e_v2_test_473_dup_alias_upstream",
        canvas={
            "nodes": [source, result_node(format="csv", fileName="e2e_arricchisci_dup_upstream")],
            "edges": [edge("e-customers-result", "customers", "result-1")],
        },
    )
    arr_id, arr_contract, _ = _create_executed_arricchisci(
        ctx,
        name="e2e_v2_test_473_arricchisci_dup_alias",
        canvas=_with_noop_enricher(
            _arricchisci_source_canvas(upstream_id, upstream_contract),
            _noop_enrich_api(ctx, "test_473"),
        ),
    )
    physical = [column["physical_name"] for column in arr_contract.get("columns") or []]
    assert_equal(len(physical), len(set(physical)), "ARRICCHISCI output_contract physical names must remain unique")
    assert_true(physical[0] == "dup" and physical[1].startswith("dup_") and physical[1] != "dup", "ARRICCHISCI must preserve uniquified upstream duplicate aliases")
    rows = ctx.preview_project(arr_id, limit=3).get("rows") or []
    assert_true(rows and {physical[0], physical[1], "age"} <= set(rows[0]), "ARRICCHISCI preview rows must match unique public names")


CASES: dict[str, CaseFn] = {
    "test_462": _ctx_case(test_462),
    "test_463": _ctx_case(test_463),
    "test_464": _ctx_case(test_464),
    "test_465": _ctx_case(test_465),
    "test_466": _ctx_case(test_466),
    "test_467": _ctx_case(test_467),
    "test_468": _ctx_case(test_468),
    "test_469": _ctx_case(test_469),
    "test_470": _ctx_case(test_470),
    "test_471": _ctx_case(test_471),
    "test_472": _ctx_case(test_472),
    "test_473": _ctx_case(test_473),
}


def run_case(case_id: str) -> None:
    case = CASES.get(case_id)
    assert_true(case is not None, f"Unknown arricchisci contract case: {case_id}")
    case()


__all__ = ["run_case"]
