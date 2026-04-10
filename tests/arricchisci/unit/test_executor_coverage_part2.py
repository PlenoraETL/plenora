"""
tests/arricchisci/unit/test_executor_coverage.py
=================================================
Coverage-oriented tests for executor.py (EnrichExecutor) targeting uncovered lines.

Lines to cover: 32-33, 43-44, 97-105, 153-285

Patch strategy
--------------
executor.py uses LOCAL imports inside execute() and preview():
  - ``from app.core.arricchisci.api_connector import api_connection_service``
  - ``from app.core.arricchisci.enrichment_service import EnrichmentExecutor``
  - ``from app.core.arricchisci.models import EnrichmentOptions``
  - ``from app.utils.dataframe import sanitize_for_json``

Because these are local imports, they re-read the source module attribute
each time. We must patch the attribute **on the source module**, NOT on
``app.core.arricchisci.executor``.

Module-level names that CAN be patched on executor:
  - ``append_step``, ``_USE_RESULT_STORE``, ``_save_result_blob``, ``settings``
"""

import sys
import importlib
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock
from pathlib import Path

pytestmark = pytest.mark.unit

from app.core.arricchisci.executor import EnrichExecutor


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_project_service():
    svc = MagicMock()
    svc.get_project_metadata.return_value = MagicMock(group_id="grp-1")
    return svc


def make_canvas(nodes=None, edges=None):
    """Return a MagicMock that behaves like CanvasState."""
    canvas = MagicMock()
    canvas.model_dump.return_value = {
        "nodes": nodes or [],
        "edges": edges or [],
    }
    return canvas




# =============================================================================
# preview() — lines 153-285
# =============================================================================

class TestPreview:

    def _make_canvas_for_preview(self, node_types=None):
        """Build a canvas mock with given node chain."""
        if node_types is None:
            node_types = ["file_source", "api_enrich", "result"]

        nodes = []
        edges = []
        for i, nt in enumerate(node_types):
            node = {"id": f"node-{i}", "type": nt, "data": {}}
            if nt == "file_source":
                node["file_source_id"] = "file-1"
            elif nt == "api_enrich":
                node["api_id"] = "api-1"
                node["param_mapping"] = {"q": "addr"}
            elif nt == "staticValues":
                node["values"] = [{"key": "lang", "value": "it"}]
            elif nt == "project_source":
                node["source_project_id"] = "proj-src"
            nodes.append(node)

        for i in range(len(nodes) - 1):
            edges.append({"id": f"e-{i}", "source": f"node-{i}", "target": f"node-{i+1}"})

        canvas = MagicMock()
        canvas.model_dump.return_value = {"nodes": nodes, "edges": edges}
        return canvas

    def test_preview_empty_nodes(self):
        """Empty canvas returns failure."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = make_canvas(nodes=[], edges=[])

        result = executor.preview("proj-1", canvas)
        assert result["success"] is False
        assert "nodo" in result["message"].lower() or "nessun" in result["message"].lower()

    def test_preview_exception_returns_failure(self):
        """Unhandled exception in preview returns failure with message."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        # Canvas with nodes so we pass the empty check, but api_connection_service
        # will raise
        canvas = self._make_canvas_for_preview(["file_source", "result"])

        with patch(
            "app.core.arricchisci.api_connector.api_connection_service",
            new=MagicMock(),
        ) as mock_api:
            mock_api.list_api_connections.side_effect = RuntimeError("crash")
            result = executor.preview("proj-1", canvas)

        assert result["success"] is False
        assert "crash" in result["message"]

    def test_preview_file_source_enricher_result(self):
        """Full preview flow: file_source -> api_enrich (enricher) -> result."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = self._make_canvas_for_preview(["file_source", "api_enrich", "result"])

        input_df = pd.DataFrame({"addr": [f"addr_{i}" for i in range(20)]})
        result_df = input_df.head(10).copy()
        result_df["lat"] = 41.9

        api_config = {
            "id": "api-1",
            "name": "Test",
            "api_type": "enricher",
            "endpoint": {"url": "http://api.test.com", "method": "GET"},
            "auth": {"type": "none"},
            "output_mapping": [{"path": "lat", "column": "lat"}],
            "options": {},
        }

        mock_executor_instance = MagicMock()
        mock_executor_instance.execute.return_value = (result_df, MagicMock())

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.enrichment_service.EnrichmentExecutor",
                   return_value=mock_executor_instance), \
             patch("app.utils.dataframe.sanitize_for_json", side_effect=lambda x: x):
            mock_api.list_api_connections.return_value = [{"id": "api-1"}]
            mock_api.get_api_connection.return_value = api_config
            mock_api._load_file_source.return_value = input_df
            mock_api._get_execution_order.return_value = ["node-0", "node-1", "node-2"]

            result = executor.preview("proj-1", canvas, limit=10)

        assert result["success"] is True
        assert "columns" in result
        assert "rows" in result
        assert result["total_count"] == 10

    def test_preview_generator_api(self):
        """Preview with generator API (no input DataFrame)."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = self._make_canvas_for_preview(["api_enrich", "result"])

        gen_df = pd.DataFrame({"city": ["Rome", "Milan"], "pop": [2800000, 1400000]})

        api_config = {
            "id": "api-1",
            "name": "Generator",
            "api_type": "generator",
            "endpoint": {"url": "http://gen.test.com", "method": "GET"},
            "auth": {"type": "none"},
            "output_mapping": [],
            "options": {},
        }

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.utils.dataframe.sanitize_for_json", side_effect=lambda x: x):
            mock_api.list_api_connections.return_value = [{"id": "api-1"}]
            mock_api.get_api_connection.return_value = api_config
            mock_api._execute_api_generator.return_value = gen_df
            mock_api._get_execution_order.return_value = ["node-0", "node-1"]

            result = executor.preview("proj-1", canvas, limit=10)

        assert result["success"] is True
        assert result["total_count"] == 2

    def test_preview_generator_returns_dict_sets_none(self):
        """Generator returning dict (error) sets result_df to None -> failure."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = self._make_canvas_for_preview(["api_enrich", "result"])

        api_config = {
            "id": "api-1",
            "name": "Generator",
            "api_type": "generator",
            "endpoint": {"url": "http://gen.test.com", "method": "GET"},
            "auth": {"type": "none"},
            "output_mapping": [],
            "options": {},
        }

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api:
            mock_api.list_api_connections.return_value = [{"id": "api-1"}]
            mock_api.get_api_connection.return_value = api_config
            mock_api._execute_api_generator.return_value = {"error": "failed"}
            mock_api._get_execution_order.return_value = ["node-0", "node-1"]

            result = executor.preview("proj-1", canvas, limit=10)

        assert result["success"] is False

    def test_preview_static_values_node(self):
        """staticValues node is processed via _load_static_values."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = self._make_canvas_for_preview(["staticValues", "api_enrich", "result"])

        static_df = pd.DataFrame({"lang": ["it"]})
        enriched_df = pd.DataFrame({"lang": ["it"], "city": ["Rome"]})

        api_config = {
            "id": "api-1",
            "name": "Test",
            "api_type": "enricher",
            "endpoint": {"url": "http://api.test.com", "method": "GET"},
            "auth": {"type": "none"},
            "output_mapping": [{"path": "city", "column": "city"}],
            "options": {},
        }

        mock_executor_instance = MagicMock()
        mock_executor_instance.execute.return_value = (enriched_df, MagicMock())

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.enrichment_service.EnrichmentExecutor",
                   return_value=mock_executor_instance), \
             patch("app.utils.dataframe.sanitize_for_json", side_effect=lambda x: x):
            mock_api.list_api_connections.return_value = [{"id": "api-1"}]
            mock_api.get_api_connection.return_value = api_config
            mock_api._load_static_values.return_value = static_df
            mock_api._get_execution_order.return_value = ["node-0", "node-1", "node-2"]

            result = executor.preview("proj-1", canvas, limit=10)

        assert result["success"] is True

    def test_preview_project_source_node(self):
        """project_source node is processed via _load_source_project."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = self._make_canvas_for_preview(["project_source", "api_enrich", "result"])

        proj_df = pd.DataFrame({"addr": ["Via Roma 1"]})
        enriched_df = pd.DataFrame({"addr": ["Via Roma 1"], "lat": [41.9]})

        api_config = {
            "id": "api-1",
            "name": "Test",
            "api_type": "enricher",
            "endpoint": {"url": "http://api.test.com", "method": "GET"},
            "auth": {"type": "none"},
            "output_mapping": [{"path": "lat", "column": "lat"}],
            "options": {},
        }

        mock_executor_instance = MagicMock()
        mock_executor_instance.execute.return_value = (enriched_df, MagicMock())

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.enrichment_service.EnrichmentExecutor",
                   return_value=mock_executor_instance), \
             patch("app.utils.dataframe.sanitize_for_json", side_effect=lambda x: x):
            mock_api.list_api_connections.return_value = [{"id": "api-1"}]
            mock_api.get_api_connection.return_value = api_config
            mock_api._load_source_project.return_value = proj_df
            mock_api._get_execution_order.return_value = ["node-0", "node-1", "node-2"]

            result = executor.preview("proj-1", canvas, limit=10)

        assert result["success"] is True

    def test_preview_no_data_produced(self):
        """No result DataFrame produces failure."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = self._make_canvas_for_preview(["file_source", "result"])

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api:
            mock_api.list_api_connections.return_value = []
            mock_api._get_execution_order.return_value = ["node-0", "node-1"]
            mock_api._load_file_source.return_value = None

            result = executor.preview("proj-1", canvas, limit=10)

        assert result["success"] is False
        assert "nessun dato" in result["message"].lower()

    def test_preview_api_not_found_skips_node(self):
        """api_enrich node with missing API config is skipped (continue)."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = self._make_canvas_for_preview(["file_source", "api_enrich", "result"])

        input_df = pd.DataFrame({"addr": ["test"]})

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api:
            # No API connections loaded -> api_connections dict is empty
            mock_api.list_api_connections.return_value = []
            mock_api.get_api_connection.return_value = None
            mock_api._load_file_source.return_value = input_df
            mock_api._get_execution_order.return_value = ["node-0", "node-1", "node-2"]

            result = executor.preview("proj-1", canvas, limit=10)

        # No enrichment happened, result_df stays None
        assert result["success"] is False

    def test_preview_column_type_detection(self):
        """Column type detection: int, real, bool, date, geometry, string."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)

        nodes = [
            {"id": "node-0", "type": "file_source", "file_source_id": "f1", "data": {}},
            {"id": "node-1", "type": "result", "data": {}},
        ]
        edges = [{"id": "e-0", "source": "node-0", "target": "node-1"}]
        canvas = MagicMock()
        canvas.model_dump.return_value = {"nodes": nodes, "edges": edges}

        result_df = pd.DataFrame({
            "name": ["Rome"],
            "population": [2800000],
            "latitude": [41.9],
            "active": [True],
            "created": pd.to_datetime(["2024-01-01"]),
            "geometry": ["POINT (12.4 41.9)"],
        })

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.utils.dataframe.sanitize_for_json", side_effect=lambda x: x):
            mock_api.list_api_connections.return_value = []
            mock_api._get_execution_order.return_value = ["node-0", "node-1"]
            mock_api._load_file_source.return_value = result_df

            result = executor.preview("proj-1", canvas, limit=10)

        assert result["success"] is True
        col_map = {c["name"]: c["type"] for c in result["columns"]}
        assert col_map["name"] == "string"
        assert col_map["population"] == "int"
        assert col_map["latitude"] == "real"
        assert col_map["active"] == "bool"
        assert col_map["created"] == "date"
        assert col_map["geometry"] == "geometry"

    def test_preview_truncated_flag(self):
        """truncated=True when total_count > limit."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)

        nodes = [
            {"id": "node-0", "type": "file_source", "file_source_id": "f1", "data": {}},
            {"id": "node-1", "type": "result", "data": {}},
        ]
        edges = [{"id": "e-0", "source": "node-0", "target": "node-1"}]
        canvas = MagicMock()
        canvas.model_dump.return_value = {"nodes": nodes, "edges": edges}

        result_df = pd.DataFrame({"val": list(range(50))})

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.utils.dataframe.sanitize_for_json", side_effect=lambda x: x):
            mock_api.list_api_connections.return_value = []
            mock_api._get_execution_order.return_value = ["node-0", "node-1"]
            mock_api._load_file_source.return_value = result_df

            result = executor.preview("proj-1", canvas, limit=5)

        assert result["truncated"] is True
        assert result["total_count"] == 50
        assert result["preview_count"] == 5

    def test_preview_geojson_geometry_string_detection(self):
        """GeoJSON string in object column detected as geometry."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)

        nodes = [
            {"id": "n0", "type": "file_source", "file_source_id": "f1", "data": {}},
            {"id": "n1", "type": "result", "data": {}},
        ]
        edges = [{"id": "e0", "source": "n0", "target": "n1"}]
        canvas = MagicMock()
        canvas.model_dump.return_value = {"nodes": nodes, "edges": edges}

        result_df = pd.DataFrame({
            "geo": ['{"type": "Point", "coordinates": [12.4, 41.9]}'],
        })

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.utils.dataframe.sanitize_for_json", side_effect=lambda x: x):
            mock_api.list_api_connections.return_value = []
            mock_api._get_execution_order.return_value = ["n0", "n1"]
            mock_api._load_file_source.return_value = result_df

            result = executor.preview("proj-1", canvas, limit=10)

        assert result["success"] is True
        col_map = {c["name"]: c["type"] for c in result["columns"]}
        assert col_map["geo"] == "geometry"

    def test_preview_geometry_column_name_detection(self):
        """Column named 'geom' detected as geometry regardless of content."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)

        nodes = [
            {"id": "n0", "type": "file_source", "file_source_id": "f1", "data": {}},
            {"id": "n1", "type": "result", "data": {}},
        ]
        edges = [{"id": "e0", "source": "n0", "target": "n1"}]
        canvas = MagicMock()
        canvas.model_dump.return_value = {"nodes": nodes, "edges": edges}

        result_df = pd.DataFrame({
            "geom": ["some_binary_data"],
        })

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.utils.dataframe.sanitize_for_json", side_effect=lambda x: x):
            mock_api.list_api_connections.return_value = []
            mock_api._get_execution_order.return_value = ["n0", "n1"]
            mock_api._load_file_source.return_value = result_df

            result = executor.preview("proj-1", canvas, limit=10)

        assert result["success"] is True
        col_map = {c["name"]: c["type"] for c in result["columns"]}
        assert col_map["geom"] == "geometry"

    def test_preview_empty_result_df(self):
        """Empty DataFrame returns failure."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)

        nodes = [
            {"id": "n0", "type": "file_source", "file_source_id": "f1", "data": {}},
            {"id": "n1", "type": "result", "data": {}},
        ]
        edges = [{"id": "e0", "source": "n0", "target": "n1"}]
        canvas = MagicMock()
        canvas.model_dump.return_value = {"nodes": nodes, "edges": edges}

        empty_df = pd.DataFrame()

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api:
            mock_api.list_api_connections.return_value = []
            mock_api._get_execution_order.return_value = ["n0", "n1"]
            mock_api._load_file_source.return_value = empty_df

            result = executor.preview("proj-1", canvas, limit=10)

        assert result["success"] is False

    def test_preview_node_not_found_in_map(self):
        """Node ID in execution_order but not in node_map is skipped."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)

        nodes = [
            {"id": "n0", "type": "file_source", "file_source_id": "f1", "data": {}},
            {"id": "n1", "type": "result", "data": {}},
        ]
        edges = [{"id": "e0", "source": "n0", "target": "n1"}]
        canvas = MagicMock()
        canvas.model_dump.return_value = {"nodes": nodes, "edges": edges}

        result_df = pd.DataFrame({"x": [1, 2, 3]})

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.utils.dataframe.sanitize_for_json", side_effect=lambda x: x):
            mock_api.list_api_connections.return_value = []
            # Include a phantom node ID that does not exist in node_map
            mock_api._get_execution_order.return_value = ["phantom", "n0", "n1"]
            mock_api._load_file_source.return_value = result_df

            result = executor.preview("proj-1", canvas, limit=10)

        assert result["success"] is True

    def test_preview_not_truncated_when_within_limit(self):
        """truncated=False when total_count <= limit."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)

        nodes = [
            {"id": "n0", "type": "file_source", "file_source_id": "f1", "data": {}},
            {"id": "n1", "type": "result", "data": {}},
        ]
        edges = [{"id": "e0", "source": "n0", "target": "n1"}]
        canvas = MagicMock()
        canvas.model_dump.return_value = {"nodes": nodes, "edges": edges}

        result_df = pd.DataFrame({"val": [1, 2, 3]})

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.utils.dataframe.sanitize_for_json", side_effect=lambda x: x):
            mock_api.list_api_connections.return_value = []
            mock_api._get_execution_order.return_value = ["n0", "n1"]
            mock_api._load_file_source.return_value = result_df

            result = executor.preview("proj-1", canvas, limit=10)

        assert result["truncated"] is False
        assert result["total_count"] == 3
        assert result["preview_count"] == 3
