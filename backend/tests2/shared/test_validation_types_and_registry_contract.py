"""Contract — Issue/ValidationReport + registry validate_node (Epic CanvasVal 0.2/0.3)."""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# --- 0.2 Issue / ValidationReport ---


def test_issue_to_dict_omits_none_suggestion():
    from app.shared.execution.validation_types import Issue

    i = Issue(node_id="n1", severity="error", code="column.missing", message="boom")
    d = i.to_dict()
    assert d == {
        "node_id": "n1",
        "severity": "error",
        "code": "column.missing",
        "message": "boom",
    }
    assert "suggestion" not in d


def test_issue_to_dict_includes_suggestion_when_present():
    from app.shared.execution.validation_types import Issue

    i = Issue(
        node_id="n1", severity="info", code="rename", message="m",
        suggestion={"rename": {"old": "new"}},
    )
    assert i.to_dict()["suggestion"] == {"rename": {"old": "new"}}


def test_validation_report_has_errors_and_filters():
    from app.shared.execution.validation_types import Issue, ValidationReport

    err = Issue("n1", "error", "c1", "e")
    warn = Issue("n1", "warning", "c2", "w")
    info = Issue("n2", "info", "c3", "i")
    report = ValidationReport(issues=(err, warn, info))

    assert report.has_errors is True
    assert report.by_severity("warning") == (warn,)
    assert report.for_node("n1") == (err, warn)
    assert report.for_node("n2") == (info,)

    d = report.to_dict()
    assert d["has_errors"] is True
    assert len(d["issues"]) == 3


def test_validation_report_empty_has_no_errors():
    from app.shared.execution.validation_types import ValidationReport

    report = ValidationReport()
    assert report.has_errors is False
    assert report.to_dict() == {"has_errors": False, "issues": []}


def test_validation_report_warnings_only_no_errors():
    from app.shared.execution.validation_types import Issue, ValidationReport

    report = ValidationReport(issues=(Issue("n", "warning", "c", "m"),))
    assert report.has_errors is False


# --- 0.3 registry / protocol ---


@pytest.fixture(autouse=True)
def _clean_registry():
    from app.shared.execution import validation_protocol as vp

    vp.clear_node_validators()
    yield
    vp.clear_node_validators()


class _DummyValidator:
    def validate_node(self, node, *, upstream):
        return []


def test_register_and_lookup_validator():
    from app.shared.execution import validation_protocol as vp

    v = _DummyValidator()
    vp.register_node_validator("transform", v)
    assert vp.validator_for("transform") is v
    assert vp.registered_node_types() == {"transform"}


def test_validator_for_unknown_returns_none():
    from app.shared.execution import validation_protocol as vp

    assert vp.validator_for("nope") is None


def test_register_overwrites_existing():
    from app.shared.execution import validation_protocol as vp

    first, second = _DummyValidator(), _DummyValidator()
    vp.register_node_validator("carica", first)
    vp.register_node_validator("carica", second)
    assert vp.validator_for("carica") is second


def test_clear_empties_registry():
    from app.shared.execution import validation_protocol as vp

    vp.register_node_validator("x", _DummyValidator())
    vp.clear_node_validators()
    assert vp.registered_node_types() == frozenset()


def test_dummy_satisfies_protocol_runtime_check():
    from app.shared.execution.validation_protocol import SupportsNodeValidation

    assert isinstance(_DummyValidator(), SupportsNodeValidation)
