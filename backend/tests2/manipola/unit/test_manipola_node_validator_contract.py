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


def _node(node_id="t1", config=None, transform_op="distinct"):
    # transform_op di default valorizzato e SENZA param required (distinct) → i test
    # col-ref/formula non scattano né TRANSFORM_OP_MISSING né TRANSFORM_PARAM_REQUIRED.
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


def test_missing_reference_tolerated_by_runtime_is_error():
    # A3 (correzione dopo la conferma): il runtime ignora in silenzio il
    # riferimento assente; Valida resta più severa, come HEAD: errore.
    node = _node(config={"column": "nope"})
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert [i.code for i in issues] == ["column.missing"]
    assert issues[0].severity == "error"
    assert issues[0].node_id == "t1"


def test_filter_on_missing_column_is_runtime_error():
    node = _node(config={"column": "nope"}, transform_op="filter")
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert [(i.code, i.severity) for i in issues] == [("preparation.invalid", "error")]
    assert issues[0].message == "Filter: colonna 'nope' non trovata. Colonne disponibili: ['a']"


def test_join_keys_list_checked():
    node = _node(config={"left_on": "a", "right_on": "missing"})
    issues = _v().validate_node(node, upstream=_upstream(["a", "b"]))
    assert "column.missing" in _codes(issues)
    assert issues[0].suggestion == {"column": "missing"}


def test_join_legacy_left_on_right_on_satisfy_new_required_keys():
    node = _node(config={"left_on": "a", "right_on": "b"}, transform_op="join")
    issues = _v().validate_node(node, upstream=_upstream(["a", "b"]))
    assert "TRANSFORM_PARAM_REQUIRED" not in _codes(issues)


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
    node = _node(config={"new_column": "z", "formula": "__import__('os')"}, transform_op="formula")
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert [(i.code, i.severity) for i in issues] == [("preparation.invalid", "error")]


def test_formula_field_on_other_operation_is_ignored_like_runtime():
    # A3: distinct non legge "formula" (parametro sconosciuto, mai valutato).
    node = _node(config={"formula": "__import__('os')"})
    assert _v().validate_node(node, upstream=_upstream(["a"])) == []


def test_valid_formula_no_issue_and_refs_not_checked():
    # formula valida; i suoi ref (a, to_numeric) NON sono validati come colonne
    node = _node(config={"formula": "to_numeric(a) + 1"})
    assert _v().validate_node(node, upstream=_upstream(["a"])) == []


def test_transform_op_missing_is_error():
    node = _node(config={"column": "a"}, transform_op=None)
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert "TRANSFORM_OP_MISSING" in _codes(issues)


def test_transform_op_unknown_is_error():
    # op orfana: nel Literal ma assente dal registry eseguibile → #B false-OK chiuso
    node = _node(config={"column": "a"}, transform_op="merge")
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert "TRANSFORM_OP_UNKNOWN" in _codes(issues)


def test_known_op_not_flagged_unknown():
    node = _node(config={"column": "a"}, transform_op="filter")
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert "TRANSFORM_OP_UNKNOWN" not in _codes(issues)


def test_required_param_missing_is_error():
    # Residui collaudo live L4b: il campo obbligatorio vuoto si dice col suo
    # nome, non con il rifiuto tecnico che ne consegue («colonna 'None'»).
    node = _node(config={}, transform_op="filter")
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert [(i.code, i.severity, i.message) for i in issues] == [
        ("TRANSFORM_PARAM_REQUIRED", "error", "Compila «Colonna»: il campo è obbligatorio.")]


def test_required_param_present_not_flagged():
    node = _node(config={"column": "a"}, transform_op="filter")
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert "TRANSFORM_PARAM_REQUIRED" not in _codes(issues)


def test_geometry_column_param_without_geometry_is_runtime_error():
    # Senza geometria a monte l'esecuzione a vuoto rifiuta come il runtime.
    node = _node(config={"geom_column": "plain", "distance": 10}, transform_op="geo_buffer")
    up = InferredSchema(columns=[{"name": "plain", "type": "str"}], status="certain")
    issues = _v().validate_node(node, upstream=up)
    assert [(i.code, i.severity) for i in issues] == [("preparation.invalid", "error")]
    assert issues[0].message == "Nessuna geometria valida nella colonna 'plain'"


def test_geometry_column_param_on_non_geometry_is_error():
    # Con un'altra geometria attiva il runtime non rifiuta: resta la regola di schema.
    node = _node(config={"geom_column": "plain", "distance": 10}, transform_op="geo_buffer")
    up = InferredSchema(columns=[{"name": "plain", "type": "str"}, {"name": "geometry", "type": "geometry:point:3857"}], status="certain")
    issues = _v().validate_node(node, upstream=up)
    assert _codes(issues) == ["TRANSFORM_GEOMETRY_TYPE_INVALID"]
    geo = issues[0]
    assert geo.severity == "error"
    assert geo.suggestion == {"param": "geom_column", "column": "plain"}


def test_geometry_column_param_on_geometry_ok():
    node = _node(config={"geom_column": "geom", "distance": 10}, transform_op="geo_buffer")
    up = InferredSchema(columns=[{"name": "geom", "type": "geometry:point:4326"}], status="certain")
    issues = _v().validate_node(node, upstream=up)
    assert "TRANSFORM_GEOMETRY_TYPE_INVALID" not in _codes(issues)


def test_geometry_type_invalid_is_warning_when_indeterminate():
    # #A: upstream indeterminate → mai falso blocco (warning, non error).
    node = _node(config={"geom_column": "plain", "distance": 10}, transform_op="geo_buffer")
    up = InferredSchema(columns=[{"name": "plain", "type": "str"}, {"name": "geometry", "type": "geometry"}], status="indeterminate")
    issues = _v().validate_node(node, upstream=up)
    geo = [i for i in issues if i.code == "TRANSFORM_GEOMETRY_TYPE_INVALID"]
    assert geo and geo[0].severity == "warning"
    assert not any(i.severity == "error" for i in issues)


def test_register_manipola_validator():
    from app.shared.execution import validation_protocol as vp
    vp.clear_node_validators()
    try:
        register_manipola_validator()
        for t in ("transform", "multi_join"):
            assert vp.validator_for(t) is not None
    finally:
        vp.clear_node_validators()
