"""
tests/arricchisci/unit/test_api_connector_coverage.py
=====================================================
Additional unit tests to cover missing lines in api_connector.py.

Missing lines targeted:
  132-148, 153-162, 166-204, 208-234, 238-242, 263-348, 380, 490,
  558, 579-580, 584, 602-607, 616, 630-631, 637-652, 673-677, 680,
  684-687, 707, 768, 772, 776, 780-852, 856-873, 881-893,
  921-1156, 1168-1223, 1250-1406, 1428, 1439, 1451-1452,
  1502-1597
"""

import pytest
import json
import pandas as pd
from unittest.mock import patch, MagicMock, PropertyMock
from pathlib import Path
from datetime import datetime

pytestmark = pytest.mark.unit

from app.core.arricchisci.api_connector import ApiConnectionService, _nd


# ─── Helper ──────────────────────────────────────────────────────────────────

def _make_svc():
    """Create an ApiConnectionService without calling __init__."""
    with patch("app.core.arricchisci.api_connector.settings") as mock_settings:
        mock_settings.DATA_DIR = Path("/tmp")
        mock_settings.RESULTS_DIR = Path("/tmp")
        mock_settings.API_CONNECTIONS_DIR = Path("/tmp")
        mock_settings.DEFAULT_GROUP = "default"
        svc = ApiConnectionService.__new__(ApiConnectionService)
        svc.data_dir = Path("/tmp")
        svc.results_dir = Path("/tmp")
        svc.api_connections_dir = Path("/tmp")
    return svc


@pytest.fixture
def svc():
    return _make_svc()




# =============================================================================
# execute_enrichment — high-level orchestration (lines 558, 579-580, 584,
#   602-607, 616, 630-631, 637-652, 673-677, 680, 684-687, 707)
# =============================================================================

class TestExecuteEnrichment:

    def _make_canvas(self, nodes, edges):
        return {"nodes": nodes, "edges": edges}

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_empty_canvas_returns_error(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = []
        result = svc.execute_enrichment("proj1", {"nodes": [], "edges": []})
        assert result["success"] is False
        assert "Nessun nodo" in result["error"]

    @patch("app.core.arricchisci.api_connector.save_result_blob")
    @patch("app.core.arricchisci.api_connector.get_temp_output_path")
    @patch("app.core.arricchisci.api_connector.save_dataframe")
    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_no_result_generated_returns_error(self, mock_settings, mock_db,
                                                mock_save_df, mock_temp, mock_blob):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = []

        canvas = self._make_canvas(
            nodes=[{"id": "n1", "type": "unknown_type"}],
            edges=[],
        )
        result = svc.execute_enrichment("proj1", canvas)
        assert result["success"] is False
        assert "Nessun risultato" in result["error"]

    @patch("app.core.arricchisci.api_connector.save_result_blob")
    @patch("app.core.arricchisci.api_connector.get_temp_output_path")
    @patch("app.core.arricchisci.api_connector.save_dataframe")
    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_api_enrich_missing_api_returns_error(self, mock_settings, mock_db,
                                                   mock_save_df, mock_temp, mock_blob):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = []
        mock_db.get_api_connection.return_value = None

        canvas = self._make_canvas(
            nodes=[
                {"id": "src", "type": "file_source", "file_source_id": "fs_1"},
                {"id": "api", "type": "api_enrich", "api_id": "nonexistent"},
            ],
            edges=[{"source": "src", "target": "api"}],
        )
        with patch.object(svc, "_load_file_source", return_value=pd.DataFrame({"a": [1]})):
            result = svc.execute_enrichment("proj1", canvas)
            assert result["success"] is False
            assert "non trovata" in result["error"]

    @patch("app.core.arricchisci.api_connector.save_result_blob")
    @patch("app.core.arricchisci.api_connector.get_temp_output_path", return_value=Path("/tmp/out.csv"))
    @patch("app.core.arricchisci.api_connector.save_dataframe", return_value=Path("/tmp/out.csv"))
    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_successful_dataframe_result(self, mock_settings, mock_db,
                                          mock_save_df, mock_temp, mock_blob):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        # Setup api connections
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_1", "api_type": "enricher", "created_at": "2026-01-01"}},
        ]
        mock_db.get_api_connection.return_value = {
            "config": {
                "id": "api_1",
                "api_type": "enricher",
                "endpoint": {"url": "http://api.test", "method": "GET"},
                "auth": {"type": "none"},
                "custom_headers": {},
                "static_parameters": {},
                "output_mapping": [],
                "options": {},
            },
            "group_id": "grp1",
        }

        result_df = pd.DataFrame({"city": ["Rome"], "lat": [41.9]})

        canvas = self._make_canvas(
            nodes=[
                {"id": "src", "type": "file_source", "file_source_id": "fs_1"},
                {"id": "api", "type": "api_enrich", "api_id": "api_1", "param_mapping": {}},
                {"id": "res", "type": "result", "format": "csv", "fileName": "output"},
            ],
            edges=[
                {"source": "src", "target": "api"},
                {"source": "api", "target": "res"},
            ],
        )
        with patch.object(svc, "_load_file_source", return_value=pd.DataFrame({"city": ["Rome"]})), \
             patch.object(svc, "_execute_api_enricher", return_value=result_df):
            result = svc.execute_enrichment("proj1", canvas)
            assert result["success"] is True
            assert result["rows"] == 1
            assert "city" in result["columns"]

    @patch("app.core.arricchisci.api_connector.save_result_blob")
    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_enricher_no_input_edge_returns_error(self, mock_settings, mock_db, mock_blob):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_1", "api_type": "enricher", "created_at": "2026-01-01"}},
        ]
        mock_db.get_api_connection.return_value = {
            "config": {
                "id": "api_1", "api_type": "enricher",
                "endpoint": {"url": "http://test", "method": "GET"},
                "auth": {"type": "none"},
            },
            "group_id": "grp1",
        }

        canvas = self._make_canvas(
            nodes=[{"id": "api", "type": "api_enrich", "api_id": "api_1"}],
            edges=[],
        )
        result = svc.execute_enrichment("proj1", canvas)
        assert result["success"] is False
        assert "non ha input" in result["error"]

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_general_exception_returns_error(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.side_effect = RuntimeError("db crash")

        canvas = self._make_canvas(
            nodes=[{"id": "n1", "type": "file_source"}],
            edges=[],
        )
        result = svc.execute_enrichment("proj1", canvas)
        assert result["success"] is False
        assert "db crash" in result["error"]

    @patch("app.core.arricchisci.api_connector.save_result_blob")
    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_file_result_from_executable(self, mock_settings, mock_db, mock_blob):
        """Executable result returns file-based result dict."""
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_1", "connection_type": "executable", "created_at": "2026-01-01"}},
        ]
        mock_db.get_api_connection.return_value = {
            "config": {
                "id": "api_1",
                "connection_type": "executable",
                "executable": {"type": "python", "path": "/s.py"},
            },
            "group_id": "grp1",
        }

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            f.write("a,b\n1,2")
            tmp_path = f.name

        try:
            file_result = {
                "is_file_result": True,
                "success": True,
                "output_path": tmp_path,
                "execution_time_ms": 100,
                "metadata": {},
            }

            canvas = self._make_canvas(
                nodes=[
                    {"id": "src", "type": "file_source", "file_source_id": "fs_1"},
                    {"id": "api", "type": "api_enrich", "api_id": "api_1"},
                    {"id": "res", "type": "result"},
                ],
                edges=[
                    {"source": "src", "target": "api"},
                    {"source": "api", "target": "res"},
                ],
            )

            with patch.object(svc, "_load_file_source") as mock_load, \
                 patch.object(svc, "_execute_executable", return_value=file_result):
                mock_load.return_value = pd.DataFrame({"a": [1]})
                result = svc.execute_enrichment("proj1", canvas)
                assert result["success"] is True
                assert result["output_type"] == "file"
        finally:
            os.unlink(tmp_path)

    @patch("app.core.arricchisci.api_connector.save_result_blob")
    @patch("app.core.arricchisci.api_connector.get_temp_output_path", return_value=Path("/tmp/out.csv"))
    @patch("app.core.arricchisci.api_connector.save_dataframe", return_value=Path("/tmp/out.csv"))
    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_generator_api_type(self, mock_settings, mock_db,
                                 mock_save_df, mock_temp, mock_blob):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_1", "api_type": "generator", "created_at": "2026-01-01"}},
        ]
        mock_db.get_api_connection.return_value = {
            "config": {
                "id": "api_1", "api_type": "generator",
                "endpoint": {"url": "http://test", "method": "GET"},
                "auth": {"type": "none"},
            },
            "group_id": "grp1",
        }

        canvas = self._make_canvas(
            nodes=[
                {"id": "api", "type": "api_enrich", "api_id": "api_1"},
                {"id": "res", "type": "result", "format": "csv"},
            ],
            edges=[{"source": "api", "target": "res"}],
        )
        gen_df = pd.DataFrame({"name": ["Italy"]})
        with patch.object(svc, "_execute_api_generator", return_value=gen_df):
            result = svc.execute_enrichment("proj1", canvas)
            assert result["success"] is True
            assert result["rows"] == 1

    @patch("app.core.arricchisci.api_connector.save_result_blob")
    @patch("app.core.arricchisci.api_connector.get_temp_output_path", return_value=Path("/tmp/out.csv"))
    @patch("app.core.arricchisci.api_connector.save_dataframe", return_value=Path("/tmp/out.csv"))
    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_generator_with_static_values_input(self, mock_settings, mock_db,
                                                  mock_save_df, mock_temp, mock_blob):
        """Generator node with StaticValues input passes input_df."""
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_1", "api_type": "generator", "created_at": "2026-01-01"}},
        ]
        mock_db.get_api_connection.return_value = {
            "config": {
                "id": "api_1", "api_type": "generator",
                "endpoint": {"url": "http://test", "method": "GET"},
                "auth": {"type": "none"},
            },
            "group_id": "grp1",
        }

        canvas = self._make_canvas(
            nodes=[
                {"id": "sv", "type": "staticValues", "data": {"fields": [{"name": "q", "value": "Rome"}]}},
                {"id": "api", "type": "api_enrich", "api_id": "api_1"},
                {"id": "res", "type": "result", "format": "csv"},
            ],
            edges=[
                {"source": "sv", "target": "api"},
                {"source": "api", "target": "res"},
            ],
        )
        gen_df = pd.DataFrame({"name": ["Rome"]})
        with patch("app.core.arricchisci.api_connector.process_static_fields", return_value={"q": "Rome"}), \
             patch.object(svc, "_execute_api_generator", return_value=gen_df) as mock_gen:
            result = svc.execute_enrichment("proj1", canvas)
            assert result["success"] is True
            # Generator was called with input_df from StaticValues
            call_args = mock_gen.call_args
            assert call_args[0][1] is not None  # input_df not None

    @patch("app.core.arricchisci.api_connector.save_result_blob")
    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_enricher_valueerror_returns_error(self, mock_settings, mock_db, mock_blob):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_1", "api_type": "enricher", "created_at": "2026-01-01"}},
        ]
        mock_db.get_api_connection.return_value = {
            "config": {
                "id": "api_1", "api_type": "enricher",
                "endpoint": {"url": "http://test", "method": "GET"},
                "auth": {"type": "none"},
            },
            "group_id": "grp1",
        }

        canvas = self._make_canvas(
            nodes=[
                {"id": "src", "type": "file_source", "file_source_id": "fs_1"},
                {"id": "api", "type": "api_enrich", "api_id": "api_1"},
            ],
            edges=[{"source": "src", "target": "api"}],
        )
        with patch.object(svc, "_load_file_source", return_value=pd.DataFrame({"a": [1]})), \
             patch.object(svc, "_execute_api_enricher", side_effect=ValueError("URL mismatch")):
            result = svc.execute_enrichment("proj1", canvas)
            assert result["success"] is False
            assert "URL mismatch" in result["error"]

    @patch("app.core.arricchisci.api_connector.save_result_blob")
    @patch("app.core.arricchisci.api_connector.get_temp_output_path", return_value=Path("/tmp/out.csv"))
    @patch("app.core.arricchisci.api_connector.save_dataframe", return_value=Path("/tmp/out.csv"))
    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_result_node_propagates_input(self, mock_settings, mock_db,
                                           mock_save_df, mock_temp, mock_blob):
        """Result node takes the DataFrame from its input edge."""
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = []

        df = pd.DataFrame({"x": [1, 2, 3]})
        canvas = self._make_canvas(
            nodes=[
                {"id": "src", "type": "file_source", "file_source_id": "fs_1"},
                {"id": "res", "type": "result", "format": "csv", "fileName": "test_out"},
            ],
            edges=[{"source": "src", "target": "res"}],
        )
        with patch.object(svc, "_load_file_source", return_value=df):
            result = svc.execute_enrichment("proj1", canvas)
            assert result["success"] is True
            assert result["rows"] == 3

    @patch("app.core.arricchisci.api_connector.save_result_blob")
    @patch("app.core.arricchisci.api_connector.get_temp_output_path", return_value=Path("/tmp/out.csv"))
    @patch("app.core.arricchisci.api_connector.save_dataframe", return_value=Path("/tmp/out.csv"))
    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_fallback_to_last_node_result(self, mock_settings, mock_db,
                                           mock_save_df, mock_temp, mock_blob):
        """When result node has no data, falls back to last non-passthrough result."""
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = []

        df = pd.DataFrame({"x": [1]})
        canvas = self._make_canvas(
            nodes=[
                {"id": "src", "type": "file_source", "file_source_id": "fs_1"},
            ],
            edges=[],
        )
        with patch.object(svc, "_load_file_source", return_value=df):
            result = svc.execute_enrichment("proj1", canvas)
            assert result["success"] is True
            assert result["rows"] == 1

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_non_dataframe_final_result_returns_error(self, mock_settings, mock_db):
        """When final result is not a DataFrame and not a file result, error."""
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = []

        canvas = self._make_canvas(
            nodes=[
                {"id": "sv", "type": "staticValues", "data": {"fields": [{"name": "x", "value": "1"}]}},
                {"id": "res", "type": "result"},
            ],
            edges=[{"source": "sv", "target": "res"}],
        )
        with patch("app.core.arricchisci.api_connector.process_static_fields", return_value={"x": "1"}), \
             patch.object(svc, "_load_static_values", return_value="not a dataframe"):
            result = svc.execute_enrichment("proj1", canvas)
            assert result["success"] is False
            assert "Nessun DataFrame" in result["error"]

    @patch("app.core.arricchisci.api_connector.save_result_blob")
    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_executable_failure_propagated(self, mock_settings, mock_db, mock_blob):
        """When executable returns success=False, the error is propagated."""
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_1", "connection_type": "executable", "created_at": "2026-01-01"}},
        ]
        mock_db.get_api_connection.return_value = {
            "config": {
                "id": "api_1",
                "connection_type": "executable",
                "executable": {"type": "python", "path": "/s.py"},
            },
            "group_id": "grp1",
        }

        canvas = self._make_canvas(
            nodes=[
                {"id": "src", "type": "file_source", "file_source_id": "fs_1"},
                {"id": "api", "type": "api_enrich", "api_id": "api_1"},
            ],
            edges=[{"source": "src", "target": "api"}],
        )
        exec_fail = {"success": False, "error": "script crashed"}
        with patch.object(svc, "_load_file_source", return_value=pd.DataFrame({"a": [1]})), \
             patch.object(svc, "_execute_executable", return_value=exec_fail):
            result = svc.execute_enrichment("proj1", canvas)
            assert result["success"] is False
            assert "script crashed" in result["error"]

    @patch("app.core.arricchisci.api_connector.save_result_blob")
    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_file_source_passthrough_for_executable(self, mock_settings, mock_db, mock_blob):
        """Line 579-584: file_source for executable downstream creates passthrough marker."""
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_1", "connection_type": "executable", "created_at": "2026-01-01"}},
        ]
        mock_db.get_api_connection.return_value = {
            "config": {
                "id": "api_1",
                "connection_type": "executable",
                "executable": {"type": "python", "path": "/s.py"},
            },
            "group_id": "grp1",
        }

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            f.write("a,b\n1,2")
            tmp_path = f.name

        try:
            file_result = {
                "is_file_result": True,
                "success": True,
                "output_path": tmp_path,
                "execution_time_ms": 50,
                "metadata": {},
            }

            canvas = self._make_canvas(
                nodes=[
                    {"id": "src", "type": "file_source", "file_source_id": "fs_1"},
                    {"id": "api", "type": "api_enrich", "api_id": "api_1"},
                    {"id": "res", "type": "result"},
                ],
                edges=[
                    {"source": "src", "target": "api"},
                    {"source": "api", "target": "res"},
                ],
            )
            with patch.object(svc, "_execute_executable", return_value=file_result):
                result = svc.execute_enrichment("proj1", canvas)
                assert result["success"] is True
                assert result["output_type"] == "file"
        finally:
            os.unlink(tmp_path)
