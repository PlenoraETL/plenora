"""Third output-contract propagation block for E2E v2 host tests."""

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
from _e2e import (
    E2EContext,
    assert_equal,
    edge,
    result_node,
    run_test,
    set_output_contract,
    transform_node,
)


def _inactive(column: dict) -> dict:
    out = dict(column)
    out["status"] = "inactive"
    return out


def _sync_config(
    ctx: E2EContext,
    case_id: str,
    *,
    config: dict,
    next_columns: list[dict],
    previous_columns: list[dict] | None = None,
    transform_op: str = "filter",
) -> dict:
    upstream_id, contract = _upstream(
        ctx,
        case_id,
        previous_columns or [
            _col("col-age", "age", "age"),
            _col("col-score", "score", "score"),
            _col("col-status", "status", "status"),
        ],
    )
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="transform-1",
            transform_op=transform_op,
            config=config,
        ),
        name=f"e2e_v2_{case_id}_contract_more3",
    )
    set_output_contract(upstream_id, _contract(upstream_id, next_columns))
    return _resync_and_config(ctx, project_id, "transform-1")


def _assert_scalar_rename(ctx: E2EContext, case_id: str, key: str) -> None:
    config = {key: "age", f"{key}_column_id": "col-age"}
    synced = _sync_config(
        ctx,
        case_id,
        config=config,
        next_columns=[
            _col("col-age", "age", "years"),
            _col("col-score", "score", "score"),
            _col("col-status", "status", "status"),
        ],
    )
    assert_equal(synced[key], "years", f"{key} must follow upstream rename")
    assert_equal(synced[f"{key}_column_id"], "col-age", f"{key} companion id must stay stable")


def _assert_scalar_removed(ctx: E2EContext, case_id: str, key: str) -> None:
    config = {key: "age", f"{key}_column_id": "col-age"}
    synced = _sync_config(
        ctx,
        case_id,
        config=config,
        next_columns=[
            _col("col-score", "score", "score"),
            _col("col-status", "status", "status"),
        ],
    )
    assert_equal(synced[key], "", f"{key} must clear when upstream column is removed")
    assert_equal(synced[f"{key}_column_id"], None, f"{key} companion id must clear when upstream column is removed")


def _chain_project(ctx: E2EContext, case_id: str, *, formula_config: dict, downstream_config: dict) -> tuple[str, str]:
    upstream_id, contract = _upstream(
        ctx,
        case_id,
        [_col("col-age", "age", "age"), _col("col-score", "score", "score")],
    )
    source = __import__("_contract_more")._source(upstream_id, contract)
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config=formula_config,
        input_columns=None,
    )
    drop = transform_node(
        node_id="drop-1",
        transform_op="drop_columns",
        config=downstream_config,
    )
    project_id = ctx.create_project(
        name=f"e2e_v2_{case_id}_chain",
        project_type="manipola",
        canvas={
            "nodes": [source, formula, drop, result_node()],
            "edges": [
                edge("e1", "source-1", "formula-1"),
                edge("e2", "formula-1", "drop-1"),
                edge("e3", "drop-1", "result-1"),
            ],
        },
    )
    return upstream_id, project_id


def test_151(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_151", "index_col")


def test_152(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_152", "pivot_col")


def test_153(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_153", "value_col")


def test_154(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_154", "group_column")


def test_155(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_155", "input_geom")


def test_156(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_156", "left_geom")


def test_157(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_157", "right_geom")


def test_158(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_158", "mask_geom")


def test_159(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_159", "left_geom_column")


def test_160(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_160", "right_geom_column")


def test_161(ctx: E2EContext) -> None:
    _assert_scalar_removed(ctx, "test_161", "index_col")


def test_162(ctx: E2EContext) -> None:
    _assert_scalar_removed(ctx, "test_162", "pivot_col")


def test_163(ctx: E2EContext) -> None:
    _assert_scalar_removed(ctx, "test_163", "value_col")


def test_164(ctx: E2EContext) -> None:
    _assert_scalar_removed(ctx, "test_164", "group_column")


def test_165(ctx: E2EContext) -> None:
    _assert_scalar_removed(ctx, "test_165", "input_geom")


def test_166(ctx: E2EContext) -> None:
    _assert_scalar_removed(ctx, "test_166", "left_geom")


def test_167(ctx: E2EContext) -> None:
    _assert_scalar_removed(ctx, "test_167", "right_geom")


def test_168(ctx: E2EContext) -> None:
    _assert_scalar_removed(ctx, "test_168", "mask_geom")


def test_169(ctx: E2EContext) -> None:
    _assert_scalar_removed(ctx, "test_169", "left_geom_column")


def test_170(ctx: E2EContext) -> None:
    _assert_scalar_removed(ctx, "test_170", "right_geom_column")


def test_171(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_171",
        config={"columns": ["age", "score", "status"], "columns_column_ids": ["col-age", "col-score", "col-status"]},
        next_columns=[_col("col-age", "age", "age"), _col("col-status", "status", "status")],
    )
    assert_equal(synced["columns"], ["age", "status"], "List removal must preserve columns after a removed middle item")
    assert_equal(synced["columns_column_ids"], ["col-age", "col-status"], "List ids must preserve columns after a removed middle item")


def test_172(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_172",
        config={"columns": ["age", "score"], "columns_column_ids": ["col-age", "col-score"]},
        next_columns=[_col("col-age", "age", "age")],
    )
    assert_equal(synced["columns"], ["age"], "List removal must preserve the first item when the last item is removed")
    assert_equal(synced["columns_column_ids"], ["col-age"], "List ids must preserve the first item when the last item is removed")


def test_173(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_173",
        config={"columns": ["age", "score"], "columns_column_ids": ["col-age", "col-score"]},
        next_columns=[_col("col-status", "status", "status")],
    )
    assert_equal(synced["columns"], [], "List must clear when every referenced column is removed")
    assert_equal(synced["columns_column_ids"], [], "List ids must clear when every referenced column is removed")


def test_174(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_174",
        config={"columns": ["age", "score"]},
        next_columns=[_col("col-age", "age", "years"), _col("col-score", "score", "points")],
    )
    assert_equal(synced["columns"], ["years", "points"], "Legacy list without ids must follow unambiguous renames")
    assert_equal("columns_column_ids" in synced, False, "Legacy list without ids must not invent companion key")


def test_175(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_175",
        config={"columns": ["age", "score"]},
        next_columns=[_col("col-age", "age", "years")],
    )
    assert_equal(synced["columns"], ["years"], "Legacy list without ids must drop removed item and rename remaining item")


def test_176(ctx: E2EContext) -> None:
    marker = {"literal": "not-a-column"}
    synced = _sync_config(
        ctx,
        "test_176",
        config={"columns": ["age", marker, "score"], "columns_column_ids": ["col-age", None, "col-score"]},
        next_columns=[_col("col-age", "age", "years"), _col("col-score", "score", "points")],
    )
    assert_equal(synced["columns"], ["years", marker, "points"], "List sync must preserve non-string items")
    assert_equal(synced["columns_column_ids"], ["col-age", None, "col-score"], "List sync must keep companion ids aligned with non-string items")


def test_177(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_177",
        config={"columns": ["age", "score"], "columns_column_ids": ["col-age"]},
        next_columns=[_col("col-age", "age", "years"), _col("col-score", "score", "points")],
    )
    assert_equal(synced["columns"], ["years", "points"], "List sync must recover ids when companion id list is short")
    assert_equal(synced["columns_column_ids"], ["col-age", "col-score"], "List sync must repair short companion id list")


def test_178(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_178",
        config={"subset": ["age", "status"], "subset_column_ids": ["col-age", "col-status"]},
        next_columns=[_col("col-age", "age", "years"), _col("col-status", "status", "state")],
    )
    assert_equal(synced["subset"], ["years", "state"], "subset list must follow multiple renames")
    assert_equal(synced["subset_column_ids"], ["col-age", "col-status"], "subset ids must remain aligned")


def test_179(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_179",
        config={
            "aggs": [
                {"column": "age", "column_id": "col-age", "method": "mean"},
                {"column": "score", "column_id": "col-score", "method": "max"},
            ]
        },
        next_columns=[_col("col-score", "score", "score")],
    )
    assert_equal(synced["aggs"], [{"column": "score", "column_id": "col-score", "method": "max"}], "Dict list must remove only the item that references a removed column")


def test_180(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_180",
        config={"maskings": ["literal", {"column": "age", "column_id": "col-age", "method": "hash"}]},
        next_columns=[_col("col-score", "score", "score")],
    )
    assert_equal(synced["maskings"], ["literal"], "Dict list sync must preserve non-dict items while dropping removed column items")


def test_181(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_181",
        config={"aggregations": [{"column": "age", "column_id": "", "function": "sum"}]},
        next_columns=[_col("col-age", "age", "years")],
    )
    assert_equal(synced["aggregations"], [{"column": "years", "column_id": "col-age", "function": "sum"}], "Dict item without id must be enriched by unambiguous legacy rename")


def test_182(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_182",
        config={"renames": [{"old_name": "age", "old_name_id": "", "new_name": "age2"}]},
        next_columns=[_col("col-score", "score", "score")],
    )
    assert_equal(synced["renames"], [], "Dict item without id must drop when its legacy name is removed")


def test_183(ctx: E2EContext) -> None:
    synced = _sync_config(
        ctx,
        "test_183",
        config={"renames": [{"old_name": "age", "old_name_id": "col-age", "new_name": "age2"}, {"old_name": "score", "old_name_id": "col-score", "new_name": "score2"}]},
        next_columns=[_col("col-score", "score", "points")],
    )
    assert_equal(synced["renames"], [{"old_name": "points", "old_name_id": "col-score", "new_name": "score2"}], "Rename dict list must remove deleted item and rewrite preserved item")


def test_184(ctx: E2EContext) -> None:
    original = {"column": "unknown", "column_id": "", "function": "sum"}
    synced = _sync_config(
        ctx,
        "test_184",
        config={"aggregations": [original]},
        next_columns=[_col("col-age", "age", "years")],
    )
    assert_equal(synced["aggregations"], [original], "Unknown dict references must be preserved rather than guessed")


def test_185(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_185", [_col("col-age", "age", "age"), _col("col-score", "score", "score")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="formula-1",
            transform_op="formula",
            config={"formula": "age + score", "formula_column_refs": [{"id": "col-age", "name": "age"}, {"id": "col-score", "name": "score"}]},
        ),
        name="e2e_v2_test_185_formula_two_ref_rename",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years"), _col("col-score", "score", "points")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "years + points", "Formula must rewrite multiple refs in one pass")
    assert_equal(config["formula_column_refs"], [{"id": "col-age", "name": "years"}, {"id": "col-score", "name": "points"}], "Formula refs must rewrite multiple refs in one pass")


def test_186(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_186", [_col("col-age", "age", "age"), _col("col-score", "score", "score")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="formula-1",
            transform_op="formula",
            config={"formula": "age + score", "formula_column_refs": [{"id": "col-age", "name": "age"}, {"id": "col-score", "name": "score"}]},
        ),
        name="e2e_v2_test_186_formula_removed_ref",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "age")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "", "Formula must clear when one of its referenced upstream columns is removed")
    assert_equal(config["formula_column_refs"], [], "Formula refs must clear when one referenced upstream column is removed")


def test_187(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_187", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="formula-1",
            transform_op="formula",
            config={"formula": "age * 2", "formula_column_refs": [{"id": "col-age", "name": "age"}]},
        ),
        name="e2e_v2_test_187_formula_all_refs_removed",
    )
    set_output_contract(upstream_id, _contract(upstream_id, []))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "", "Formula must clear when every referenced upstream column is removed")
    assert_equal(config["formula_column_refs"], [], "Formula refs must clear when every referenced upstream column is removed")


def test_188(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_188", [_col("col-total", "total amount", "total amount")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="formula-1",
            transform_op="formula",
            config={"formula": "`total amount` * 2", "formula_column_refs": [{"id": "col-total", "name": "total amount"}]},
        ),
        name="e2e_v2_test_188_formula_backtick_space",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-total", "total amount", "total_amount")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "`total_amount` * 2", "Backtick formula refs with spaces must follow rename")


def test_189(ctx: E2EContext) -> None:
    upstream_id, project_id = _chain_project(
        ctx,
        "test_189",
        formula_config={"formula": "age * 2", "new_column": "double_age", "formula_column_refs": [{"id": "col-age", "name": "age"}]},
        downstream_config={"columns": ["age"], "columns_column_ids": ["col-age"]},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years"), _col("col-score", "score", "score")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["formula-1"]["config"]["formula"], "years * 2", "Formula rename must propagate through chain")
    assert_equal(nodes["drop-1"]["config"]["columns"], ["years"], "Downstream list must see renamed column through upstream transform")


def test_190(ctx: E2EContext) -> None:
    upstream_id, project_id = _chain_project(
        ctx,
        "test_190",
        formula_config={"formula": "age * 2", "new_column": "double_age", "formula_column_refs": [{"id": "col-age", "name": "age"}]},
        downstream_config={"columns": ["score"], "columns_column_ids": ["col-score"]},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "age")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["drop-1"]["config"]["columns"], [], "Removed columns must propagate through a transform chain")


def test_191(ctx: E2EContext) -> None:
    upstream_id, project_id = _chain_project(
        ctx,
        "test_191",
        formula_config={"formula": "age * 2", "new_column": "double_age", "formula_column_refs": [{"id": "col-age", "name": "age"}]},
        downstream_config={"columns": ["double_age"], "columns_column_ids": []},
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "years"), _col("col-score", "score", "score")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["drop-1"]["config"]["columns"], ["double_age"], "Calculated columns must remain stable while upstream inputs rename")


def test_192(ctx: E2EContext) -> None:
    previous = [_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")]
    synced = _sync_config(
        ctx,
        "test_192",
        config={"column": "id", "operator": "=", "value": "1"},
        previous_columns=previous,
        next_columns=[_col("col-left", "left_id", "left_id"), _col("col-right", "right_id", "right_id")],
    )
    assert_equal(synced["column"], "id", "Ambiguous legacy scalar name without id must be preserved rather than cleared")


def test_193(ctx: E2EContext) -> None:
    previous = [_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")]
    synced = _sync_config(
        ctx,
        "test_193",
        config={"columns": ["id"]},
        previous_columns=previous,
        next_columns=[_col("col-left", "left_id", "left_id"), _col("col-right", "right_id", "right_id")],
    )
    assert_equal(synced["columns"], ["id"], "Ambiguous legacy list item without id must be preserved rather than dropped")


def test_194(ctx: E2EContext) -> None:
    previous = [_col("col-left", "left_id", "id"), _col("col-right", "right_id", "id")]
    synced = _sync_config(
        ctx,
        "test_194",
        config={"column": "id", "column_column_id": "col-left", "operator": "=", "value": "1"},
        previous_columns=previous,
        next_columns=[_col("col-left", "left_id", "left_id"), _col("col-right", "right_id", "right_id")],
    )
    assert_equal(synced["column"], "left_id", "Explicit id must resolve ambiguous legacy scalar names")
    assert_equal(synced["column_column_id"], "col-left", "Explicit id must stay attached after ambiguity resolution")


def test_195(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_195", [_col("col-old", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-old"}),
        name="e2e_v2_test_195_inactive_replacement",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_inactive(_col("col-old", "age", "age")), _col("col-new", "age", "years")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["source-1"]["columns"][0]["id"], "col-new", "Inactive old column plus active replacement must refresh project_source id")
    assert_equal(nodes["transform-1"]["config"]["column"], "years", "Inactive old column plus active replacement must update transform config")
    assert_equal(nodes["transform-1"]["config"]["column_column_id"], "col-new", "Inactive old column plus active replacement must update transform companion id")


def test_196(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_196", [_col("col-age", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-age"}),
        name="e2e_v2_test_196_inactive_added_ignored",
    )
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-age", "age", "age"), _inactive(_col("col-hidden", "hidden", "hidden"))]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal([col["name"] for col in nodes["source-1"]["columns"]], ["age"], "Inactive added output_contract columns must not enter project_source")


def test_197(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_197", [_col("col-old", "age", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(node_id="transform-1", transform_op="filter", config={"column": "age", "column_column_id": "col-old"}),
        name="e2e_v2_test_197_visibility_source_fallback",
    )
    nodes = _resync_and_nodes(ctx, project_id)
    nodes["source-1"]["columns"][0]["visible"] = False
    nodes["source-1"]["data"]["columns"][0]["visible"] = False
    canvas = ctx.get_project(project_id)["canvas"]
    ctx.update_canvas(project_id, {"nodes": list(nodes.values()), "edges": canvas["edges"]})
    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-new", "age", "years")]))
    nodes = _resync_and_nodes(ctx, project_id)
    assert_equal(nodes["source-1"]["columns"][0]["visible"], False, "Visibility must survive id replacement resolved by source_name")
    assert_equal(nodes["transform-1"]["config"]["column_column_id"], "col-new", "Transform id must follow source_name replacement")


def test_198(ctx: E2EContext) -> None:
    previous = [_col("col-age", "age", "years")]
    synced = _sync_config(
        ctx,
        "test_198",
        config={"column": "years", "operator": ">", "value": "18"},
        previous_columns=previous,
        next_columns=[_col("col-age", "age", "age")],
    )
    assert_equal(synced["column"], "age", "Alias removal must rewrite legacy display/physical name back to source name")


def test_199(ctx: E2EContext) -> None:
    previous = [_col("col-age", "age", "age")]
    synced = _sync_config(
        ctx,
        "test_199",
        config={"column": "age", "column_column_id": "col-age"},
        previous_columns=previous,
        next_columns=[_col("col-age-v2", "age", "years")],
    )
    assert_equal(synced["column"], "years", "Scalar config must follow id replacement resolved by source_name")
    assert_equal(synced["column_column_id"], "col-age-v2", "Scalar companion id must follow id replacement resolved by source_name")


def test_200(ctx: E2EContext) -> None:
    previous = [_col("col-age", "age", "age"), _col("col-score", "score", "score")]
    synced = _sync_config(
        ctx,
        "test_200",
        config={"columns": ["age", "score"], "columns_column_ids": ["col-age", "col-score"]},
        previous_columns=previous,
        next_columns=[_col("col-age-v2", "age", "years"), _col("col-score-v2", "score", "points")],
    )
    assert_equal(synced["columns"], ["years", "points"], "List config must follow id replacements resolved by source_name")
    assert_equal(synced["columns_column_ids"], ["col-age-v2", "col-score-v2"], "List companion ids must follow id replacements resolved by source_name")


TESTS: dict[str, Callable[[E2EContext], None]] = {
    f"test_{i:03d}": globals()[f"test_{i:03d}"]
    for i in range(151, 201)
}


def run_case(case_id: str) -> None:
    run_test(TESTS[case_id])
