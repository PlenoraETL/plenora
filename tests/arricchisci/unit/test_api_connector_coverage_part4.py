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
# _resolve_file_source_path  (lines 1168-1223)
# =============================================================================

class TestResolveFileSourcePath:

    def test_returns_none_when_no_input_edge(self):
        svc = _make_svc()
        result = svc._resolve_file_source_path([], [], "n1")
        assert result is None

    def test_returns_none_when_source_node_not_found(self):
        svc = _make_svc()
        edges = [{"source": "n0", "target": "n1"}]
        nodes = [{"id": "n1", "type": "api_enrich"}]
        result = svc._resolve_file_source_path(nodes, edges, "n1")
        assert result is None

    @patch("app.config.settings")
    def test_file_source_returns_file_path(self, mock_settings):
        svc = _make_svc()
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a fake file
            data_dir = Path(tmpdir) / "data"
            data_dir.mkdir()
            test_file = data_dir / "input.csv"
            test_file.write_text("a,b\n1,2")

            mock_settings.get_file_source_dir.return_value = data_dir

            nodes = [
                {"id": "n0", "type": "file_source", "file_source_id": "fs_123"},
                {"id": "n1", "type": "api_enrich"},
            ]
            edges = [{"source": "n0", "target": "n1"}]
            result = svc._resolve_file_source_path(nodes, edges, "n1")
            assert result is not None
            assert "input.csv" in result

    @patch("app.config.settings")
    def test_file_source_skips_metadata_json(self, mock_settings):
        svc = _make_svc()
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            meta = data_dir / "metadata.json"
            meta.write_text("{}")
            real_file = data_dir / "data.csv"
            real_file.write_text("a\n1")

            mock_settings.get_file_source_dir.return_value = data_dir

            nodes = [
                {"id": "n0", "type": "file_source", "file_source_id": "fs_123"},
                {"id": "n1", "type": "api_enrich"},
            ]
            edges = [{"source": "n0", "target": "n1"}]
            result = svc._resolve_file_source_path(nodes, edges, "n1")
            assert result is not None
            assert "metadata.json" not in result

    @patch("app.core.arricchisci.api_connector.settings")
    def test_file_source_returns_none_when_no_file_source_id(self, mock_settings):
        svc = _make_svc()
        nodes = [
            {"id": "n0", "type": "file_source"},
            {"id": "n1", "type": "api_enrich"},
        ]
        edges = [{"source": "n0", "target": "n1"}]
        result = svc._resolve_file_source_path(nodes, edges, "n1")
        assert result is None

    def test_project_source_returns_tempfile(self):
        svc = _make_svc()
        nodes = [
            {"id": "n0", "type": "project_source", "source_project_id": "proj_1"},
            {"id": "n1", "type": "api_enrich"},
        ]
        edges = [{"source": "n0", "target": "n1"}]

        with patch("app.utils.result_store.get_result_as_tempfile") as mock_blob:
            mock_blob.return_value = "/tmp/proj_1_output.csv"
            result = svc._resolve_file_source_path(nodes, edges, "n1")
            assert result == "/tmp/proj_1_output.csv"

    def test_project_source_returns_none_when_blob_fails(self):
        svc = _make_svc()
        nodes = [
            {"id": "n0", "type": "project_source", "source_project_id": "proj_1"},
            {"id": "n1", "type": "api_enrich"},
        ]
        edges = [{"source": "n0", "target": "n1"}]

        with patch("app.utils.result_store.get_result_as_tempfile") as mock_blob:
            mock_blob.side_effect = Exception("fail")
            result = svc._resolve_file_source_path(nodes, edges, "n1")
            assert result is None

    def test_project_source_returns_none_when_no_project_id(self):
        svc = _make_svc()
        nodes = [
            {"id": "n0", "type": "project_source"},
            {"id": "n1", "type": "api_enrich"},
        ]
        edges = [{"source": "n0", "target": "n1"}]
        result = svc._resolve_file_source_path(nodes, edges, "n1")
        assert result is None

    def test_unknown_source_type_returns_none(self):
        svc = _make_svc()
        nodes = [
            {"id": "n0", "type": "unknown_type"},
            {"id": "n1", "type": "api_enrich"},
        ]
        edges = [{"source": "n0", "target": "n1"}]
        result = svc._resolve_file_source_path(nodes, edges, "n1")
        assert result is None

    @patch("app.config.settings")
    @patch("app.db.db_service.get_file_source")
    def test_file_source_blob_fallback(self, mock_get_fs, mock_settings):
        """B-BE-34: when file not on disk, extract from DB blob."""
        svc = _make_svc()
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "empty_dir"
            source_dir.mkdir()  # Empty dir, no files
            mock_settings.get_file_source_dir.return_value = source_dir

            mock_get_fs.return_value = {
                "data": b"a,b\n1,2",
                "original_filename": "test.csv",
            }

            nodes = [
                {"id": "n0", "type": "file_source", "file_source_id": "fs_123"},
                {"id": "n1", "type": "api_enrich"},
            ]
            edges = [{"source": "n0", "target": "n1"}]
            result = svc._resolve_file_source_path(nodes, edges, "n1")
            assert result is not None
            assert "test.csv" in result
