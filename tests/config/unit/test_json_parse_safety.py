"""
tests/config/unit/test_json_parse_safety.py
============================================
Unit test per il Blocco C: difesa da JSON corrotto in 5 siti.

Verifica che i seguenti loader di JSON dal DB non sollevino 500 ma usino
fallback safe + log warning su input malformato:

  1. ProjectService.get_project_canvas
  2. db.config_db_service.get_project (canvas)
  3. DataSourceService._get_full_config
  4. SchemaInferenceService._infer_project_source_schema
"""

import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_reset():
    yield


# ============================================================================
# 1. ProjectService.get_project_canvas
# ============================================================================

class TestProjectServiceGetCanvas:

    def test_corrupted_canvas_returns_empty_state(self):
        from app.services.project_service import ProjectService
        from app.shared.models import CanvasState
        svc = ProjectService()
        # Simula un row con canvas come stringa JSON malformata
        with patch("app.services.project_service.db.get_project") as mock_get:
            mock_get.return_value = {
                "id": "proj-1",
                "canvas": "{not valid json[}",
            }
            result = svc.get_project_canvas("proj-1")
        assert isinstance(result, CanvasState)
        assert result.nodes == []
        assert result.edges == []

    def test_valid_canvas_string_parsed(self):
        from app.services.project_service import ProjectService
        svc = ProjectService()
        with patch("app.services.project_service.db.get_project") as mock_get:
            mock_get.return_value = {
                "id": "proj-1",
                "canvas": '{"nodes": [], "edges": []}',
            }
            result = svc.get_project_canvas("proj-1")
        assert result is not None
        assert result.nodes == []

    def test_canvas_already_dict_works(self):
        """Caso normale: canvas già parsato come dict (path comune)."""
        from app.services.project_service import ProjectService
        svc = ProjectService()
        with patch("app.services.project_service.db.get_project") as mock_get:
            mock_get.return_value = {
                "id": "proj-1",
                "canvas": {"nodes": [], "edges": []},
            }
            result = svc.get_project_canvas("proj-1")
        assert result is not None

    def test_canvas_unexpected_type_returns_empty(self):
        """Se canvas è una lista o altro tipo strano → fallback empty."""
        from app.services.project_service import ProjectService
        from app.shared.models import CanvasState
        svc = ProjectService()
        with patch("app.services.project_service.db.get_project") as mock_get:
            mock_get.return_value = {"id": "proj-1", "canvas": [1, 2, 3]}
            result = svc.get_project_canvas("proj-1")
        assert isinstance(result, CanvasState)
        assert result.nodes == []

    def test_canvas_with_invalid_schema_returns_empty(self):
        """Dict valido ma schema CanvasState invalido → fallback empty."""
        from app.services.project_service import ProjectService
        from app.shared.models import CanvasState
        svc = ProjectService()
        with patch("app.services.project_service.db.get_project") as mock_get:
            # nodes non è una lista — Pydantic solleverà ValidationError
            mock_get.return_value = {
                "id": "proj-1",
                "canvas": {"nodes": "not-a-list", "edges": []},
            }
            result = svc.get_project_canvas("proj-1")
        assert isinstance(result, CanvasState)
        assert result.nodes == []

    def test_project_not_found_returns_none(self):
        from app.services.project_service import ProjectService
        svc = ProjectService()
        with patch("app.services.project_service.db.get_project") as mock_get:
            mock_get.return_value = None
            result = svc.get_project_canvas("nonexistent")
        assert result is None


# ============================================================================
# 2. db.config_db_service.get_project — canvas parsing
# ============================================================================

class TestConfigDbGetProjectCanvas:

    def test_corrupted_canvas_falls_back_to_empty(self):
        from app.db.config_db_service import get_project
        with patch("app.db.config_db_service.fetchone") as mock_fetch:
            mock_fetch.return_value = {
                "id": "proj-1",
                "name": "Test",
                "canvas": "{broken json",
            }
            row = get_project("proj-1")
        assert row is not None
        assert row["canvas"] == {"nodes": [], "edges": []}

    def test_valid_canvas_parsed(self):
        from app.db.config_db_service import get_project
        with patch("app.db.config_db_service.fetchone") as mock_fetch:
            mock_fetch.return_value = {
                "id": "proj-1",
                "canvas": '{"nodes": [{"id": "n1"}], "edges": []}',
            }
            row = get_project("proj-1")
        assert row["canvas"]["nodes"][0]["id"] == "n1"

    def test_no_canvas_field_unchanged(self):
        """Row senza canvas (es. legacy): non tocca nulla."""
        from app.db.config_db_service import get_project
        with patch("app.db.config_db_service.fetchone") as mock_fetch:
            mock_fetch.return_value = {"id": "proj-1", "name": "X"}
            row = get_project("proj-1")
        assert "canvas" not in row or not row.get("canvas")

    def test_project_not_found(self):
        from app.db.config_db_service import get_project
        with patch("app.db.config_db_service.fetchone") as mock_fetch:
            mock_fetch.return_value = None
            row = get_project("nonexistent")
        assert row is None


# ============================================================================
# 3. DataSourceService._get_full_config
# ============================================================================

class TestDataSourceFullConfig:

    def test_corrupted_config_returns_none(self):
        from app.core.datasources.service import DataSourceService
        svc = DataSourceService()
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds-1",
                "config": "{not valid json[",
                "group_id": "g1",
            }
            result = svc._get_full_config("ds-1")
        assert result is None

    def test_valid_config_string_parsed(self):
        from app.core.datasources.service import DataSourceService
        svc = DataSourceService()
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds-1",
                "config": '{"host": "ftp.example.com", "port": 21}',
                "group_id": "g1",
                "created_by": "u1",
            }
            result = svc._get_full_config("ds-1")
        assert result is not None
        assert result["host"] == "ftp.example.com"
        assert result["port"] == 21
        # Inietta group_id e created_by dal record DB
        assert result["group_id"] == "g1"
        assert result["created_by"] == "u1"

    def test_config_dict_already_parsed(self):
        from app.core.datasources.service import DataSourceService
        svc = DataSourceService()
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds-1",
                "config": {"host": "x", "port": 22},
                "group_id": "g1",
                "created_by": None,
            }
            result = svc._get_full_config("ds-1")
        assert result is not None
        assert result["host"] == "x"

    def test_config_unexpected_type_returns_none(self):
        from app.core.datasources.service import DataSourceService
        svc = DataSourceService()
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds-1",
                "config": [1, 2, 3],  # lista invece di dict/string
                "group_id": "g1",
            }
            result = svc._get_full_config("ds-1")
        assert result is None

    def test_datasource_not_found_returns_none(self):
        from app.core.datasources.service import DataSourceService
        svc = DataSourceService()
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = None
            result = svc._get_full_config("nonexistent")
        assert result is None


# ============================================================================
# 4. SchemaInferenceService._infer_project_source_schema
# ============================================================================

class TestSchemaInferenceProjectSource:

    def _make_node(self, source_project_id="proj-1"):
        node = MagicMock()
        node.id = "n1"
        node.type = "project_source"
        node.source_project_id = source_project_id
        node.data = {"source_project_id": source_project_id}
        node.columns = None
        return node

    def test_corrupted_canvas_returns_empty(self):
        from app.services.schema_inference import SchemaInferenceService
        svc = SchemaInferenceService(project_service=MagicMock())
        node = self._make_node("proj-src")
        with patch("app.db.db_service.get_project") as mock_get:
            mock_get.return_value = {
                "id": "proj-src",
                "canvas": "{broken[",
            }
            result = svc._infer_project_source_schema(node)
        assert result == []

    def test_valid_canvas_with_columns_metadata(self):
        from app.services.schema_inference import SchemaInferenceService
        svc = SchemaInferenceService(project_service=MagicMock())
        node = self._make_node("proj-src")
        with patch("app.db.db_service.get_project") as mock_get:
            mock_get.return_value = {
                "id": "proj-src",
                "canvas": '{"columns_metadata": [{"name": "col1", "type": "str"}]}',
            }
            result = svc._infer_project_source_schema(node)
        assert len(result) == 1
        assert result[0].name == "col1"

    def test_canvas_unexpected_type_returns_empty(self):
        from app.services.schema_inference import SchemaInferenceService
        svc = SchemaInferenceService(project_service=MagicMock())
        node = self._make_node("proj-src")
        with patch("app.db.db_service.get_project") as mock_get:
            mock_get.return_value = {"id": "proj-src", "canvas": "not a dict"}
            result = svc._infer_project_source_schema(node)
        assert result == []

    def test_source_project_not_found_returns_empty(self):
        from app.services.schema_inference import SchemaInferenceService
        svc = SchemaInferenceService(project_service=MagicMock())
        node = self._make_node("nonexistent")
        with patch("app.db.db_service.get_project") as mock_get:
            mock_get.return_value = None
            result = svc._infer_project_source_schema(node)
        assert result == []
