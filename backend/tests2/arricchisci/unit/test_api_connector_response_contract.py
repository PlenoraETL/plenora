"""
Tests the api connector response behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


def test_extract_rows_from_response_supports_wildcard_paths(api_service):
    data = {
        "data": [
            {"id": 1, "meta": {"name": "A"}},
            {"id": 2, "meta": {"name": "B"}},
        ]
    }
    mapping = [
        {"path": "data[*].id", "column": "id"},
        {"path": "data[*].meta.name", "column": "name"},
    ]

    rows = api_service._extract_rows_from_response(data, mapping)

    assert rows == [
        {"id": 1, "name": "A"},
        {"id": 2, "name": "B"},
    ]


def test_extract_rows_from_response_autodetects_data_array(api_service):
    data = {
        "data": [
            {"code": "x1", "value": 10},
            {"code": "x2", "value": 20},
        ]
    }
    mapping = [
        {"path": "code", "column": "code"},
        {"path": "value", "column": "value"},
    ]

    rows = api_service._extract_rows_from_response(data, mapping)

    assert rows == [
        {"code": "x1", "value": 10},
        {"code": "x2", "value": 20},
    ]
