"""
tests/scheduler/unit/test_schema_inference_coverage.py
======================================================
Unit tests for app.services.schema_inference — ColumnSchema, type inference,
schema propagation through canvas graph.

Mocked: project_service.
"""

import pytest
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.unit

from app.services.schema_inference import (
    ColumnSchema,
    SchemaInferenceService,
)


# ---------------------------------------------------------------------------
# Helpers — mock canvas structures
# ---------------------------------------------------------------------------

def _make_node(
    node_id="n1",
    node_type="source",
    columns=None,
    calculated_columns=None,
    data=None,
    config=None,
    transform_op=None,
    source_project_id=None,
):
    node = MagicMock()
    node.id = node_id
    node.type = node_type
    node.columns = columns or []
    node.calculated_columns = calculated_columns or []
    node.data = data or {}
    node.config = config or {}
    node.transform_op = transform_op
    node.source_project_id = source_project_id
    return node


def _make_edge(source, target):
    edge = MagicMock()
    edge.source = source
    edge.target = target
    return edge


def _make_canvas(nodes, edges=None):
    canvas = MagicMock()
    canvas.nodes = nodes
    canvas.edges = edges or []
    return canvas


def _make_column(name, type_="str", visible=True, alias=None):
    col = MagicMock()
    col.name = name
    col.type = type_
    col.visible = visible
    col.alias = alias
    return col


# =============================================================================
# ColumnSchema
# =============================================================================

class TestColumnSchema:

    def test_to_dict(self):
        cs = ColumnSchema(name="col1", type="int", source_node="n1", is_calculated=False)
        d = cs.to_dict()
        assert d["name"] == "col1"
        assert d["type"] == "int"
        assert d["source_node"] == "n1"
        assert d["is_calculated"] is False

    def test_to_dict_calculated(self):
        cs = ColumnSchema(name="formula_col", type="real", is_calculated=True)
        d = cs.to_dict()
        assert d["is_calculated"] is True

    def test_defaults(self):
        cs = ColumnSchema(name="c", type="str")
        assert cs.source_node is None
        assert cs.is_calculated is False


# =============================================================================
# SchemaInferenceService — infer_all_schemas
# =============================================================================

class TestInferAllSchemas:

    def test_empty_canvas(self):
        mock_ps = MagicMock()
        mock_ps.get_project_canvas.return_value = None
        svc = SchemaInferenceService(mock_ps)
        result = svc.infer_all_schemas("proj1")
        assert result == {}

    def test_single_source_node(self):
        col = _make_column("id", "int")
        node = _make_node("n1", "source", columns=[col])
        canvas = _make_canvas([node])

        mock_ps = MagicMock()
        mock_ps.get_project_canvas.return_value = canvas

        svc = SchemaInferenceService(mock_ps)
        result = svc.infer_all_schemas("proj1")

        assert "n1" in result
        assert len(result["n1"]) == 1
        assert result["n1"][0]["name"] == "id"
        assert result["n1"][0]["type"] == "int"

    def test_source_with_alias(self):
        col = _make_column("original_name", "str", alias="alias_name")
        node = _make_node("n1", "source", columns=[col])
        canvas = _make_canvas([node])

        mock_ps = MagicMock()
        mock_ps.get_project_canvas.return_value = canvas

        svc = SchemaInferenceService(mock_ps)
        result = svc.infer_all_schemas("proj1")

        assert result["n1"][0]["name"] == "alias_name"

    def test_source_with_invisible_columns(self):
        cols = [
            _make_column("visible_col", "int", visible=True),
            _make_column("hidden_col", "str", visible=False),
        ]
        node = _make_node("n1", "source", columns=cols)
        canvas = _make_canvas([node])

        mock_ps = MagicMock()
        mock_ps.get_project_canvas.return_value = canvas

        svc = SchemaInferenceService(mock_ps)
        result = svc.infer_all_schemas("proj1")

        names = [c["name"] for c in result["n1"]]
        assert "visible_col" in names
        assert "hidden_col" not in names

    def test_source_with_calculated_columns(self):
        node = _make_node(
            "n1", "source",
            columns=[_make_column("id", "int")],
            calculated_columns=[
                {"name": "total", "output_type": "real"},
            ],
        )
        canvas = _make_canvas([node])

        mock_ps = MagicMock()
        mock_ps.get_project_canvas.return_value = canvas

        svc = SchemaInferenceService(mock_ps)
        result = svc.infer_all_schemas("proj1")

        names = [c["name"] for c in result["n1"]]
        assert "total" in names
        calc_col = next(c for c in result["n1"] if c["name"] == "total")
        assert calc_col["is_calculated"] is True


# =============================================================================
# SchemaInferenceService — infer_node_schema (single)
# =============================================================================

class TestInferNodeSchema:

    def test_node_not_found(self):
        canvas = _make_canvas([_make_node("n1", "source")])
        mock_ps = MagicMock()
        mock_ps.get_project_canvas.return_value = canvas

        svc = SchemaInferenceService(mock_ps)
        result = svc.infer_node_schema("proj1", "nonexistent")
        assert result == []

    def test_no_canvas(self):
        mock_ps = MagicMock()
        mock_ps.get_project_canvas.return_value = None

        svc = SchemaInferenceService(mock_ps)
        result = svc.infer_node_schema("proj1", "n1")
        assert result == []

    def test_single_node(self):
        col = _make_column("name", "str")
        node = _make_node("n1", "source", columns=[col])
        canvas = _make_canvas([node])

        mock_ps = MagicMock()
        mock_ps.get_project_canvas.return_value = canvas

        svc = SchemaInferenceService(mock_ps)
        result = svc.infer_node_schema("proj1", "n1")
        assert len(result) == 1
        assert result[0]["name"] == "name"


# =============================================================================
# _infer_file_source_schema
# =============================================================================

class TestInferFileSourceSchema:

    def test_with_columns(self):
        node = _make_node("n1", "file_source", data={
            "columns": [
                {"name": "col1", "type": "int", "visible": True},
                {"name": "col2", "type": "str", "visible": False},
            ]
        })
        node.columns = None  # Force fallback to data.columns

        mock_ps = MagicMock()
        svc = SchemaInferenceService(mock_ps)
        schema = svc._infer_file_source_schema(node)
        assert len(schema) == 1  # Only visible
        assert schema[0].name == "col1"

    def test_no_columns(self):
        node = _make_node("n1", "file_source", data={})
        node.columns = None

        mock_ps = MagicMock()
        svc = SchemaInferenceService(mock_ps)
        schema = svc._infer_file_source_schema(node)
        assert schema == []


# =============================================================================
# _infer_transform_schema — various ops
# =============================================================================

class TestInferTransformSchema:

    def _make_input_schema(self):
        return [
            ColumnSchema(name="id", type="int", source_node="n1"),
            ColumnSchema(name="value", type="real", source_node="n1"),
        ]

    def test_filter_passthrough(self):
        node = _make_node("t1", "transform", transform_op="filter")
        mock_ps = MagicMock()
        svc = SchemaInferenceService(mock_ps)
        schema = svc._infer_transform_schema(
            node, [self._make_input_schema()], {}
        )
        assert len(schema) == 2

    def test_sort_passthrough(self):
        node = _make_node("t1", "transform", transform_op="sort")
        mock_ps = MagicMock()
        svc = SchemaInferenceService(mock_ps)
        schema = svc._infer_transform_schema(
            node, [self._make_input_schema()], {}
        )
        assert len(schema) == 2

    def test_no_transform_op_passthrough(self):
        node = _make_node("t1", "transform", transform_op=None)
        node.data = {}
        mock_ps = MagicMock()
        svc = SchemaInferenceService(mock_ps)
        schema = svc._infer_transform_schema(
            node, [self._make_input_schema()], {}
        )
        assert len(schema) == 2

    def test_unknown_transform_op_passthrough(self):
        node = _make_node("t1", "transform", transform_op="exotic_op")
        mock_ps = MagicMock()
        svc = SchemaInferenceService(mock_ps)
        schema = svc._infer_transform_schema(
            node, [self._make_input_schema()], {}
        )
        assert len(schema) == 2

    def test_no_input_schemas(self):
        node = _make_node("t1", "transform", transform_op=None)
        node.data = {}
        mock_ps = MagicMock()
        svc = SchemaInferenceService(mock_ps)
        schema = svc._infer_transform_schema(node, [], {})
        assert schema == []


# =============================================================================
# _infer_api_enrich_schema
# =============================================================================

class TestInferApiEnrichSchema:

    def test_adds_output_fields(self):
        node = _make_node("e1", "api_enrich", data={
            "output_fields": [
                {"name": "geocoded_address", "type": "str"},
                {"name": "lat", "type": "real"},
            ]
        })
        input_schema = [ColumnSchema(name="address", type="str")]

        mock_ps = MagicMock()
        svc = SchemaInferenceService(mock_ps)
        schema = svc._infer_api_enrich_schema(node, [input_schema])

        names = [c.name for c in schema]
        assert "address" in names
        assert "geocoded_address" in names
        assert "lat" in names
        assert len(schema) == 3

    def test_no_output_fields(self):
        node = _make_node("e1", "api_enrich", data={})
        input_schema = [ColumnSchema(name="col1", type="str")]

        mock_ps = MagicMock()
        svc = SchemaInferenceService(mock_ps)
        schema = svc._infer_api_enrich_schema(node, [input_schema])
        assert len(schema) == 1

    def test_no_input(self):
        node = _make_node("e1", "api_enrich", data={})
        mock_ps = MagicMock()
        svc = SchemaInferenceService(mock_ps)
        schema = svc._infer_api_enrich_schema(node, [])
        assert schema == []


# =============================================================================
# Result node — passthrough
# =============================================================================

class TestResultNode:

    def test_result_passthrough(self):
        source_col = _make_column("id", "int")
        source_node = _make_node("n1", "source", columns=[source_col])
        result_node = _make_node("r1", "result")

        edge = _make_edge("n1", "r1")
        canvas = _make_canvas([source_node, result_node], edges=[edge])

        mock_ps = MagicMock()
        mock_ps.get_project_canvas.return_value = canvas

        svc = SchemaInferenceService(mock_ps)
        result = svc.infer_all_schemas("proj1")

        assert "r1" in result
        assert len(result["r1"]) == 1
        assert result["r1"][0]["name"] == "id"

    def test_result_no_input(self):
        result_node = _make_node("r1", "result")
        canvas = _make_canvas([result_node])

        mock_ps = MagicMock()
        mock_ps.get_project_canvas.return_value = canvas

        svc = SchemaInferenceService(mock_ps)
        result = svc.infer_all_schemas("proj1")
        assert result["r1"] == []


# =============================================================================
# Schema caching
# =============================================================================

class TestSchemaCaching:

    def test_cache_prevents_recomputation(self):
        col = _make_column("id", "int")
        node = _make_node("n1", "source", columns=[col])
        canvas = _make_canvas([node])

        mock_ps = MagicMock()
        mock_ps.get_project_canvas.return_value = canvas

        svc = SchemaInferenceService(mock_ps)

        # First call populates cache
        result1 = svc.infer_all_schemas("proj1")
        # Cache should be populated
        assert "n1" in svc._schema_cache

    def test_cache_resets_on_new_call(self):
        col = _make_column("id", "int")
        node = _make_node("n1", "source", columns=[col])
        canvas = _make_canvas([node])

        mock_ps = MagicMock()
        mock_ps.get_project_canvas.return_value = canvas

        svc = SchemaInferenceService(mock_ps)
        svc.infer_all_schemas("proj1")

        # infer_node_schema resets cache
        svc.infer_node_schema("proj1", "n1")
        # Still works
        assert len(svc._schema_cache) > 0
