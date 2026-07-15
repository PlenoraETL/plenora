"""Validation contracts for multi-layer upload configuration."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.core.files.upload_boundary import parse_layers_config


pytestmark = [pytest.mark.unit, pytest.mark.regression]


@pytest.mark.parametrize("payload", [
    ["layer-a"],
    [1],
    [{}],
    [{"layer": "a", "sheet": "b"}],
    [{"layer": "a", "selected_columns": "id"}],
    [{"sheet": "Summary", "selected_columns": []}],
    [{"layer": "a"}, {"layer": "a"}],
])
def test_invalid_layer_entries_return_http_400(payload):
    with pytest.raises(HTTPException) as exc_info:
        parse_layers_config(json.dumps(payload))
    assert exc_info.value.status_code == 400


def test_valid_layer_and_sheet_entries_are_preserved():
    payload = [
        {"layer": "buildings", "selected_columns": ["id", "geometry"]},
        {"sheet": "Summary", "selected_columns": None},
    ]
    assert parse_layers_config(json.dumps(payload)) == payload
