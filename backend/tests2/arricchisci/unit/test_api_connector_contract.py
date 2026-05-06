import pytest

from app.core.arricchisci._api_connection_helpers import _nd


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


def test_nd_reads_flat_field_before_data_fallback():
    node = {"name": "flat", "data": {"name": "nested"}}
    assert _nd(node, "name") == "flat"


def test_nd_uses_data_fallback_when_flat_missing():
    node = {"data": {"name": "nested"}}
    assert _nd(node, "name") == "nested"
