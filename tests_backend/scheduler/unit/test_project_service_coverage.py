"""
tests/scheduler/unit/test_project_service_coverage.py
=====================================================
Unit tests for app.services.project_service — CRUD, canvas, results,
group isolation.

Mocked: app.db.db_service.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit

from app.services.project_service import ProjectService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project_row(**overrides) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    defaults = dict(
        id="proj_Test_20260101_000000_abcd1234",
        name="Test Project",
        description="A test project",
        project_type="estrai",
        connection_id="conn_1",
        group_id="default",
        canvas={"nodes": [], "edges": []},
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return defaults


# =============================================================================
# _generate_project_id
# =============================================================================

class TestGenerateProjectId:

    def test_format(self):
        svc = ProjectService()
        pid = svc._generate_project_id("My Project")
        assert "My_Project" in pid

    def test_special_chars_stripped(self):
        svc = ProjectService()
        pid = svc._generate_project_id("t@st!#proj")
        assert "@" not in pid

    def test_truncation(self):
        svc = ProjectService()
        pid = svc._generate_project_id("A" * 100)
        # Name portion should be truncated to 30 chars
        name_part = pid.split("_")[0]
        assert len(name_part) <= 30


# =============================================================================
# _row_to_metadata
# =============================================================================

class TestRowToMetadata:

    def test_full_row(self):
        svc = ProjectService()
        row = _make_project_row()
        meta = svc._row_to_metadata(row)
        assert meta is not None
        assert meta.id == row["id"]
        assert meta.name == "Test Project"
        assert meta.project_type == "estrai"

    def test_none_row(self):
        svc = ProjectService()
        result = svc._row_to_metadata(None)
        assert result is None

    def test_missing_optional_fields(self):
        svc = ProjectService()
        row = _make_project_row()
        del row["description"]
        del row["project_type"]
        del row["connection_id"]
        meta = svc._row_to_metadata(row)
        assert meta.description == ""
        assert meta.project_type == "estrai"


# =============================================================================
# CRUD — create_project
# =============================================================================

class TestCreateProject:

    @patch("app.services.project_service.db")
    def test_creates_project(self, mock_db):
        from app.models import ProjectCreate
        svc = ProjectService()
        mock_db.get_group.return_value = {"id": "default"}
        mock_db.create_project.return_value = _make_project_row()

        data = ProjectCreate(name="Test", project_type="estrai")
        result = svc.create_project(data)
        assert result is not None
        assert result.name == "Test Project"
        mock_db.create_project.assert_called_once()

    @patch("app.services.project_service.db")
    def test_creates_default_group_if_missing(self, mock_db):
        from app.models import ProjectCreate
        svc = ProjectService()
        mock_db.get_group.return_value = None
        mock_db.create_project.return_value = _make_project_row()

        data = ProjectCreate(name="Test", project_type="estrai")
        svc.create_project(data)
        mock_db.create_group.assert_called_once()


# =============================================================================
# CRUD — list_projects
# =============================================================================

class TestListProjects:

    @patch("app.services.project_service.db")
    def test_returns_project_list(self, mock_db):
        svc = ProjectService()
        mock_db.get_group.return_value = {"id": "default"}
        mock_db.list_projects.return_value = [
            _make_project_row(id="p1"),
            _make_project_row(id="p2"),
        ]
        result = svc.list_projects()
        assert len(result.projects) == 2

    @patch("app.services.project_service.db")
    def test_empty_list(self, mock_db):
        svc = ProjectService()
        mock_db.get_group.return_value = {"id": "default"}
        mock_db.list_projects.return_value = []
        result = svc.list_projects()
        assert result.projects == []

    @patch("app.services.project_service.db")
    def test_filters_none_rows(self, mock_db):
        svc = ProjectService()
        mock_db.get_group.return_value = {"id": "default"}
        mock_db.list_projects.return_value = [_make_project_row(), None]
        result = svc.list_projects()
        assert len(result.projects) == 1


# =============================================================================
# CRUD — get_project_metadata
# =============================================================================

class TestGetProjectMetadata:

    @patch("app.services.project_service.db")
    def test_found(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = _make_project_row()
        result = svc.get_project_metadata("proj_id")
        assert result is not None

    @patch("app.services.project_service.db")
    def test_not_found(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = None
        result = svc.get_project_metadata("nonexistent")
        assert result is None


# =============================================================================
# get_project_canvas
# =============================================================================

class TestGetProjectCanvas:

    @patch("app.services.project_service.db")
    def test_returns_canvas(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = _make_project_row(
            canvas={"nodes": [], "edges": []}
        )
        result = svc.get_project_canvas("proj_id")
        assert result is not None
        assert result.nodes == []

    @patch("app.services.project_service.db")
    def test_canvas_from_json_string(self, mock_db):
        import json
        svc = ProjectService()
        mock_db.get_project.return_value = _make_project_row(
            canvas=json.dumps({"nodes": [], "edges": []})
        )
        result = svc.get_project_canvas("proj_id")
        assert result is not None

    @patch("app.services.project_service.db")
    def test_project_not_found(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = None
        result = svc.get_project_canvas("missing")
        assert result is None


# =============================================================================
# get_project (full: metadata + canvas)
# =============================================================================

class TestGetProject:

    @patch("app.services.project_service.db")
    def test_returns_full_project(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = _make_project_row()
        result = svc.get_project("proj_id")
        assert result is not None
        assert "metadata" in result
        assert "canvas" in result
        assert result["id"] == "proj_id"

    @patch("app.services.project_service.db")
    def test_not_found(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = None
        result = svc.get_project("missing")
        assert result is None


# =============================================================================
# update_canvas
# =============================================================================

class TestUpdateCanvas:

    @patch("app.services.project_service.db")
    def test_update_success(self, mock_db):
        from app.models import CanvasState
        svc = ProjectService()
        mock_db.get_project.return_value = _make_project_row()

        canvas = CanvasState(nodes=[], edges=[])
        result = svc.update_canvas("proj_id", canvas)
        assert result is True
        mock_db.update_project.assert_called_once()

    @patch("app.services.project_service.db")
    def test_update_not_found(self, mock_db):
        from app.models import CanvasState
        svc = ProjectService()
        mock_db.get_project.return_value = None

        result = svc.update_canvas("missing", CanvasState(nodes=[], edges=[]))
        assert result is False


# =============================================================================
# delete_project
# =============================================================================

class TestDeleteProject:

    @patch("app.services.project_service.db")
    def test_delete_success(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = _make_project_row()

        with patch("app.core.intermediate.db.delete_all_runs_for_canvas", return_value=2):
            with patch("app.db.results_db_service.delete_results_for_project", return_value=3):
                result = svc.delete_project("proj_id")

        assert result is True
        mock_db.delete_project.assert_called_once()

    @patch("app.services.project_service.db")
    def test_delete_not_found(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = None
        result = svc.delete_project("missing")
        assert result is False

    @patch("app.services.project_service.db")
    def test_delete_intermediate_cleanup_failure_non_blocking(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = _make_project_row()

        with patch("app.core.intermediate.db.delete_all_runs_for_canvas", side_effect=Exception("boom")):
            with patch("app.db.results_db_service.delete_results_for_project", return_value=0):
                result = svc.delete_project("proj_id")

        assert result is True  # cleanup failure is non-blocking

    @patch("app.services.project_service.db")
    def test_delete_with_group_isolation(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = _make_project_row(group_id="team_a")

        with patch("app.core.intermediate.db.delete_all_runs_for_canvas", return_value=0):
            with patch("app.db.results_db_service.delete_results_for_project", return_value=0):
                result = svc.delete_project("proj_id", group_id="team_a")

        assert result is True
        # Verify group_id was passed
        mock_db.get_project.assert_called_with("proj_id", group_id="team_a")


# =============================================================================
# project_exists
# =============================================================================

class TestProjectExists:

    @patch("app.services.project_service.db")
    def test_exists(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = _make_project_row()
        assert svc.project_exists("proj_id") is True

    @patch("app.services.project_service.db")
    def test_not_exists(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = None
        assert svc.project_exists("missing") is False


# =============================================================================
# update_project_columns_metadata
# =============================================================================

class TestUpdateColumnsMetadata:

    @patch("app.services.project_service.db")
    def test_update_success(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = _make_project_row(
            canvas={"nodes": [], "edges": []}
        )
        result = svc.update_project_columns_metadata(
            "proj_id", [{"name": "col1", "type": "int"}]
        )
        assert result is True
        mock_db.update_project.assert_called_once()

    @patch("app.services.project_service.db")
    def test_update_not_found(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.return_value = None
        result = svc.update_project_columns_metadata("missing", [])
        assert result is False

    @patch("app.services.project_service.db")
    def test_update_with_json_string_canvas(self, mock_db):
        import json
        svc = ProjectService()
        mock_db.get_project.return_value = _make_project_row(
            canvas=json.dumps({"nodes": [], "edges": []})
        )
        result = svc.update_project_columns_metadata("proj_id", [])
        assert result is True

    @patch("app.services.project_service.db")
    def test_update_exception_returns_false(self, mock_db):
        svc = ProjectService()
        mock_db.get_project.side_effect = RuntimeError("boom")
        result = svc.update_project_columns_metadata("proj_id", [])
        assert result is False


# =============================================================================
# save_result
# =============================================================================

class TestSaveResult:

    @patch("app.services.project_service.db")
    def test_save_returns_id(self, mock_db):
        svc = ProjectService()
        result_id = svc.save_result(
            project_id="proj1",
            filename="out.csv",
            fmt="csv",
            data=b"col1,col2\n1,2",
            rows_count=1,
        )
        assert isinstance(result_id, str)
        assert len(result_id) > 0
        mock_db.save_result.assert_called_once()


# =============================================================================
# get_latest_result
# =============================================================================

class TestGetLatestResult:

    @patch("app.services.project_service.db")
    def test_returns_result(self, mock_db):
        svc = ProjectService()
        mock_db.get_latest_result.return_value = {"id": "r1", "format": "csv"}
        result = svc.get_latest_result("proj1")
        assert result is not None
        assert result["id"] == "r1"

    @patch("app.services.project_service.db")
    def test_returns_none(self, mock_db):
        svc = ProjectService()
        mock_db.get_latest_result.return_value = None
        assert svc.get_latest_result("proj1") is None


# =============================================================================
# list_results
# =============================================================================

class TestListResults:

    @patch("app.services.project_service.db")
    def test_returns_list(self, mock_db):
        svc = ProjectService()
        mock_db.list_results.return_value = [
            {"id": "r1"}, {"id": "r2"}
        ]
        results = svc.list_results("proj1")
        assert len(results) == 2

    @patch("app.services.project_service.db")
    def test_returns_empty_list(self, mock_db):
        svc = ProjectService()
        mock_db.list_results.return_value = []
        assert svc.list_results("proj1") == []
