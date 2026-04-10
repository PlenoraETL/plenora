"""
tests/arricchisci/unit/test_executor_router.py
Fixed version - correct import names and patch paths.
"""
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from pathlib import Path

pytestmark = pytest.mark.unit

from app.core.arricchisci.executor import EnrichExecutor


def make_executor():
    ex = EnrichExecutor.__new__(EnrichExecutor)
    ex.project_service = MagicMock()
    ex.results_base_dir = MagicMock()
    return ex


def make_canvas():
    from app.models import CanvasState
    return CanvasState(nodes=[], edges=[])


class TestEnrichExecutorExecute:

    def test_success_dataframe(self):
        ex = make_executor()
        canvas = make_canvas()
        mock_result = {
            "success": True, "rows_processed": 10,
            "columns": ["paese", "capitale"],
            "output_path": "/tmp/output.csv",
        }
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.execute_enrichment.return_value = mock_result
            result = ex.execute("proj-1", canvas)
        assert result["success"] is True
        assert result["rows_count"] == 10

    def test_success_file_output(self):
        ex = make_executor()
        canvas = make_canvas()
        mock_result = {
            "success": True, "output_type": "file",
            "output_path": "/tmp/output.gpkg", "output_filename": "output.gpkg",
            "output_size_bytes": 1024, "rows_processed": 5, "execution_time_ms": 1500,
        }
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.execute_enrichment.return_value = mock_result
            result = ex.execute("proj-1", canvas)
        assert result["success"] is True
        assert result["output_type"] == "file"

    def test_failure(self):
        ex = make_executor()
        canvas = make_canvas()
        mock_result = {"success": False, "error": "API non raggiungibile"}
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.execute_enrichment.return_value = mock_result
            result = ex.execute("proj-1", canvas)
        assert result["success"] is False

    def test_output_path_da_output_file_nome(self):
        ex = make_executor()
        canvas = make_canvas()
        mock_result = {
            "success": True, "rows_processed": 5, "columns": [],
            "output_file": "output.csv",
        }
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc, \
             patch("app.core.arricchisci.executor._USE_RESULT_STORE", False):
            mock_svc.execute_enrichment.return_value = mock_result
            result = ex.execute("proj-1", canvas)
        assert result["success"] is True

    def test_output_path_da_output_file_path_completo(self):
        ex = make_executor()
        canvas = make_canvas()
        mock_result = {
            "success": True, "rows_processed": 3, "columns": [],
            "output_file": "/tmp/results/proj-1/output.csv",
        }
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc, \
             patch("app.core.arricchisci.executor._USE_RESULT_STORE", False):
            mock_svc.execute_enrichment.return_value = mock_result
            result = ex.execute("proj-1", canvas)
        assert result["success"] is True


class TestEnrichExecutorPreview:

    def test_preview_no_nodes_failure(self):
        ex = make_executor()
        canvas = make_canvas()
        result = ex.preview("proj-1", canvas, limit=10)
        assert result["success"] is False

    def test_preview_eccezione_gestita(self):
        ex = make_executor()
        canvas = make_canvas()
        result = ex.preview("proj-1", canvas, limit=10)
        assert result["success"] is False


class TestEnrichExecutorPreviewExtra:

    def test_preview_result_ha_success_key(self):
        ex = make_executor()
        canvas = make_canvas()
        result = ex.preview("proj-1", canvas, limit=5)
        assert "success" in result

    def test_preview_limit_diversi(self):
        ex = make_executor()
        canvas = make_canvas()
        for limit in [1, 5, 10]:
            result = ex.preview("proj-1", canvas, limit=limit)
            assert "success" in result


class TestEnrichExecutorExecuteExtra:

    def test_message_nel_result_failure(self):
        ex = make_executor()
        canvas = make_canvas()
        mock_result = {"success": False, "error": "timeout"}
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.execute_enrichment.return_value = mock_result
            result = ex.execute("proj-1", canvas)
        assert result.get("success") is False

    def test_execute_con_force_recalculate(self):
        ex = make_executor()
        canvas = make_canvas()
        mock_result = {"success": True, "rows_processed": 0, "columns": [], "output_path": "/tmp/out.csv"}
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.execute_enrichment.return_value = mock_result
            result = ex.execute("proj-1", canvas, force_recalculate=True)
        assert result["success"] is True

    def test_execute_con_format_gpkg(self):
        ex = make_executor()
        canvas = make_canvas()
        mock_result = {"success": True, "rows_processed": 5, "columns": [], "output_path": "/tmp/out.gpkg"}
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.execute_enrichment.return_value = mock_result
            result = ex.execute("proj-1", canvas, format="gpkg")
        assert result["success"] is True

    def test_rows_count_nel_result(self):
        ex = make_executor()
        canvas = make_canvas()
        mock_result = {"success": True, "rows_processed": 42, "columns": ["a"], "output_path": "/tmp/out.csv"}
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.execute_enrichment.return_value = mock_result
            result = ex.execute("proj-1", canvas)
        assert result["rows_count"] == 42
