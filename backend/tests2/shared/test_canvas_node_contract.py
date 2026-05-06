"""Contract tests for CanvasNode typed accessor.

BE-GO-READY-ARCH-055-W3: typed node accessor pilot.
Verifies the two-level field lookup, typed properties, and TypeError guard.
"""
import pytest
from app.shared.canvas_node import CanvasNode


# ──────────────────────────────────────────────
# Properties
# ──────────────────────────────────────────────

def test_id_from_root():
    assert CanvasNode({"id": "n1", "type": "api_enrich"}).id == "n1"

def test_id_missing_returns_empty_string():
    assert CanvasNode({"type": "api_enrich"}).id == ""

def test_type_from_root():
    assert CanvasNode({"type": "file_source"}).type == "file_source"

def test_type_missing_returns_none():
    assert CanvasNode({}).type is None

def test_data_property_returns_dict():
    assert CanvasNode({"data": {"x": 1}}).data == {"x": 1}

def test_data_property_absent_returns_empty_dict():
    assert CanvasNode({"type": "api_enrich"}).data == {}

def test_data_property_non_dict_returns_empty_dict():
    assert CanvasNode({"data": "bad"}).data == {}


# ──────────────────────────────────────────────
# get_field two-level lookup
# ──────────────────────────────────────────────

def test_get_field_top_level_wins():
    node = {"api_id": "top", "data": {"api_id": "nested"}}
    assert CanvasNode(node).get_field("api_id") == "top"

def test_get_field_falls_back_to_data():
    node = {"data": {"api_id": "nested"}}
    assert CanvasNode(node).get_field("api_id") == "nested"

def test_get_field_top_level_none_falls_back():
    node = {"api_id": None, "data": {"api_id": "nested"}}
    assert CanvasNode(node).get_field("api_id") == "nested"

def test_get_field_missing_returns_default():
    assert CanvasNode({}).get_field("missing", "fallback") == "fallback"

def test_get_field_empty_list_is_not_none_no_fallback():
    """Empty list at top-level is returned as-is (None-only check, like _nd)."""
    node = {"output_mapping": [], "data": {"output_mapping": [{"column": "col"}]}}
    assert CanvasNode(node).get_field("output_mapping") == []

def test_get_field_zero_is_truthy_value_returned():
    node = {"count": 0}
    assert CanvasNode(node).get_field("count") == 0


# ──────────────────────────────────────────────
# TypeError guard
# ──────────────────────────────────────────────

def test_non_dict_raises_type_error():
    with pytest.raises(TypeError, match="CanvasNode expects a dict"):
        CanvasNode("not-a-dict")


# ──────────────────────────────────────────────
# Integration: parallel.py helpers via CanvasNode
# ──────────────────────────────────────────────

def test_parallel_extract_consumed_uses_canvas_node():
    """_extract_consumed_columns reads param_mapping via CanvasNode."""
    from app.core.arricchisci.parallel import _extract_consumed_columns
    node = {"data": {"param_mapping": {"p": "colA"}}}
    assert _extract_consumed_columns(node) == {"colA"}

def test_parallel_extract_produced_uses_canvas_node():
    from app.core.arricchisci.parallel import _extract_produced_columns
    node = {"output_mapping": [{"column": "result"}]}
    assert _extract_produced_columns(node) == {"result"}

def test_parallel_may_explode_cardinality_type_check():
    from app.core.arricchisci.parallel import _node_may_explode_cardinality
    non_enrich = {"type": "file_source"}
    assert _node_may_explode_cardinality(non_enrich) is False

def test_parallel_may_explode_cardinality_path_marker():
    from app.core.arricchisci.parallel import _node_may_explode_cardinality
    node = {"type": "api_enrich", "output_mapping": [{"path": "items[]", "column": "c"}]}
    assert _node_may_explode_cardinality(node) is True
