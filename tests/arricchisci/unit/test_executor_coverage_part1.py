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
# __init__ — lines 43-44
# =============================================================================

class TestEnrichExecutorInit:

    def test_init_stores_project_service(self):
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        assert executor.project_service is svc

    def test_init_sets_results_base_dir(self):
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        assert executor.results_base_dir is not None


# =============================================================================
# ImportError branch — lines 32-33
# =============================================================================

class TestImportErrorBranch:

    def test_use_result_store_false_when_import_fails(self):
        """When app.utils.result_store is missing, _USE_RESULT_STORE is False."""
        import app.core.arricchisci.executor as executor_mod

        # Temporarily hide result_store from sys.modules
        saved = sys.modules.get("app.utils.result_store")
        sys.modules["app.utils.result_store"] = None  # forces ImportError

        try:
            importlib.reload(executor_mod)
            assert executor_mod._USE_RESULT_STORE is False
        finally:
            # Restore
            if saved is not None:
                sys.modules["app.utils.result_store"] = saved
            else:
                sys.modules.pop("app.utils.result_store", None)
            # Re-reload to restore normal state
            importlib.reload(executor_mod)


# =============================================================================
# execute() — success with output_type="file" (lines 72-83)
# =============================================================================

class TestExecuteFileOutput:

    def test_output_type_file_returns_file_result(self):
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {
            "success": True,
            "output_type": "file",
            "output_path": "/tmp/result.gpkg",
            "output_filename": "result.gpkg",
            "output_size_bytes": 12345,
            "execution_time_ms": 500,
            "message": "Completato",
        }

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step"):
            mock_api.execute_enrichment.return_value = api_result
            result = executor.execute("proj-1", canvas)

        assert result["success"] is True
        assert result["output_type"] == "file"
        assert result["output_path"] == "/tmp/result.gpkg"
        assert result["output_filename"] == "result.gpkg"
        assert result["output_size_bytes"] == 12345
        assert result["rows_count"] == 0
        assert result["columns"] == []

    def test_output_type_file_message(self):
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {
            "success": True,
            "output_type": "file",
            "output_path": "/tmp/out.csv",
            "message": "Custom message",
        }

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step"):
            mock_api.execute_enrichment.return_value = api_result
            result = executor.execute("proj-1", canvas)

        assert result["message"] == "Custom message"


# =============================================================================
# execute() — success with output_path derivation (lines 85-113)
# =============================================================================

class TestExecuteNonFileOutput:

    def test_success_with_direct_output_path(self):
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {
            "success": True,
            "output_path": "/tmp/results/proj-1/output.csv",
            "rows_processed": 100,
            "columns": ["col1", "col2"],
        }

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step"):
            mock_api.execute_enrichment.return_value = api_result
            result = executor.execute("proj-1", canvas)

        assert result["success"] is True
        assert result["output_path"] == "/tmp/results/proj-1/output.csv"
        assert result["rows_count"] == 100
        assert result["columns"] == ["col1", "col2"]
        assert "100" in result["message"]

    def test_output_path_from_output_file_with_slash(self):
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {
            "success": True,
            "output_file": "/absolute/path/output.csv",
            "rows_processed": 50,
            "columns": [],
        }

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step"):
            mock_api.execute_enrichment.return_value = api_result
            result = executor.execute("proj-1", canvas)

        assert result["output_path"] == "/absolute/path/output.csv"

    def test_output_path_from_plain_output_file(self):
        """Plain filename builds path from results_base_dir / project_id / filename."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {
            "success": True,
            "output_file": "output.csv",
            "rows_processed": 10,
            "columns": [],
        }

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step"):
            mock_api.execute_enrichment.return_value = api_result
            result = executor.execute("proj-1", canvas)

        assert "proj-1" in result["output_path"]
        assert "output.csv" in result["output_path"]


# =============================================================================
# execute() — _USE_RESULT_STORE + save_result_blob (lines 93-105)
# =============================================================================

class TestExecuteResultStore:

    def test_result_store_save_called(self, tmp_path):
        """_save_result_blob called when _USE_RESULT_STORE=True and file exists."""
        # Create a real temp file so _Path(output_path).exists() returns True
        output_file = tmp_path / "output.csv"
        output_file.write_text("a,b\n1,2\n")

        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {
            "success": True,
            "output_path": str(output_file),
            "rows_processed": 10,
            "columns": [],
        }

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step"), \
             patch("app.core.arricchisci.executor._USE_RESULT_STORE", True), \
             patch("app.core.arricchisci.executor._save_result_blob") as mock_save:
            mock_api.execute_enrichment.return_value = api_result
            result = executor.execute("proj-1", canvas)

        mock_save.assert_called_once()
        call_args = mock_save.call_args
        assert call_args[0][0] == "proj-1"
        # format derived from .csv suffix
        assert call_args[0][2] == "csv"
        assert call_args[1]["group_id"] == "grp-1"

    def test_result_store_not_called_when_file_missing(self):
        """_save_result_blob NOT called when file does not exist."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {
            "success": True,
            "output_path": "/tmp/nonexistent_abc123.csv",
            "rows_processed": 10,
            "columns": [],
        }

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step"), \
             patch("app.core.arricchisci.executor._USE_RESULT_STORE", True), \
             patch("app.core.arricchisci.executor._save_result_blob") as mock_save:
            mock_api.execute_enrichment.return_value = api_result
            result = executor.execute("proj-1", canvas)

        mock_save.assert_not_called()

    def test_result_store_metadata_exception_swallowed(self, tmp_path):
        """Exception getting project metadata is silently caught; group_id=None."""
        output_file = tmp_path / "output.csv"
        output_file.write_text("x\n1\n")

        svc = make_project_service()
        svc.get_project_metadata.side_effect = RuntimeError("DB error")
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {
            "success": True,
            "output_path": str(output_file),
            "rows_processed": 5,
            "columns": [],
        }

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step"), \
             patch("app.core.arricchisci.executor._USE_RESULT_STORE", True), \
             patch("app.core.arricchisci.executor._save_result_blob") as mock_save:
            mock_api.execute_enrichment.return_value = api_result
            result = executor.execute("proj-1", canvas)

        mock_save.assert_called_once()
        assert mock_save.call_args[1]["group_id"] is None

    def test_result_store_metadata_returns_none(self, tmp_path):
        """get_project_metadata returns None -> group_id stays None."""
        output_file = tmp_path / "output.gpkg"
        output_file.write_text("binary")

        svc = make_project_service()
        svc.get_project_metadata.return_value = None
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {
            "success": True,
            "output_path": str(output_file),
            "rows_processed": 3,
            "columns": [],
        }

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step"), \
             patch("app.core.arricchisci.executor._USE_RESULT_STORE", True), \
             patch("app.core.arricchisci.executor._save_result_blob") as mock_save:
            mock_api.execute_enrichment.return_value = api_result
            result = executor.execute("proj-1", canvas)

        mock_save.assert_called_once()
        # format from .gpkg suffix
        assert mock_save.call_args[0][2] == "gpkg"
        assert mock_save.call_args[1]["group_id"] is None


# =============================================================================
# execute() — failure result (lines 114-118)
# =============================================================================

class TestExecuteFailure:

    def test_failure_returns_error_message(self):
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {
            "success": False,
            "error": "API connection failed",
        }

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step"):
            mock_api.execute_enrichment.return_value = api_result
            result = executor.execute("proj-1", canvas)

        assert result["success"] is False
        assert result["message"] == "API connection failed"

    def test_failure_default_error_message(self):
        """Missing 'error' key uses default Italian message."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {"success": False}

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step"):
            mock_api.execute_enrichment.return_value = api_result
            result = executor.execute("proj-1", canvas)

        assert result["success"] is False
        assert result["message"] == "Errore durante l'arricchimento"


# =============================================================================
# execute() — step tracking (lines 121-128)
# =============================================================================

class TestExecuteStepTracking:

    def test_success_step_tracking(self):
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {
            "success": True,
            "output_path": "/tmp/out.csv",
            "rows_processed": 10,
            "columns": [],
        }

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step") as mock_step:
            mock_api.execute_enrichment.return_value = api_result
            executor.execute("proj-1", canvas)

        mock_step.assert_called_once()
        step_data = mock_step.call_args[0][0]
        assert step_data["module"] == "arricchisci"
        assert step_data["status"] == "success"
        assert step_data["rows_out"] == 10
        assert step_data["error"] is None
        assert isinstance(step_data["duration_ms"], int)

    def test_failure_step_tracking(self):
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {"success": False, "error": "boom"}

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step") as mock_step:
            mock_api.execute_enrichment.return_value = api_result
            executor.execute("proj-1", canvas)

        step_data = mock_step.call_args[0][0]
        assert step_data["status"] == "failure"
        assert step_data["error"] == "boom"

    def test_format_and_run_id_passed(self):
        """format and run_id are forwarded to api_connection_service."""
        svc = make_project_service()
        executor = EnrichExecutor(svc)
        canvas = make_canvas()

        api_result = {"success": False, "error": "x"}

        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_api, \
             patch("app.core.arricchisci.executor.append_step"):
            mock_api.execute_enrichment.return_value = api_result
            executor.execute("proj-1", canvas, format="gpkg", run_id="run-abc")

        call_kwargs = mock_api.execute_enrichment.call_args[1]
        assert call_kwargs["format"] == "gpkg"
        assert call_kwargs["run_id"] == "run-abc"
