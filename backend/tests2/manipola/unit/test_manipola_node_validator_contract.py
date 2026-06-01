"""Contract — ManipolaNodeValidator (Epic CanvasVal 1.6c).

Ref-colonna del config risolvono contro upstream (deliverable #1) + safety formula
+ status #A (indeterminate ⇒ warning). Ref dentro la formula NON validati (skip).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.manipola.node_validator import (
    ManipolaNodeValidator,
    register_manipola_validator,
)
from app.shared.execution.schema_types import InferredSchema


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _node(node_id="t1", config=None, transform_op="filter"):
    # transform_op di default valorizzato → i test col-ref non scattano TRANSFORM_OP_MISSING
    return SimpleNamespace(
        id=node_id, type="transform", data={}, config=config or {}, transform_op=transform_op,
    )


def _upstream(names, status="certain", ids=None):
    cols = []
    for i, n in enumerate(names):
        col = {"name": n}
        if ids:
            col["id"] = ids[i]
        cols.append(col)
    return InferredSchema(columns=cols, status=status)


def _codes(issues):
    return sorted(i.code for i in issues)


def _v():
    return ManipolaNodeValidator()


def test_filter_on_existing_column_ok():
    node = _node(config={"column": "a"})
    assert _v().validate_node(node, upstream=_upstream(["a", "b"])) == []


def test_filter_on_missing_column_is_error():
    node = _node(config={"column": "nope"})
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert [i.code for i in issues] == ["column.missing"]
    assert issues[0].severity == "error"
    assert issues[0].node_id == "t1"


def test_join_keys_list_checked():
    node = _node(config={"left_on": "a", "right_on": "missing"})
    issues = _v().validate_node(node, upstream=_upstream(["a", "b"]))
    assert "column.missing" in _codes(issues)
    assert issues[0].suggestion == {"column": "missing"}


def test_list_key_group_by_checked():
    node = _node(config={"group_by": ["a", "ghost"]})
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert [i.suggestion["column"] for i in issues] == ["ghost"]


def test_missing_is_warning_when_upstream_indeterminate():
    # #A: upstream non risolvibile -> warning, non error
    node = _node(config={"column": "nope"})
    issues = _v().validate_node(node, upstream=_upstream([], status="indeterminate"))
    assert [i.code for i in issues] == ["column.missing"]
    assert issues[0].severity == "warning"


def test_by_id_ref_resolves_no_false_positive():
    # config by-id: left_on stale ("vecchio") ma left_on_column_id punta a id "x1"
    # che risolve a "nuovo" (in upstream) -> NESSUN errore (no falso positivo).
    node = _node(config={"left_on": "vecchio", "left_on_column_id": "x1"})
    up = _upstream(["nuovo", "b"], ids=["x1", "x2"])
    assert _v().validate_node(node, upstream=up) == []


def test_output_column_key_not_flagged():
    # 'output_column' (nome nuovo) NON è una key-ref di input -> non flaggato
    node = _node(config={"column": "a", "output_column": "brand_new"})
    assert _v().validate_node(node, upstream=_upstream(["a"])) == []


def test_invalid_formula_is_error():
    node = _node(config={"formula": "__import__('os')"})
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert "formula.invalid" in _codes(issues)


def test_valid_formula_no_issue_and_refs_not_checked():
    # formula valida; i suoi ref (a, to_numeric) NON sono validati come colonne
    node = _node(config={"formula": "to_numeric(a) + 1"})
    assert _v().validate_node(node, upstream=_upstream(["a"])) == []


def test_transform_op_missing_is_error():
    node = _node(config={"column": "a"}, transform_op=None)
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert "TRANSFORM_OP_MISSING" in _codes(issues)


def test_register_manipola_validator():
    from app.shared.execution import validation_protocol as vp
    vp.clear_node_validators()
    try:
        register_manipola_validator()
        for t in ("transform", "multi_join"):
            assert vp.validator_for(t) is not None
    finally:
        vp.clear_node_validators()
