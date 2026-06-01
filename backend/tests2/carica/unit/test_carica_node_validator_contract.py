"""Contract — CaricaNodeValidator (Epic CanvasVal 1.6b).

check_mapping (subset comune) + extra carica (max-1-geom, match-column) + lo
status #A (indeterminate ⇒ source-mancante = warning, non error).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.carica.node_validator import CaricaNodeValidator, register_carica_validator
from app.shared.execution.schema_types import InferredSchema


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _node(node_id="c1", **data):
    return SimpleNamespace(id=node_id, type="carica_arcgis", data=data)


def _upstream(names, status="certain"):
    return InferredSchema(
        columns=[{"name": n} for n in names], status=status,
    )


def _codes(issues):
    return sorted(i.code for i in issues)


def _v():
    return CaricaNodeValidator()


def test_valid_mapping_no_issues():
    node = _node(column_mappings=[
        {"source_column": "a", "dest_column": "A"},
        {"source_column": "b", "dest_column": "B"},
    ])
    assert _v().validate_node(node, upstream=_upstream(["a", "b"])) == []


def test_missing_source_is_error_when_certain():
    node = _node(column_mappings=[{"source_column": "nope", "dest_column": "X"}])
    issues = _v().validate_node(node, upstream=_upstream(["a"], status="certain"))
    assert [i.code for i in issues] == ["mapping.source_missing"]
    assert issues[0].severity == "error"
    assert issues[0].node_id == "c1"


def test_missing_source_is_warning_when_indeterminate():
    # #A: upstream non risolvibile -> la colonna potrebbe esistere a runtime -> warning
    node = _node(column_mappings=[{"source_column": "nope", "dest_column": "X"}])
    issues = _v().validate_node(node, upstream=_upstream([], status="indeterminate"))
    assert [i.code for i in issues] == ["mapping.source_missing"]
    assert issues[0].severity == "warning"


def test_duplicate_dest_is_error():
    node = _node(column_mappings=[
        {"source_column": "a", "dest_column": "X"},
        {"source_column": "b", "dest_column": "X"},
    ])
    issues = _v().validate_node(node, upstream=_upstream(["a", "b"]))
    assert "mapping.dup_dest" in _codes(issues)


def test_multi_geometry_is_error():
    node = _node(column_mappings=[
        {"source_column": "g1", "dest_column": "G1", "is_geometry": True},
        {"source_column": "g2", "dest_column": "G2", "is_geometry": True},
    ])
    issues = _v().validate_node(node, upstream=_upstream(["g1", "g2"]))
    assert "mapping.multi_geometry" in _codes(issues)


def test_match_column_not_produced_is_error():
    node = _node(
        arcgis_match_column="KEY",
        column_mappings=[{"source_column": "a", "dest_column": "A"}],
    )
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert "mapping.match_missing" in _codes(issues)


def test_match_column_produced_ok():
    node = _node(
        arcgis_match_column="A",
        column_mappings=[{"source_column": "a", "dest_column": "A"}],
    )
    issues = _v().validate_node(node, upstream=_upstream(["a"]))
    assert "mapping.match_missing" not in _codes(issues)


def test_no_active_mappings_skips_validation():
    # mapping vuoto (auto_map a runtime) -> niente da validare a PUT
    assert _v().validate_node(_node(column_mappings=[]), upstream=_upstream([])) == []
    node = _node(column_mappings=[{"source_column": "a", "dest_column": "A", "skip": True}])
    assert _v().validate_node(node, upstream=_upstream(["a"])) == []


def test_register_carica_validator_covers_all_carica_types():
    from app.shared.execution import validation_protocol as vp
    vp.clear_node_validators()
    try:
        register_carica_validator()
        for t in ("carica", "carica_arcgis", "datasource_node"):
            assert vp.validator_for(t) is not None
    finally:
        vp.clear_node_validators()
