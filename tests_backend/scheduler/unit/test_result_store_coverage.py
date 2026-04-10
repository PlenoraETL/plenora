"""
tests/scheduler/unit/test_result_store_coverage.py
===================================================
Unit tests for app.utils.result_store — get_temp_output_path,
save_result_blob, get_result_as_tempfile, get_result_info,
_apply_results_retention.

Mocked: app.db.db_service, app.db.results_db_service,
        app.db.config_db_service, filesystem.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit

from app.utils.result_store import (
    get_temp_output_path,
    save_result_blob,
    get_result_as_tempfile,
    get_result_info,
    _apply_results_retention,
    DEFAULT_RESULTS_RETENTION,
)


# =============================================================================
# get_temp_output_path
# =============================================================================

class TestGetTempOutputPath:

    @patch("app.config.settings")
    def test_returns_path_in_temp_dir(self, mock_settings):
        mock_settings.DATA_DIR = "/data"
        path = get_temp_output_path("output.csv")
        assert path.name == "output.csv"
        assert "temp" in str(path)

    @patch("app.config.settings")
    def test_unique_dir_per_call(self, mock_settings):
        mock_settings.DATA_DIR = "/data"
        p1 = get_temp_output_path("a.csv")
        p2 = get_temp_output_path("a.csv")
        # Different temp subdirectories
        assert p1.parent != p2.parent


# =============================================================================
# _apply_results_retention
# =============================================================================

class TestApplyResultsRetention:

    def test_no_deletion_under_limit(self):
        with patch("app.db.config_db_service.get_project_retention_n", return_value=5):
            with patch("app.db.results_db_service.list_results", return_value=[
                {"id": "r1"}, {"id": "r2"}, {"id": "r3"},
            ]):
                with patch("app.db.results_db_service.delete_result") as mock_del:
                    _apply_results_retention("proj1")
                    mock_del.assert_not_called()

    @patch("app.utils.result_store.logger")
    def test_deletes_oldest_beyond_limit(self, mock_logger):
        results = [{"id": f"r{i}"} for i in range(15)]
        with patch("app.db.config_db_service.get_project_retention_n", return_value=10):
            with patch("app.db.results_db_service.list_results", return_value=results):
                with patch("app.db.results_db_service.delete_result") as mock_del:
                    _apply_results_retention("proj1")
                    assert mock_del.call_count == 5  # 15 - 10

    @patch("app.utils.result_store.logger")
    def test_uses_default_retention_if_none(self, mock_logger):
        results = [{"id": f"r{i}"} for i in range(DEFAULT_RESULTS_RETENTION + 3)]
        with patch("app.db.config_db_service.get_project_retention_n", return_value=None):
            with patch("app.db.results_db_service.list_results", return_value=results):
                with patch("app.db.results_db_service.delete_result") as mock_del:
                    _apply_results_retention("proj1")
                    assert mock_del.call_count == 3

    def test_exception_is_caught(self):
        with patch("app.db.config_db_service.get_project_retention_n", side_effect=RuntimeError("boom")):
            # Should not raise
            _apply_results_retention("proj1")


# =============================================================================
# save_result_blob
# =============================================================================

class TestSaveResultBlob:

    def test_saves_and_returns_id(self, tmp_path):
        # Create a temp file to simulate saved output
        file_path = tmp_path / "output.csv"
        file_path.write_text("col1,col2\n1,2")

        with patch("app.db.db_service.save_result") as mock_save:
            with patch("app.utils.result_store._apply_results_retention"):
                with patch("app.config.settings") as mock_settings:
                    mock_settings.DEFAULT_GROUP = "default"
                    result_id = save_result_blob(
                        project_id="proj1",
                        path=file_path,
                        fmt="csv",
                        rows_count=1,
                        group_id="default",
                    )
        assert isinstance(result_id, str)
        assert len(result_id) > 0
        mock_save.assert_called_once()

    def test_cleans_up_temp_file(self, tmp_path):
        file_path = tmp_path / "output.csv"
        file_path.write_text("data")

        with patch("app.db.db_service.save_result"):
            with patch("app.utils.result_store._apply_results_retention"):
                with patch("app.config.settings") as mock_settings:
                    mock_settings.DEFAULT_GROUP = "default"
                    save_result_blob("proj1", file_path, "csv", group_id="g1")

        # File should be removed after save
        assert not file_path.exists()

    def test_resolves_group_from_project(self, tmp_path):
        file_path = tmp_path / "output.csv"
        file_path.write_text("data")

        with patch("app.db.db_service.save_result"):
            with patch("app.db.db_service.get_project", return_value={"group_id": "team_a"}):
                with patch("app.utils.result_store._apply_results_retention"):
                    with patch("app.config.settings") as mock_settings:
                        mock_settings.DEFAULT_GROUP = "default"
                        result_id = save_result_blob(
                            project_id="proj1",
                            path=file_path,
                            fmt="csv",
                            group_id=None,
                        )
        assert len(result_id) > 0

    def test_error_returns_empty_string(self, tmp_path):
        file_path = tmp_path / "output.csv"
        file_path.write_text("data")

        with patch("app.db.db_service.save_result", side_effect=RuntimeError("db error")):
            with patch("app.config.settings") as mock_settings:
                mock_settings.DEFAULT_GROUP = "default"
                result_id = save_result_blob("proj1", file_path, "csv", group_id="g")
        assert result_id == ""


# =============================================================================
# get_result_as_tempfile
# =============================================================================

class TestGetResultAsTempfile:

    @patch("app.config.settings")
    @patch("app.db.db_service.get_result_data")
    @patch("app.db.db_service.get_latest_result")
    def test_returns_temp_file(self, mock_latest, mock_data, mock_settings, tmp_path):
        mock_settings.DATA_DIR = str(tmp_path)
        mock_latest.return_value = {"id": "r1", "format": "csv"}
        mock_data.return_value = b"col1,col2\n1,2"

        result = get_result_as_tempfile("proj1")
        assert result is not None
        assert result.exists()
        assert result.suffix == ".csv"
        # Cleanup
        result.unlink()

    @patch("app.db.db_service.get_latest_result")
    def test_no_result_returns_none(self, mock_latest):
        mock_latest.return_value = None
        result = get_result_as_tempfile("proj1")
        assert result is None

    @patch("app.db.db_service.get_result_data")
    @patch("app.db.db_service.get_latest_result")
    def test_no_data_returns_none(self, mock_latest, mock_data):
        mock_latest.return_value = {"id": "r1", "format": "csv"}
        mock_data.return_value = None
        result = get_result_as_tempfile("proj1")
        assert result is None

    @patch("app.db.db_service.get_latest_result")
    def test_exception_returns_none(self, mock_latest):
        mock_latest.side_effect = RuntimeError("db error")
        result = get_result_as_tempfile("proj1")
        assert result is None


# =============================================================================
# get_result_info
# =============================================================================

class TestGetResultInfo:

    @patch("app.db.db_service.get_latest_result")
    def test_returns_info(self, mock_latest):
        mock_latest.return_value = {"id": "r1", "format": "csv", "rows_count": 42}
        result = get_result_info("proj1")
        assert result is not None
        assert result["rows_count"] == 42

    @patch("app.db.db_service.get_latest_result")
    def test_returns_none_if_no_result(self, mock_latest):
        mock_latest.return_value = None
        result = get_result_info("proj1")
        assert result is None

    @patch("app.db.db_service.get_latest_result")
    def test_exception_returns_none(self, mock_latest):
        mock_latest.side_effect = RuntimeError("db error")
        result = get_result_info("proj1")
        assert result is None


# =============================================================================
# DEFAULT_RESULTS_RETENTION
# =============================================================================

class TestDefaults:

    def test_default_retention_is_10(self):
        assert DEFAULT_RESULTS_RETENTION == 10
