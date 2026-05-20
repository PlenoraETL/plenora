"""Second output-contract propagation block for E2E v2 host tests."""

from __future__ import annotations

from typing import Callable

from _contract_more import (
    _col,
    _contract,
    _project_with_transform,
    _resync_and_config,
    _upstream,
)
from _e2e import E2EContext, assert_equal, run_test, transform_node


def _one_col_case(ctx: E2EContext, case_id: str, *, old_name: str = "age", new_name: str = "years") -> tuple[str, dict]:
    return _upstream(ctx, case_id, [_col("col-main", old_name, old_name)])


def _sync_config_for_transform(
    ctx: E2EContext,
    case_id: str,
    *,
    config: dict,
    next_columns: list[dict],
    transform_op: str = "filter",
) -> dict:
    upstream_id, contract = _upstream(ctx, case_id, [_col("col-main", "age", "age"), _col("col-other", "score", "score")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="transform-1",
            transform_op=transform_op,
            config=config,
        ),
        name=f"e2e_v2_{case_id}_contract_more2",
    )
    set_columns = next_columns
    from _contract_more import set_output_contract

    set_output_contract(upstream_id, _contract(upstream_id, set_columns))
    return _resync_and_config(ctx, project_id, "transform-1")


def _assert_scalar_rename(ctx: E2EContext, case_id: str, key: str) -> None:
    config = {key: "age", f"{key}_column_id": "col-main"}
    synced = _sync_config_for_transform(
        ctx,
        case_id,
        config=config,
        next_columns=[_col("col-main", "age", "years"), _col("col-other", "score", "score")],
    )
    assert_equal(synced[key], "years", f"{key} must follow upstream rename")
    assert_equal(synced[f"{key}_column_id"], "col-main", f"{key} companion id must be preserved")


def _assert_scalar_removed(ctx: E2EContext, case_id: str, key: str) -> None:
    config = {key: "age", f"{key}_column_id": "col-main"}
    synced = _sync_config_for_transform(
        ctx,
        case_id,
        config=config,
        next_columns=[_col("col-other", "score", "score")],
    )
    assert_equal(synced[key], "", f"{key} must clear when upstream column is removed")
    assert_equal(synced[f"{key}_column_id"], None, f"{key} companion id must clear when removed")


def _assert_list_rename(ctx: E2EContext, case_id: str, key: str) -> None:
    config = {key: ["age", "score"], f"{key}_column_ids": ["col-main", "col-other"]}
    synced = _sync_config_for_transform(
        ctx,
        case_id,
        config=config,
        next_columns=[_col("col-main", "age", "years"), _col("col-other", "score", "points")],
    )
    assert_equal(synced[key], ["years", "points"], f"{key} list must follow upstream renames")
    assert_equal(synced[f"{key}_column_ids"], ["col-main", "col-other"], f"{key} ids must stay aligned")


def _assert_list_removed(ctx: E2EContext, case_id: str, key: str) -> None:
    config = {key: ["age", "score"], f"{key}_column_ids": ["col-main", "col-other"]}
    synced = _sync_config_for_transform(
        ctx,
        case_id,
        config=config,
        next_columns=[_col("col-other", "score", "score")],
    )
    assert_equal(synced[key], ["score"], f"{key} list must drop removed upstream column")
    assert_equal(synced[f"{key}_column_ids"], ["col-other"], f"{key} ids must drop removed upstream id")


def _assert_dict_rename(ctx: E2EContext, case_id: str, key: str, name_field: str, id_field: str) -> None:
    config = {key: [{name_field: "age", id_field: "col-main", "method": "mean", "alias": "age_mean"}]}
    synced = _sync_config_for_transform(
        ctx,
        case_id,
        config=config,
        next_columns=[_col("col-main", "age", "years"), _col("col-other", "score", "score")],
    )
    assert_equal(synced[key][0][name_field], "years", f"{key}.{name_field} must follow upstream rename")
    assert_equal(synced[key][0][id_field], "col-main", f"{key}.{id_field} must be preserved")


def _assert_dict_removed(ctx: E2EContext, case_id: str, key: str, name_field: str, id_field: str) -> None:
    config = {key: [{name_field: "age", id_field: "col-main", "method": "mean", "alias": "age_mean"}]}
    synced = _sync_config_for_transform(
        ctx,
        case_id,
        config=config,
        next_columns=[_col("col-other", "score", "score")],
    )
    assert_equal(synced[key], [], f"{key} item must be removed when referenced upstream column is removed")


def test_111(ctx: E2EContext) -> None:
    upstream_id, contract = _one_col_case(ctx, "test_111")
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="formula-1",
            transform_op="formula",
            config={"formula": "age + myage", "new_column": "calc", "formula_column_refs": [{"id": "col-main", "name": "age"}]},
        ),
        name="e2e_v2_test_111_formula_word_boundary",
    )
    from _contract_more import set_output_contract

    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-main", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "years + myage", "Formula rewrite must not touch identifiers that only contain the old name")


def test_112(ctx: E2EContext) -> None:
    upstream_id, contract = _one_col_case(ctx, "test_112")
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="formula-1",
            transform_op="formula",
            config={"formula": "df['age'] * 2", "new_column": "calc", "formula_column_refs": [{"id": "col-main", "name": "age"}]},
        ),
        name="e2e_v2_test_112_formula_single_quote",
    )
    from _contract_more import set_output_contract

    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-main", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "df['years'] * 2", "Formula df['column'] token must follow upstream rename")


def test_113(ctx: E2EContext) -> None:
    upstream_id, contract = _one_col_case(ctx, "test_113")
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="formula-1",
            transform_op="formula",
            config={"formula": 'df["age"] * 2', "new_column": "calc", "formula_column_refs": [{"id": "col-main", "name": "age"}]},
        ),
        name="e2e_v2_test_113_formula_double_quote",
    )
    from _contract_more import set_output_contract

    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-main", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], 'df["years"] * 2', 'Formula df["column"] token must follow upstream rename')


def test_114(ctx: E2EContext) -> None:
    upstream_id, contract = _one_col_case(ctx, "test_114")
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="formula-1",
            transform_op="formula",
            config={"formula": "age + score", "new_column": "calc"},
        ),
        name="e2e_v2_test_114_formula_legacy_collection",
    )
    from _contract_more import set_output_contract

    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-main", "age", "years")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula"], "years + score", "Formula without refs must rewrite only known upstream legacy tokens")
    assert_equal(config["formula_column_refs"], [{"id": "col-main", "name": "years"}], "Formula without refs must collect rewritten column refs")


def test_115(ctx: E2EContext) -> None:
    _assert_list_rename(ctx, "test_115", "compare_columns")


def test_116(ctx: E2EContext) -> None:
    _assert_list_rename(ctx, "test_116", "group_by_columns")


def test_117(ctx: E2EContext) -> None:
    _assert_list_rename(ctx, "test_117", "left_keys")


def test_118(ctx: E2EContext) -> None:
    _assert_list_rename(ctx, "test_118", "right_keys")


def test_119(ctx: E2EContext) -> None:
    _assert_list_rename(ctx, "test_119", "id_columns")


def test_120(ctx: E2EContext) -> None:
    _assert_list_rename(ctx, "test_120", "value_columns")


def test_121(ctx: E2EContext) -> None:
    _assert_list_rename(ctx, "test_121", "column_order")


def test_122(ctx: E2EContext) -> None:
    _assert_list_rename(ctx, "test_122", "join_keys")


def test_123(ctx: E2EContext) -> None:
    _assert_list_removed(ctx, "test_123", "subset")


def test_124(ctx: E2EContext) -> None:
    _assert_list_removed(ctx, "test_124", "compare_columns")


def test_125(ctx: E2EContext) -> None:
    _assert_list_removed(ctx, "test_125", "column_order")


def test_126(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_126", "lat_column")


def test_127(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_127", "lon_column")


def test_128(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_128", "source_column")


def test_129(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_129", "target_column")


def test_130(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_130", "geom_column")


def test_131(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_131", "geometry_column")


def test_132(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_132", "partition_column")


def test_133(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_133", "stratify_column")


def test_134(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_134", "aggregate_column")


def test_135(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_135", "by_column")


def test_136(ctx: E2EContext) -> None:
    _assert_scalar_rename(ctx, "test_136", "id_column")


def test_137(ctx: E2EContext) -> None:
    _assert_scalar_removed(ctx, "test_137", "point_geom_column")


def test_138(ctx: E2EContext) -> None:
    _assert_scalar_removed(ctx, "test_138", "polygon_geom_column")


def test_139(ctx: E2EContext) -> None:
    _assert_scalar_removed(ctx, "test_139", "wkt_column")


def test_140(ctx: E2EContext) -> None:
    _assert_dict_rename(ctx, "test_140", "aggs", "column", "column_id")


def test_141(ctx: E2EContext) -> None:
    _assert_dict_removed(ctx, "test_141", "aggs", "column", "column_id")


def test_142(ctx: E2EContext) -> None:
    _assert_dict_rename(ctx, "test_142", "maskings", "column", "column_id")


def test_143(ctx: E2EContext) -> None:
    _assert_dict_removed(ctx, "test_143", "maskings", "column", "column_id")


def test_144(ctx: E2EContext) -> None:
    _assert_dict_rename(ctx, "test_144", "aggregations", "column", "column_id")


def test_145(ctx: E2EContext) -> None:
    _assert_dict_removed(ctx, "test_145", "aggregations", "column", "column_id")


def test_146(ctx: E2EContext) -> None:
    _assert_dict_rename(ctx, "test_146", "renames", "old_name", "old_name_id")


def test_147(ctx: E2EContext) -> None:
    _assert_dict_removed(ctx, "test_147", "renames", "old_name", "old_name_id")


def test_148(ctx: E2EContext) -> None:
    upstream_id, contract = _one_col_case(ctx, "test_148")
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="formula-1",
            transform_op="formula",
            config={"formula": "age * 2", "new_column": "calc", "formula_column_refs": [{"id": "col-main", "name": "age"}]},
        ),
        name="e2e_v2_test_148_formula_id_migration",
    )
    from _contract_more import set_output_contract

    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-main-v2", "age", "age")]))
    config = _resync_and_config(ctx, project_id, "formula-1")
    assert_equal(config["formula_column_refs"], [{"id": "col-main-v2", "name": "age"}], "Formula refs must migrate ids by stable source_name")


def test_149(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_149", [_col("col-main", "age", "age"), _col("col-other", "age_backup", "age")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="filter-1",
            transform_op="filter",
            config={"column": "age", "column_column_id": "col-main", "operator": ">", "value": "18"},
        ),
        name="e2e_v2_test_149_duplicate_current_names_with_id",
    )
    from _contract_more import set_output_contract

    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-main", "age", "years"), _col("col-other", "age_backup", "years")]))
    config = _resync_and_config(ctx, project_id, "filter-1")
    assert_equal(config["column"], "years", "Explicit column id must resolve even when current names are duplicated")
    assert_equal(config["column_column_id"], "col-main", "Explicit column id must remain attached to the selected column")


def test_150(ctx: E2EContext) -> None:
    upstream_id, contract = _upstream(ctx, "test_150", [_col("col-main", "age", "age"), _col("col-other", "score", "score")])
    project_id = _project_with_transform(
        ctx,
        upstream_id=upstream_id,
        contract=contract,
        transform=transform_node(
            node_id="filter-1",
            transform_op="filter",
            config={"column": "age", "operator": ">", "value": "18"},
        ),
        name="e2e_v2_test_150_legacy_no_id_rename",
    )
    from _contract_more import set_output_contract

    set_output_contract(upstream_id, _contract(upstream_id, [_col("col-main", "age", "years"), _col("col-other", "score", "score")]))
    config = _resync_and_config(ctx, project_id, "filter-1")
    assert_equal(config["column"], "years", "Legacy configs without companion id must still follow unambiguous source rename")


TESTS: dict[str, Callable[[E2EContext], None]] = {
    f"test_{i:03d}": globals()[f"test_{i:03d}"]
    for i in range(111, 151)
}


def run_case(case_id: str) -> None:
    run_test(TESTS[case_id])
