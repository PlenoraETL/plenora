"""Contracts for shared execution schema types."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared]


def test_column_schema_lives_in_shared_execution_and_keeps_legacy_alias() -> None:
    from app.core.projects.schema_inference import ColumnSchema as LegacyColumnSchema
    from app.shared.execution.schema_types import ColumnSchema

    assert LegacyColumnSchema is ColumnSchema

    column = ColumnSchema(
        name="total",
        type="real",
        source_node="node_1",
        is_calculated=True,
    )

    assert column.to_dict() == {
        "name": "total",
        "type": "real",
        "source_node": "node_1",
        "is_calculated": True,
    }
