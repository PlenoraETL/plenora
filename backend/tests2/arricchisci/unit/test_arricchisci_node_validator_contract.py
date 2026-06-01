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


def test_register_arricchisci_validator():
    from app.shared.execution import validation_protocol as vp
    vp.clear_node_validators()
    try:
        register_arricchisci_validator()
        assert vp.validator_for("api_enrich") is not None
    finally:
        vp.clear_node_validators()
