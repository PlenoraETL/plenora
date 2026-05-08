"""Typed static-parameter contract for StaticValues/API static params."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.artifacts.static_parameters import (
    StaticParameterField,
    process_static_fields,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_static_parameter_contract_resolves_templates_and_casts_types() -> None:
    reference_date = datetime(2026, 2, 2, 11, 30, tzinfo=timezone.utc)

    processed = process_static_fields(
        [
            StaticParameterField("q", "reftime:>={{NOW-2h}}"),
            {"name": "limit", "value": "10.9", "field_type": "integer"},
            {"name": "ratio", "value": "1.25", "field_type": "float"},
            {"name": "enabled", "value": "si", "field_type": "boolean"},
            {"name": "", "value": "ignored"},
        ],
        reference_date=reference_date,
    )

    assert processed == {
        "q": "reftime:>=2026-02-02 09:30",
        "limit": 10,
        "ratio": 1.25,
        "enabled": True,
    }


def test_static_parameter_contract_keeps_invalid_cast_value() -> None:
    processed = process_static_fields(
        [{"name": "limit", "value": "not-a-number", "field_type": "integer"}]
    )

    assert processed == {"limit": "not-a-number"}
