"""Contract — ArricchisciNodeValidator (Epic CanvasVal 1.6): ARRICCHISCI_API_NOT_SELECTED."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.arricchisci.node_validator import (
    ArricchisciNodeValidator,
    register_arricchisci_validator,
)
from app.shared.execution.schema_types import InferredSchema


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _node(api_id=None, **data):
    return SimpleNamespace(id="e1", type="api_enrich", data={"api_id": api_id, **data})


def _v():
    return ArricchisciNodeValidator()


def test_api_not_selected_is_error():
    issues = _v().validate_node(_node(api_id=None), upstream=InferredSchema(columns=[]))
    assert [i.code for i in issues] == ["ARRICCHISCI_API_NOT_SELECTED"]
    assert issues[0].severity == "error"


def test_api_selected_ok():
    issues = _v().validate_node(_node(api_id="api-123"), upstream=InferredSchema(columns=[]))
    assert issues == []


def _certain(*names):
    return InferredSchema(columns=[{"name": n} for n in names], status="certain")


def _param(name, required=True, mode="mapped"):
    return {"name": name, "required": required, "mode": mode}


def test_required_param_without_mapping_is_error():
    node = _node(api_id="api-1", parameters=[_param("cap")], param_mapping={})
    issues = _v().validate_node(node, upstream=_certain("via"))
    assert [i.code for i in issues] == ["ARRICCHISCI_PARAM_MAPPING_MISSING"]
    assert issues[0].suggestion == {"params": ["cap"]}


def test_required_param_mapped_to_existing_column_ok():
    node = _node(api_id="api-1", parameters=[_param("cap")], param_mapping={"cap": "via"})
    assert _v().validate_node(node, upstream=_certain("via")) == []


def test_required_param_mapped_to_missing_column_is_error():
    node = _node(api_id="api-1", parameters=[_param("cap")], param_mapping={"cap": "ghost"})
    issues = _v().validate_node(node, upstream=_certain("via"))
    assert [i.code for i in issues] == ["ARRICCHISCI_PARAM_MAPPING_MISSING"]


def test_template_mapping_all_refs_must_exist():
    node = _node(api_id="api-1", parameters=[_param("addr")], param_mapping={"addr": "{via} {ghost}"})
    issues = _v().validate_node(node, upstream=_certain("via"))
    assert [i.code for i in issues] == ["ARRICCHISCI_PARAM_MAPPING_MISSING"]


def test_fixed_and_static_params_skipped():
    node = _node(
        api_id="api-1",
        parameters=[_param("k", mode="fixed"), _param("token"), _param("opt", required=False)],
        param_mapping={},
        static_parameters={"token": "secret"},
    )
    assert _v().validate_node(node, upstream=_certain("via")) == []


def test_indeterminate_upstream_skips_column_existence_but_flags_no_mapping():
    # mappato a colonna sconosciuta + upstream indeterminate → non flaggato (#A).
    node = _node(api_id="api-1", parameters=[_param("cap")], param_mapping={"cap": "ghost"})
    assert _v().validate_node(node, upstream=InferredSchema(columns=[], status="indeterminate")) == []
    # ma "nessun mapping" resta deterministico anche con upstream indeterminate.
    node2 = _node(api_id="api-1", parameters=[_param("cap")], param_mapping={})
    issues = _v().validate_node(node2, upstream=InferredSchema(columns=[], status="indeterminate"))
    assert [i.code for i in issues] == ["ARRICCHISCI_PARAM_MAPPING_MISSING"]


def test_register_arricchisci_validator():
    from app.shared.execution import validation_protocol as vp
    vp.clear_node_validators()
    try:
        register_arricchisci_validator()
        assert vp.validator_for("api_enrich") is not None
    finally:
        vp.clear_node_validators()
