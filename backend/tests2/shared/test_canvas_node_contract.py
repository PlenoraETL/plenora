"""Contract tests for CanvasNode typed accessor and CanvasGraph intake.

BE-GO-READY-ARCH-055-W3: typed node accessor pilot.
BE-GO-READY-ARCH-058-W3: CanvasGraph intake replaces parallel local dicts.
Verifies the two-level field lookup, typed properties, TypeError guard,
and CanvasGraph from_nodes_edges / lookup / result_node contract.
"""
import pytest
from app.shared.execution.canvas_node import CanvasNode
from app.shared.execution.canvas_graph import CanvasGraph


# ------------------------------------------------------------
# Properties
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# get_field two-level lookup
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# TypeError guard
# ------------------------------------------------------------

def test_non_dict_raises_type_error():
    with pytest.raises(TypeError, match="CanvasNode expects a dict"):
        CanvasNode("not-a-dict")


# ------------------------------------------------------------
# Integration: parallel.py helpers via CanvasNode
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# CanvasGraph contract (BE-GO-READY-ARCH-058-W3)
# ------------------------------------------------------------

_NODES = [
    {"id": "n1", "type": "file_source", "data": {"file_source_id": "fs-1"}},
    {"id": "n2", "type": "api_enrich", "api_id": "api-x"},
    {"id": "n3", "type": "result", "data": {"format": "csv", "fileName": "out"}},
]
_EDGES = [
    {"source": "n1", "target": "n2"},
    {"source": "n2", "target": "n3"},
]


def test_canvas_graph_from_nodes_edges_builds():
    g = CanvasGraph.from_nodes_edges(_NODES, _EDGES)
    assert g is not None


def test_canvas_graph_raw_returns_dict():
    g = CanvasGraph.from_nodes_edges(_NODES, _EDGES)
    assert g.raw("n1") == _NODES[0]
    assert g.raw("missing") is None


def test_canvas_graph_get_returns_canvas_node():
    g = CanvasGraph.from_nodes_edges(_NODES, _EDGES)
    cn = g.get("n1")
    assert isinstance(cn, CanvasNode)
    assert cn.type == "file_source"
    assert g.get("missing") is None


def test_canvas_graph_typed_field_access():
    g = CanvasGraph.from_nodes_edges(_NODES, _EDGES)
    assert g.get("n1").get_field("file_source_id") == "fs-1"
    assert g.get("n2").get_field("api_id") == "api-x"


def test_canvas_graph_result_node_found():
    g = CanvasGraph.from_nodes_edges(_NODES, _EDGES)
    rn = g.result_node()
    assert rn is not None
    assert rn.type == "result"
    assert rn.get_field("format") == "csv"


def test_canvas_graph_result_node_none_when_absent():
    g = CanvasGraph.from_nodes_edges(
        [{"id": "n1", "type": "file_source"}], []
    )
    assert g.result_node() is None


def test_canvas_graph_raw_map_property():
    g = CanvasGraph.from_nodes_edges(_NODES, _EDGES)
    rm = g.raw_map
    assert set(rm.keys()) == {"n1", "n2", "n3"}
    assert rm["n2"] == _NODES[1]


def test_canvas_graph_edges_property():
    g = CanvasGraph.from_nodes_edges(_NODES, _EDGES)
    assert g.edges == _EDGES


def test_canvas_graph_raw_map_usable_by_legacy_callers():
    """raw_map can be passed directly to is_file_source_for_file_processor."""
    from app.core.arricchisci.canvas_orchestrator import is_file_source_for_file_processor
    api_connections = {"api-x": {"connection_type": "rest_file"}}
    g = CanvasGraph.from_nodes_edges(_NODES, _EDGES)
    result = is_file_source_for_file_processor(
        "n1", _EDGES, g.raw_map, api_connections
    )
    assert result is True


def test_canvas_graph_skips_non_dict_nodes():
    nodes = [{"id": "n1", "type": "file_source"}, "bad", None]
    g = CanvasGraph.from_nodes_edges(nodes, [])
    assert set(g.raw_map.keys()) == {"n1"}


def test_canvas_graph_empty_inputs():
    g = CanvasGraph.from_nodes_edges([], [])
    assert g.raw_map == {}
    assert g.edges == []
    assert g.result_node() is None


def test_canvas_graph_accepts_pydantic_like_nodes():
    """BE-GO-READY-ARCH-059-W2: from_nodes_edges handles Pydantic model nodes
    (those with model_dump()) alongside plain dicts. This lets Manipola build a
    CanvasGraph from CanvasState.nodes without pre-converting to raw dicts."""

    class _PydanticNode:
        """Minimal stand-in for a Pydantic CanvasState node."""
        def __init__(self, id, type_, position=None):
            self._id = id
            self._type = type_
            self._position = position or {"x": 0, "y": 0}

        def model_dump(self):
            return {
                "id": self._id,
                "type": self._type,
                "position": self._position,
                "data": {},
            }

    nodes = [
        _PydanticNode("n1", "transform", {"x": 10, "y": 100}),
        _PydanticNode("n2", "result", {"x": 20, "y": 200}),
    ]
    g = CanvasGraph.from_nodes_edges(nodes, [])

    assert set(g.raw_map.keys()) == {"n1", "n2"}
    # raw dict exposes position as a plain dict (model_dump output)
    assert g.raw("n1")["position"] == {"x": 10, "y": 100}
    # typed accessor works normally
    assert g.get("n2").type == "result"
    assert g.result_node() is not None
    assert g.result_node().type == "result"


def test_canvas_graph_mixed_dict_and_pydantic_nodes():
    """from_nodes_edges accepts a mixed list of plain dicts and Pydantic nodes."""

    class _PydanticNode:
        def __init__(self, id, type_):
            self._id = id
            self._type = type_

        def model_dump(self):
            return {"id": self._id, "type": self._type, "data": {}}

    nodes = [
        {"id": "dict-node", "type": "file_source"},
        _PydanticNode("pydantic-node", "transform"),
    ]
    g = CanvasGraph.from_nodes_edges(nodes, [])
    assert set(g.raw_map.keys()) == {"dict-node", "pydantic-node"}
    assert g.get("dict-node").type == "file_source"
    assert g.get("pydantic-node").type == "transform"
