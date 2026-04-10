"""
tests/arricchisci/unit/test_debug_payload_coverage.py
======================================================
Coverage tests for debug_payload_builder.py — DebugPayloadBuilder.

Tests all public methods and edge cases:
  - build(): full payload assembly with all fields
  - build(): minimal payload (no input_sample)
  - _build_pipeline_context(): success path with real project data
  - _build_pipeline_context(): exception path returns fallback
  - _summarize_canvas(): dict-based canvas with nodes and edges
  - _summarize_canvas(): object-based canvas (hasattr nodes/edges)
  - _summarize_canvas(): empty canvas returns "non disponibile"
  - _summarize_canvas(): no root nodes fallback (all nodes are targets)
  - _summarize_canvas(): non-dict/non-object returns "non disponibile"
  - _summarize_canvas(): exception returns "non disponibile"
  - _extract_node_config(): found node returns config minus internal keys
  - _extract_node_config(): node_id is None returns empty dict
  - _extract_node_config(): node not found returns empty dict
  - _extract_node_config(): empty canvas returns empty dict
  - _extract_node_config(): exception returns empty dict
  - _extract_node_config(): object-based nodes (hasattr id/data)
"""

import sys
sys.path.insert(0, r"C:\Users\Marco\Desktop\int_efm\URBY-ETL\backend")

import pytest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

pytestmark = pytest.mark.unit

from app.utils.debug_payload_builder import DebugPayloadBuilder


# =============================================================================
# Helpers
# =============================================================================

def _mock_project(canvas=None, name="TestProject", project_type="manipola"):
    """Create a mock project dict as returned by config_db_service."""
    return {
        "name": name,
        "project_type": project_type,
        "triggered_by": "manual",
        "canvas": canvas,
    }


def _sample_canvas_dict():
    """Canvas as a dict with nodes and edges."""
    return {
        "nodes": [
            {"id": "n1", "type": "estrai", "data": {"label": "Estrai DB"}},
            {"id": "n2", "type": "manipola", "data": {"label": "Trasforma"}},
            {"id": "n3", "type": "carica", "data": {"label": "Carica"}},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ],
    }


def _sample_canvas_objects():
    """Canvas as an object with .nodes and .edges attributes."""
    nodes = [
        SimpleNamespace(id="n1", type="estrai", data={"label": "Estrai"}),
        SimpleNamespace(id="n2", type="manipola", data={"label": "Manipola"}),
    ]
    edges = [
        SimpleNamespace(source="n1", target="n2"),
    ]
    return SimpleNamespace(nodes=nodes, edges=edges)


def _sample_failure():
    return {
        "node_id": "n2",
        "node_type": "manipola",
        "node_label": "Trasforma",
        "module": "manipola",
        "error_type": "ValueError",
        "error_message": "Column not found",
        "traceback": "Traceback ...",
        "occurred_at_ms": 1500,
    }


def _sample_steps():
    return [
        {"step": 1, "module": "estrai", "status": "success", "rows_out": 100, "duration_ms": 200},
        {"step": 2, "module": "manipola", "status": "failure", "rows_out": 0, "duration_ms": 50},
    ]


# =============================================================================
# DebugPayloadBuilder.build — full assembly
# =============================================================================

class TestBuild:

    def test_build_full_payload(self):
        """build() returns dict with all 5 expected keys."""
        builder = DebugPayloadBuilder()
        canvas = _sample_canvas_dict()
        project = _mock_project(canvas=canvas)
        failure = _sample_failure()
        steps = _sample_steps()
        input_sample = {"rows": 100, "columns": ["a", "b"], "sample": [{"a": 1}]}

        with patch("app.db.config_db_service.get_project", return_value=project):
            payload = builder.build(
                run_id="run_1",
                project_id="proj_1",
                steps=steps,
                failure=failure,
                input_sample=input_sample,
            )

        assert "pipeline_context" in payload
        assert "steps" in payload
        assert "failure" in payload
        assert "failing_node_config" in payload
        assert "input_sample" in payload
        assert payload["steps"] == steps
        assert payload["failure"] == failure
        assert payload["input_sample"] == input_sample

    def test_build_without_input_sample(self):
        """build() with no input_sample defaults to empty dict."""
        builder = DebugPayloadBuilder()
        failure = _sample_failure()

        with patch("app.db.config_db_service.get_project", return_value=None):
            payload = builder.build(
                run_id="run_2",
                project_id="proj_2",
                steps=[],
                failure=failure,
            )

        assert payload["input_sample"] == {}

    def test_build_failure_without_node_id(self):
        """build() with failure missing node_id returns empty node config."""
        builder = DebugPayloadBuilder()
        failure = {"error_message": "Something failed"}

        with patch("app.db.config_db_service.get_project", return_value=None):
            payload = builder.build(
                run_id="run_3",
                project_id="proj_3",
                steps=[],
                failure=failure,
            )

        assert payload["failing_node_config"] == {}


# =============================================================================
# _build_pipeline_context
# =============================================================================

class TestBuildPipelineContext:

    def test_success_path_with_project(self):
        """Returns full context with project metadata and canvas summary."""
        builder = DebugPayloadBuilder()
        canvas = _sample_canvas_dict()
        project = _mock_project(canvas=canvas, name="MyProject", project_type="manipola")

        with patch("app.db.config_db_service.get_project", return_value=project):
            ctx = builder._build_pipeline_context("proj_1", _sample_steps())

        assert ctx["project_id"] == "proj_1"
        assert ctx["project_name"] == "MyProject"
        assert ctx["module"] == "manipola"
        assert ctx["triggered_by"] == "manual"
        assert "Estrai DB" in ctx["canvas_summary"]

    def test_project_not_found(self):
        """get_project returns None -> uses project_id as name."""
        builder = DebugPayloadBuilder()

        with patch("app.db.config_db_service.get_project", return_value=None):
            ctx = builder._build_pipeline_context("proj_404", [])

        assert ctx["project_id"] == "proj_404"
        assert ctx["project_name"] == "proj_404"

    def test_exception_returns_fallback(self):
        """Exception during context building returns minimal fallback."""
        builder = DebugPayloadBuilder()

        with patch("app.db.config_db_service.get_project", side_effect=RuntimeError("DB down")):
            ctx = builder._build_pipeline_context("proj_err", [])

        assert ctx["project_id"] == "proj_err"
        assert ctx["canvas_summary"] == "non disponibile"

    def test_project_with_no_canvas(self):
        """Project with canvas=None returns 'non disponibile' summary."""
        builder = DebugPayloadBuilder()
        project = _mock_project(canvas=None)

        with patch("app.db.config_db_service.get_project", return_value=project):
            ctx = builder._build_pipeline_context("proj_nc", [])

        assert ctx["canvas_summary"] == "non disponibile"


# =============================================================================
# _summarize_canvas
# =============================================================================

class TestSummarizeCanvas:

    def test_dict_canvas_with_chain(self):
        """Dict canvas with nodes and edges produces arrow-chain."""
        builder = DebugPayloadBuilder()
        canvas = _sample_canvas_dict()
        summary = builder._summarize_canvas(canvas)
        assert "Estrai DB" in summary
        assert "Trasforma" in summary
        assert "Carica" in summary
        assert " → " in summary  # uses arrow separator

    def test_object_canvas_with_chain(self):
        """Object canvas (hasattr nodes/edges) produces chain."""
        builder = DebugPayloadBuilder()
        canvas = _sample_canvas_objects()
        summary = builder._summarize_canvas(canvas)
        assert "Estrai" in summary
        assert "Manipola" in summary

    def test_empty_canvas_returns_non_disponibile(self):
        """Empty/None canvas returns 'non disponibile'."""
        builder = DebugPayloadBuilder()
        assert builder._summarize_canvas(None) == "non disponibile"
        assert builder._summarize_canvas({}) == "non disponibile"
        assert builder._summarize_canvas("") == "non disponibile"

    def test_no_roots_fallback_to_node_labels(self):
        """When all nodes are targets (no root), fallback to listing labels."""
        builder = DebugPayloadBuilder()
        # Create a cycle so there's no root
        canvas = {
            "nodes": [
                {"id": "n1", "type": "a", "data": {"label": "Node1"}},
                {"id": "n2", "type": "b", "data": {"label": "Node2"}},
            ],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n1"},
            ],
        }
        summary = builder._summarize_canvas(canvas)
        # All nodes are both sources and targets -> roots is empty
        # Fallback: " → ".join(node_labels.values())
        assert "Node1" in summary or "Node2" in summary

    def test_non_dict_non_object_returns_non_disponibile(self):
        """Non-dict/non-object canvas returns 'non disponibile'."""
        builder = DebugPayloadBuilder()
        assert builder._summarize_canvas(42) == "non disponibile"
        assert builder._summarize_canvas([1, 2, 3]) == "non disponibile"

    def test_canvas_with_no_edges(self):
        """Canvas with nodes but no edges."""
        builder = DebugPayloadBuilder()
        canvas = {
            "nodes": [{"id": "n1", "type": "a", "data": {"label": "Solo"}}],
            "edges": [],
        }
        summary = builder._summarize_canvas(canvas)
        # No edges -> sources is empty set -> roots = empty set - empty set = empty
        # Fallback: join node_labels.values()
        assert "Solo" in summary

    def test_node_without_label_uses_type(self):
        """Node without data.label falls back to type."""
        builder = DebugPayloadBuilder()
        canvas = {
            "nodes": [
                {"id": "n1", "type": "estrai", "data": {}},
                {"id": "n2", "type": "carica", "data": None},
            ],
            "edges": [{"source": "n1", "target": "n2"}],
        }
        summary = builder._summarize_canvas(canvas)
        assert "estrai" in summary
        assert "carica" in summary

    def test_exception_in_summarize_returns_non_disponibile(self):
        """Exception during summarization returns 'non disponibile'."""
        builder = DebugPayloadBuilder()
        # Pass something that will cause attribute errors internally
        bad_canvas = {"nodes": "not a list", "edges": "not a list"}
        summary = builder._summarize_canvas(bad_canvas)
        assert summary == "non disponibile"


# =============================================================================
# _extract_node_config
# =============================================================================

class TestExtractNodeConfig:

    def test_found_node_returns_config(self):
        """Node found in canvas returns its data minus internal keys."""
        builder = DebugPayloadBuilder()
        canvas = {
            "nodes": [
                {"id": "n1", "type": "estrai", "data": {
                    "label": "Estrai",
                    "color": "blue",
                    "__pydantic_fields_set__": {"a"},
                    "connection_id": "conn_1",
                    "query": "SELECT * FROM t",
                }},
            ],
            "edges": [],
        }
        project = _mock_project(canvas=canvas)

        with patch("app.db.config_db_service.get_project", return_value=project):
            config = builder._extract_node_config("proj_1", "n1")

        assert "connection_id" in config
        assert "query" in config
        assert "label" not in config  # internal, removed
        assert "color" not in config  # internal, removed
        assert "__pydantic_fields_set__" not in config  # internal, removed

    def test_node_id_none_returns_empty(self):
        """node_id=None returns empty dict immediately."""
        builder = DebugPayloadBuilder()
        assert builder._extract_node_config("proj_1", None) == {}

    def test_node_not_found_returns_empty(self):
        """Node ID not in canvas returns empty dict."""
        builder = DebugPayloadBuilder()
        canvas = {
            "nodes": [{"id": "n1", "type": "a", "data": {"x": 1}}],
            "edges": [],
        }
        project = _mock_project(canvas=canvas)

        with patch("app.db.config_db_service.get_project", return_value=project):
            config = builder._extract_node_config("proj_1", "n_missing")

        assert config == {}

    def test_empty_canvas_returns_empty(self):
        """Empty canvas returns empty dict."""
        builder = DebugPayloadBuilder()
        project = _mock_project(canvas={})

        with patch("app.db.config_db_service.get_project", return_value=project):
            config = builder._extract_node_config("proj_1", "n1")

        assert config == {}

    def test_no_canvas_returns_empty(self):
        """Project with no canvas key returns empty dict."""
        builder = DebugPayloadBuilder()
        project = {"name": "test"}

        with patch("app.db.config_db_service.get_project", return_value=project):
            config = builder._extract_node_config("proj_1", "n1")

        assert config == {}

    def test_exception_returns_empty(self):
        """Exception during extraction returns empty dict."""
        builder = DebugPayloadBuilder()

        with patch("app.db.config_db_service.get_project", side_effect=RuntimeError("DB error")):
            config = builder._extract_node_config("proj_1", "n1")

        assert config == {}

    def test_object_based_nodes(self):
        """Nodes as objects with .id and .data attributes."""
        builder = DebugPayloadBuilder()
        canvas = SimpleNamespace(
            nodes=[
                SimpleNamespace(id="n1", type="estrai", data={
                    "label": "Test",
                    "connection_id": "c1",
                }),
            ],
            edges=[],
        )
        project = _mock_project(canvas=canvas)

        with patch("app.db.config_db_service.get_project", return_value=project):
            config = builder._extract_node_config("proj_1", "n1")

        assert "connection_id" in config
        assert "label" not in config

    def test_node_with_none_data_returns_empty_config(self):
        """Node with data=None returns empty dict for that node's config."""
        builder = DebugPayloadBuilder()
        canvas = {
            "nodes": [{"id": "n1", "type": "a", "data": None}],
            "edges": [],
        }
        project = _mock_project(canvas=canvas)

        with patch("app.db.config_db_service.get_project", return_value=project):
            config = builder._extract_node_config("proj_1", "n1")

        assert config == {}
