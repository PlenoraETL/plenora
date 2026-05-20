"""Fourth output-contract propagation block for E2E v2 host tests."""

from __future__ import annotations

from typing import Callable

from _contract_more import (
    _col,
    _contract,
    _project_with_transform,
    _resync_and_config,
    _resync_and_nodes,
    _upstream,
)
from _contract_more3 import _inactive, _sync_config
from _e2e import (
    E2EContext,
    assert_equal,
    edge,
    project_source_node_from_contract,
    result_node,
    run_test,
    set_output_contract,
    transform_node,
)


def _formula_project(ctx: E2EContext, case_id: str, *, columns: list[dict], config: dict) -> tuple[str, str]:
    upstream_id, contract = _upstream(ctx, case_id, columns)
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="formula-1",
            transform_op="formula",
            config=config,
        ),
        name=f"e2e_v2_{case_id}_formula_more4",
    )
    return upstream_id, project_id


def _two_source_project(
    ctx: E2EContext,
    case_id: str,
    *,
    left_columns: list[dict] | None = None,
    right_columns: list[dict] | None = None,
    join_config: dict | None = None,
) -> tuple[str, str, str]:
    left_id, left_contract = _upstream(ctx, f"{case_id}_left", left_columns or [_col("left-id", "id", "id")])
    right_id, right_contract = _upstream(ctx, f"{case_id}_right", right_columns or [_col("right-id", "id", "id")])
    left = project_source_node_from_contract(node_id="left-source", source_project_id=left_id, contract=left_contract)
    right = project_source_node_from_contract(node_id="right-source", source_project_id=right_id, contract=right_contract)
    join = transform_node(
        node_id="join-1",
        transform_op="join",
        config=join_config or {
            "left_on": "id",
            "left_on_column_id": "left-id",
            "right_on": "id",
            "right_on_column_id": "right-id",
            "how": "inner",
        },
    )
    project_id = ctx.create_project(
        name=f"e2e_v2_{case_id}_two_source",
        project_type="manipola",
        canvas={
            "nodes": [left, right, join, result_node()],
            "edges": [
                edge("e-left", "left-source", "join-1"),
                edge("e-right", "right-source", "join-1"),
                edge("e-result", "join-1", "result-1"),
            ],
        },
    )
    return left_id, right_id, project_id


def test_201(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_201",
        columns=[_col("col-age", "age", "age"), _col("col-score", "score", "score")],
        config={"formula": "age + score", "new_column": "calc"},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "age")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "", "Formula without refs must clear when a referenced legacy token is removed")
    assert_equal(config["formula_column_refs"], [], "Formula without refs must clear refs when a referenced legacy token is removed")


def test_202(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_202",
        columns=[_col("col-age", "age", "age")],
        config={"formula": "age * 2", "new_column": "calc"},
    )
    set_output_contract(upstream_id, _contract(upstream_id, []))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "", "Formula without refs must clear when all referenced tokens are removed")
    assert_equal(config["formula_column_refs"], [], "Formula without refs must set empty refs when all referenced tokens are removed")


def test_203(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_203",
        columns=[_col("col-age", "age", "age"), _col("col-score", "score", "score")],
        config={"formula": "age + score", "new_column": "calc"},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "", "Formula without refs must clear when one token renames and another is removed")


def test_204(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_204",
        columns=[_col("col-age", "age", "age")],
        config={"formula": "df['age'] * 2", "new_column": "calc"},
    )
    set_output_contract(upstream_id, _contract(upstream_id, []))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "", "Formula without refs must clear removed df['column'] tokens")


def test_205(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_205",
        columns=[_col("col-total", "total amount", "total amount")],
        config={"formula": "`total amount` * 2", "new_column": "calc"},
    )
    set_output_contract(upstream_id, _contract(upstream_id, []))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "", "Formula without refs must clear removed backtick tokens")


def test_206(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_206",
        columns=[_col("col-age", "age", "age")],
        config={"formula": "age + external_value", "new_column": "calc"},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "years + external_value", "Formula without refs must rewrite known token and preserve unknown identifiers")
    assert_equal(config["formula_column_refs"], [{"id": "col-age", "name": "years"}], "Formula without refs must collect only known rewritten refs")


def test_207(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_207",
        columns=[_col("col-age", "age", "age")],
        config={"formula": "age + myage", "new_column": "calc"},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "years + myage", "Formula without refs must respect word boundaries")


def test_208(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_208",
        columns=[_col("col-age", "age", "age")],
        config={"formula": "age * 2", "new_column": "calc", "formula_column_refs": []},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "years * 2", "Empty formula refs must be treated like missing refs")
    assert_equal(config["formula_column_refs"], [{"id": "col-age", "name": "years"}], "Empty formula refs must be rebuilt")


def test_209(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_209",
        columns=[_col("col-age", "age", "age")],
        config={"formula": "age * 2", "formula_column_refs": [{"id": "stale-id", "name": "age"}]},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "age")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula_column_refs"], [{"id": "col-age", "name": "age"}], "Formula refs with stale ids must be repaired by unambiguous name")


def test_210(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_210",
        columns=[_col("col-age", "age", "age")],
        config={"formula": "age * 2", "formula_column_refs": [{"name": "age"}]},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "age")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula_column_refs"], [{"id": "col-age", "name": "age"}], "Formula refs without ids must be repaired by unambiguous name")


def test_211(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_211",
        config={"column": "age", "column_column_id": "stale-id"},
        next_columns=[_col("col-age", "age", "age")],
        previous_columns=[_col("col-age", "age", "age")],
    )
    assert_equal(synced["column_column_id"], "col-age", "Scalar stale companion id must be repaired by current name")


def test_212(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_212",
        config={"columns": ["age", "score"], "columns_column_ids": ["stale-age", "stale-score"]},
        next_columns=[_col("col-age", "age", "age"), _col("col-score", "score", "score")],
        previous_columns=[_col("col-age", "age", "age"), _col("col-score", "score", "score")],
    )
    assert_equal(synced["columns_column_ids"], ["col-age", "col-score"], "List stale companion ids must be repaired by current names")


def test_213(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_213",
        config={"aggregations": [{"column": "age", "column_id": "stale-id", "function": "sum"}]},
        next_columns=[_col("col-age", "age", "age")],
        previous_columns=[_col("col-age", "age", "age")],
    )
    assert_equal(synced["aggregations"], [{"column": "age", "column_id": "col-age", "function": "sum"}], "Dict stale companion id must be repaired by current name")


def test_214(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_214",
        config={"column": "", "column_column_id": "col-age"},
        next_columns=[_col("col-age", "age", "years")],
        previous_columns=[_col("col-age", "age", "age")],
    )
    assert_equal(synced["column"], "years", "Scalar empty name with valid id must be filled")


def test_215(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_215",
        config={"columns": [""], "columns_column_ids": ["col-age"]},
        next_columns=[_col("col-age", "age", "years")],
        previous_columns=[_col("col-age", "age", "age")],
    )
    assert_equal(synced["columns"], ["years"], "List empty name with valid id must be filled")


def test_216(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_216",
        config={"renames": [{"old_name": "", "old_name_id": "col-age", "new_name": "age2"}]},
        next_columns=[_col("col-age", "age", "years")],
        previous_columns=[_col("col-age", "age", "age")],
    )
    assert_equal(synced["renames"], [{"old_name": "years", "old_name_id": "col-age", "new_name": "age2"}], "Dict empty name with valid id must be filled")


def test_217(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_217",
        config={"column": "age", "column_column_id": "col-age"},
        next_columns=[_inactive(_col("col-age", "age", "age"))],
        previous_columns=[_col("col-age", "age", "age")],
    )
    assert_equal(synced["column"], "", "Inactive-only replacement must clear scalar config")
    assert_equal(synced["column_column_id"], None, "Inactive-only replacement must clear scalar id")


def test_218(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_218",
        config={"columns": ["age"], "columns_column_ids": ["col-age"]},
        next_columns=[_inactive(_col("col-age", "age", "age"))],
        previous_columns=[_col("col-age", "age", "age")],
    )
    assert_equal(synced["columns"], [], "Inactive-only replacement must clear list config")
    assert_equal(synced["columns_column_ids"], [], "Inactive-only replacement must clear list ids")


def test_219(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_219",
        config={"maskings": [{"column": "age", "column_id": "col-age", "method": "hash"}]},
        next_columns=[_inactive(_col("col-age", "age", "age"))],
        previous_columns=[_col("col-age", "age", "age")],
    )
    assert_equal(synced["maskings"], [], "Inactive-only replacement must clear dict items")


def test_220(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_220",
        columns=[_col("col-age", "age", "age")],
        config={"formula": "age * 2", "formula_column_refs": [{"id": "col-age", "name": "age"}]},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_inactive(_col("col-age", "age", "age"))]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "", "Inactive-only replacement must clear formula refs")


def test_221(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(ctx, "test_221")
    set_output_contract(left_id, _contract(left_id, [_col("left-id", "id", "customer_id")]))
    set_output_contract(right_id, _contract(right_id, [_col("right-id", "id", "city_id")]))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["left_on"], "customer_id", "Two-source join left key must follow left rename")
    assert_equal(config["right_on"], "city_id", "Two-source join right key must follow right rename")


def test_222(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(ctx, "test_222")
    set_output_contract(left_id, _contract(left_id, []))
    set_output_contract(right_id, _contract(right_id, [_col("right-id", "id", "id")]))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["left_on"], "", "Two-source join must clear removed left key")
    assert_equal(config["right_on"], "id", "Two-source join must preserve right key when only left is removed")


def test_223(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(ctx, "test_223")
    set_output_contract(left_id, _contract(left_id, [_col("left-id", "id", "id")]))
    set_output_contract(right_id, _contract(right_id, []))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["left_on"], "id", "Two-source join must preserve left key when only right is removed")
    assert_equal(config["right_on"], "", "Two-source join must clear removed right key")


def test_224(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(ctx, "test_224")
    set_output_contract(left_id, _contract(left_id, [_col("left-id", "id", "id"), _col("left-extra", "extra", "extra")]))
    set_output_contract(right_id, _contract(right_id, [_col("right-id", "id", "id")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal([c["name"] for c in nodes["left-source"]["columns"]], ["id", "extra"], "Added left source column must sync only to left project_source")
    assert_equal([c["name"] for c in nodes["right-source"]["columns"]], ["id"], "Right project_source must not receive left additions")


def test_225(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(ctx, "test_225")
    nodes = _resync_and_nodes(ctx, project_id)
    nodes["left-source"]["columns"][0]["visible"] = False
    nodes["left-source"]["data"]["columns"][0]["visible"] = False
    canvas = ctx.get_project(project_id)["canvas"]
    ctx.update_canvas(project_id, {"nodes": list(nodes.values()), "edges": canvas["edges"]})
    set_output_contract(left_id, _contract(left_id, [_col("left-id", "id", "customer_id")]))
    set_output_contract(right_id, _contract(right_id, [_col("right-id", "id", "city_id")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["left-source"]["columns"][0]["visible"], False, "Left project_source visibility must be preserved independently")
    assert_equal(nodes["right-source"]["columns"][0]["visible"], True, "Right project_source visibility must remain unchanged")


def test_226(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(
        ctx,
        "test_226",
        join_config={"left_on": "id", "right_on": "id", "how": "inner"},
    )
    set_output_contract(left_id, _contract(left_id, [_col("left-id", "id", "customer_id")]))
    set_output_contract(right_id, _contract(right_id, [_col("right-id", "id", "city_id")]))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["left_on"], "id", "Ambiguous join left key without id must be preserved")
    assert_equal(config["right_on"], "id", "Ambiguous join right key without id must be preserved")


def test_227(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(
        ctx,
        "test_227",
        join_config={"left_on": "id", "left_on_column_id": "stale", "right_on": "id", "right_on_column_id": "right-id", "how": "inner"},
    )
    set_output_contract(left_id, _contract(left_id, [_col("left-id", "id", "customer_id")]))
    set_output_contract(right_id, _contract(right_id, [_col("right-id", "id", "city_id")]))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["left_on"], "id", "Stale id plus ambiguous name must be preserved")
    assert_equal(config["right_on"], "city_id", "Valid id in same join must still resolve")


def test_228(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(ctx, "test_228")
    set_output_contract(left_id, _contract(left_id, [_col("left-id-v2", "id", "customer_id")]))
    set_output_contract(right_id, _contract(right_id, [_col("right-id-v2", "id", "city_id")]))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["left_on_column_id"], "left-id-v2", "Join left companion id must follow source_name id replacement")
    assert_equal(config["right_on_column_id"], "right-id-v2", "Join right companion id must follow source_name id replacement")


def test_229(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(ctx, "test_229")
    set_output_contract(left_id, _contract(left_id, [_inactive(_col("left-id", "id", "id")), _col("left-new", "id", "customer_id")]))
    set_output_contract(right_id, _contract(right_id, [_inactive(_col("right-id", "id", "id")), _col("right-new", "id", "city_id")]))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["left_on_column_id"], "left-new", "Join left id must follow inactive replacement")
    assert_equal(config["right_on_column_id"], "right-new", "Join right id must follow inactive replacement")


def test_230(ctx: E2EContext) -> None:
    left_id, right_id, project_id = _two_source_project(
        ctx,
        "test_230",
        left_columns=[_col("left-age", "age", "age")],
        right_columns=[_col("right-score", "score", "score")],
        join_config={"left_on": "age", "left_on_column_id": "stale-left", "right_on": "score", "right_on_column_id": "stale-right", "how": "inner"},
    )
    set_output_contract(left_id, _contract(left_id, [_col("left-age", "age", "years")]))
    set_output_contract(right_id, _contract(right_id, [_col("right-score", "score", "points")]))
    config = _resync_and_config(ctx, project_id, "join-1")
    assert_equal(config["left_on_column_id"], "left-age", "Distinct stale join left id must repair by name")
    assert_equal(config["right_on_column_id"], "right-score", "Distinct stale join right id must repair by name")


def test_231(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_231",
        columns=[_col("col-age", "age", "age")],
        config={"formula": "age_score + age", "new_column": "calc"},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "age_score + years", "Formula rewrite must not alter identifiers containing the old name")


def test_232(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_232",
        columns=[_col("col-age", "age", "age")],
        config={"formula": "df['age'] * 2", "new_column": "calc"},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "df['years'] * 2", "Formula without refs must rewrite single-quoted dataframe tokens")


def test_233(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_233",
        columns=[_col("col-age", "age", "age")],
        config={"formula": 'df["age"] * 2', "new_column": "calc"},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], 'df["years"] * 2', "Formula without refs must rewrite double-quoted dataframe tokens")


def test_234(ctx: E2EContext) -> None:
    upstream_id, project_id = _formula_project(
        ctx,
        "test_234",
        columns=[_col("col-total", "total amount", "total amount")],
        config={"formula": "`total amount` * 2", "new_column": "calc"},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-total", "total amount", "total_amount")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "`total_amount` * 2", "Formula without refs must rewrite backtick tokens with spaces")


def test_235(ctx: E2EContext) -> None:
    previous = [_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")]
    synced = _sync_config(
        ctx,
        "test_235",
        config={"formula": "id * 2", "new_column": "calc"},
        previous_columns=previous,
        next_columns=previous,
        transform_op="formula",
    )
    assert_equal(synced.get("formula_column_refs"), None, "Ambiguous formula token without refs must not invent multiple refs")


def test_236(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_236",
        config={"formula": "age * 2", "formula_column_refs": [{"id": "col-age", "name": "old_age"}]},
        previous_columns=[_col("col-age", "age", "old_age")],
        next_columns=[_col("col-age", "age", "years")],
        transform_op="formula",
    )
    assert_equal(synced["formula"], "age * 2", "Formula ref rename must not rewrite a token that is not present")
    assert_equal(synced["formula_column_refs"], [{"id": "col-age", "name": "years"}], "Formula ref name must still update when expression token is absent")


def test_237(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_237",
        config={"formula": "age + 1", "formula_column_refs": [{"id": "col-age", "name": "age"}, {"name": "unknown"}]},
        previous_columns=[_col("col-age", "age", "age")],
        next_columns=[_col("col-age", "age", "years")],
        transform_op="formula",
    )
    assert_equal(synced["formula_column_refs"], [{"id": "col-age", "name": "years"}, {"name": "unknown"}], "Formula ref sync must preserve unknown nameless-id refs")


def test_238(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_238",
        config={"formula": "age + score", "formula_column_refs": [{"id": "col-age", "name": "age"}, {"id": "col-score", "name": "score"}]},
        previous_columns=[_col("col-age", "age", "age"), _col("col-score", "score", "score")],
        next_columns=[_col("col-score", "score", "points"), _col("col-age", "age", "years")],
        transform_op="formula",
    )
    assert_equal(synced["formula"], "years + points", "Formula refs must survive upstream reorder plus rename")


def test_239(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_239",
        config={"formula": "id * 2", "new_column": "calc"},
        previous_columns=[_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")],
        next_columns=[_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")],
        transform_op="formula",
    )
    assert_equal("formula_column_refs" in synced, False, "Formula without refs must not collect ambiguous duplicate current names")


def test_240(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_240",
        config={"formula": "age + 1", "new_column": "calc"},
        previous_columns=[_col("col-age", "age", "years")],
        next_columns=[_col("col-age", "age", "age")],
        transform_op="formula",
    )
    assert_equal(synced["formula"], "age + 1", "Formula alias removal should leave expression aligned to source name")
    assert_equal(synced["formula_column_refs"], [{"id": "col-age", "name": "age"}], "Formula alias removal should collect current ref")


def test_241(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_241", [_col("col-age", "age", "age", "integer")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-age"}),
        name="e2e_v2_test_241_type_sync",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "age", "float")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["source-1"]["columns"][0]["type"], "float", "Project_source type must refresh from output_contract")


def test_242(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_242",
        config={"column": "age", "column_column_id": "col-age"},
        previous_columns=[_col("col-age", "age", "age"), _col("col-score", "score", "score")],
        next_columns=[_col("col-score", "score", "score"), _col("col-age", "age", "age")],
    )
    assert_equal(synced["column"], "age", "Reorder without rename must not change scalar config")
    assert_equal(synced["column_column_id"], "col-age", "Reorder without rename must preserve scalar id")


def test_243(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_243", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-age"}),
        name="e2e_v2_test_243_added_visible",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "age"), _col("col-score", "score", "score")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["source-1"]["columns"][1]["visible"], True, "Added project_source columns must default visible")


def test_244(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_244", [_col("col-age", "age", "age"), _col("col-score", "score", "score")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-age"}),
        name="e2e_v2_test_244_source_removed",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-score", "score", "score")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal([c["name"] for c in nodes["source-1"]["columns"]], ["score"], "Removed output_contract columns must disappear from project_source")


def test_245(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_245", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-age"}),
        name="e2e_v2_test_245_source_name_change_same_id",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "years_source", "age")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["source-1"]["columns"][0]["source_name"], "years_source", "Project_source source_name must refresh even when physical name is unchanged")


def test_246(ctx: E2EContext) -> None:
    previous = [_col("col-age", "age", "years")]
    synced = _sync_config(
        ctx,
        "test_246",
        config={"columns": ["years"]},
        previous_columns=previous,
        next_columns=[_col("col-age", "age", "age")],
    )
    assert_equal(synced["columns"], ["age"], "Legacy list alias removal must rewrite display name back to source name")


def test_247(ctx: E2EContext) -> None:
    previous = [_col("col-age", "age", "years")]
    synced = _sync_config(
        ctx,
        "test_247",
        config={"aggregations": [{"column": "years", "column_id": "", "function": "sum"}]},
        previous_columns=previous,
        next_columns=[_col("col-age", "age", "age")],
    )
    assert_equal(synced["aggregations"], [{"column": "age", "column_id": "col-age", "function": "sum"}], "Legacy dict alias removal must rewrite display name back to source name")


def test_248(ctx: E2EContext) -> None:
    previous = [_col("col-age", "age", "years")]
    synced = _sync_config(
        ctx,
        "test_248",
        config={"formula": "years * 2", "new_column": "calc"},
        previous_columns=previous,
        next_columns=[_col("col-age", "age", "age")],
        transform_op="formula",
    )
    assert_equal(synced["formula"], "age * 2", "Legacy formula alias removal must rewrite display name back to source name")


def test_249(ctx: E2EContext) -> None:
    previous = [_col("col-age", "age", "years")]
    synced = _sync_config(
        ctx,
        "test_249",
        config={"formula": "years + years_extra", "new_column": "calc"},
        previous_columns=previous,
        next_columns=[_col("col-age", "age", "age")],
        transform_op="formula",
    )
    assert_equal(synced["formula"], "age + years_extra", "Legacy formula alias removal must respect word boundaries")


def test_250(ctx: E2EContext) -> None:
    previous = [_col("col-age", "age", "years"), _col("col-score", "score", "score")]
    synced = _sync_config(
        ctx,
        "test_250",
        config={"columns": ["years", "score"], "columns_column_ids": ["col-age", "col-score"]},
        previous_columns=previous,
        next_columns=[_col("col-score", "score", "points"), _col("col-age", "age", "age")],
    )
    assert_equal(synced["columns"], ["age", "points"], "List must handle alias removal plus reorder plus rename")
    assert_equal(synced["columns_column_ids"], ["col-age", "col-score"], "List ids must survive alias removal plus reorder plus rename")


TESTS: dict[str, Callable[[E2EContext], None]] = {
    f"test_{i:03d}": globals()[f"test_{i:03d}"]
    for i in range(201, 251)
}


def run_case(case_id: str) -> None:
    run_test(TESTS[case_id])
