"""Contract — check_mapping regola pura condivisa (Epic CanvasVal 0.4)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.shared.execution.mapping_rules import check_mapping


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _m(source, dest, *, skip=False, is_geometry=False):
    return SimpleNamespace(
        source_column=source, dest_column=dest, skip=skip, is_geometry=is_geometry,
    )


def test_valid_mapping_has_no_issues():
    issues = check_mapping({"a", "b"}, [_m("a", "A"), _m("b", "B")])
    assert issues == []


def test_missing_source_issue():
    issues = check_mapping({"a"}, [_m("nope", "X")])
    assert [i.code for i in issues] == ["mapping.source_missing"]
    assert issues[0].severity == "error"
    assert issues[0].suggestion == {"columns": ["nope"]}


def test_duplicate_source_issue():
    issues = check_mapping({"a"}, [_m("a", "A1"), _m("a", "A2")])
    assert [i.code for i in issues] == ["mapping.dup_source"]
    assert issues[0].suggestion == {"columns": ["a"]}


def test_duplicate_dest_issue():
    issues = check_mapping({"a", "b"}, [_m("a", "X"), _m("b", "X")])
    assert [i.code for i in issues] == ["mapping.dup_dest"]


def test_issue_order_dup_source_then_dest_then_missing():
    # mapping con tutti e tre i problemi → ordine deterministico
    issues = check_mapping(
        {"a"},
        [_m("a", "X"), _m("a", "X"), _m("missing", "Y")],
    )
    assert [i.code for i in issues] == [
        "mapping.dup_source", "mapping.dup_dest", "mapping.source_missing",
    ]


def test_skip_and_incomplete_mappings_excluded():
    issues = check_mapping(
        {"a"},
        [_m("nope", "X", skip=True), _m("", "Y"), _m("a", "")],
    )
    assert issues == []  # nessun mapping attivo → niente da validare


def test_node_id_propagated():
    issues = check_mapping({"a"}, [_m("nope", "X")], node_id="carica-1")
    assert issues[0].node_id == "carica-1"


def test_duck_typed_over_real_mapping_models():
    # Funziona sui modelli reali di entrambi i domini (no tipo nuovo).
    from app.core.carica.models import ColumnMapping
    from app.core.connections.arcgis_write_mapping import ArcGISColumnMapping

    cm = ColumnMapping(source_column="nope", dest_column="X", dest_type="text")
    am = ArcGISColumnMapping(source_column="nope2", dest_column="Y")
    issues = check_mapping({"a"}, [cm, am])
    assert [i.code for i in issues] == ["mapping.source_missing"]
    assert set(issues[0].suggestion["columns"]) == {"nope", "nope2"}
