"""
tests/arricchisci/unit/test_router_coverage.py

Unit tests targeting uncovered lines in app.core.arricchisci.router (lines 152-1015).
"""

import json
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch, PropertyMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

import app.core.arricchisci.router as R
from app.core.auth.auth import get_current_user, CurrentUser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_user():
    return CurrentUser(
        user_id="test-uid", username="test",
        group_id="default", role="superadmin",
    )


def _mock_user_non_admin():
    return CurrentUser(
        user_id="user2", username="viewer",
        group_id="group-b", role="viewer",
    )


def make_client(user_fn=None):
    """Build a TestClient with all sub-routers and mocked services."""
    app = FastAPI()
    app.dependency_overrides[get_current_user] = user_fn or _mock_user
    app.include_router(R.enrichment_router)
    app.include_router(R.api_connections_router)
    app.include_router(R.templates_router)
    app.include_router(R.executables_router)
    app.include_router(R.enrich_router)

    mock_prj = MagicMock()
    mock_prj.list_projects.return_value = []
    mock_api = MagicMock()
    R.init_arricchisci(mock_prj, mock_api)
    return TestClient(app, raise_server_exceptions=False), mock_prj, mock_api




# ---------------------------------------------------------------------------
# _format_time helper (lines 1008-1015)
# ---------------------------------------------------------------------------

class TestFormatTime:

    def test_seconds_only(self):
        assert R._format_time(45) == "45s"

    def test_zero_seconds(self):
        assert R._format_time(0) == "0s"

    def test_minutes_and_seconds(self):
        assert R._format_time(125) == "2m 5s"

    def test_exactly_60(self):
        assert R._format_time(60) == "1m 0s"

    def test_hours(self):
        assert R._format_time(3661) == "1h 1m"

    def test_exactly_3600(self):
        assert R._format_time(3600) == "1h 0m"


# ---------------------------------------------------------------------------
# _load_data helper (lines 957-982)
# ---------------------------------------------------------------------------

class TestLoadData:

    @patch("app.core.arricchisci.router._load_project_output")
    def test_load_data_with_project_id_only(self, mock_lpo):
        mock_lpo.return_value = pd.DataFrame({"a": [1]})
        result = R._load_data("proj-1", file_source_id=None)
        mock_lpo.assert_called_once_with("proj-1")
        assert len(result) == 1

    def test_load_data_no_project_no_file(self):
        result = R._load_data("", None)
        # empty string is falsy for project_id check
        assert result is None

    @patch("app.services.file_service.file_source_service")
    def test_load_data_file_source_success(self, mock_fs):
        df = pd.DataFrame({"x": [10, 20]})
        mock_fs.load_dataframe.return_value = (df, {})
        result = R._load_data("proj-1", file_source_id="file-1")
        assert len(result) == 2

    @patch("app.services.file_service.file_source_service")
    def test_load_data_file_source_failure_raises_400(self, mock_fs):
        mock_fs.load_dataframe.side_effect = RuntimeError("file not found")
        with pytest.raises(Exception) as exc_info:
            R._load_data("proj-1", file_source_id="file-bad")
        # Should raise HTTPException(400)
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# _load_project_output helper (lines 985-1005)
# ---------------------------------------------------------------------------

class TestLoadProjectOutput:

    @patch("app.utils.result_store.get_result_as_tempfile", return_value=None)
    def test_returns_none_when_no_tempfile(self, _):
        assert R._load_project_output("proj-x") is None

    @patch("os.path.exists", return_value=True)
    @patch("os.unlink")
    @patch("app.utils.dataframe.load_dataframe_from_file")
    @patch("app.utils.result_store.get_result_as_tempfile", return_value="/tmp/fake.csv")
    def test_returns_dataframe_and_cleans_up(self, mock_tmp, mock_load, mock_unlink, mock_exists):
        mock_load.return_value = pd.DataFrame({"a": [1]})
        result = R._load_project_output("proj-1")
        assert len(result) == 1
        mock_unlink.assert_called_once()

    @patch("os.path.exists", return_value=True)
    @patch("os.unlink")
    @patch("app.utils.dataframe.load_dataframe_from_file", side_effect=RuntimeError("bad"))
    @patch("app.utils.result_store.get_result_as_tempfile", return_value="/tmp/fake.csv")
    def test_returns_none_on_load_error(self, mock_tmp, mock_load, mock_unlink, mock_exists):
        result = R._load_project_output("proj-1")
        assert result is None


# ---------------------------------------------------------------------------
# validate_enrichment (lines 152-256)
# ---------------------------------------------------------------------------

class TestValidateEnrichment:

    def test_api_not_found_returns_404(self):
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.return_value = None
            res = client.post("/enrichment/validate", json={
                "project_id": "p1", "api_id": "bad", "param_mapping": {}
            })
        assert res.status_code == 404

    def test_unmapped_required_params_returns_invalid(self):
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.return_value = {
                "id": "api1",
                "parameters": [
                    {"name": "address", "required": True},
                    {"name": "city", "required": True},
                ]
            }
            res = client.post("/enrichment/validate", json={
                "project_id": "p1", "api_id": "api1",
                "param_mapping": {"address": "col_addr"},
                # city is unmapped
            })
        assert res.status_code == 200
        body = res.json()
        assert body["valid"] is False
        assert "city" in body["missing_columns"]

    def test_unmapped_required_but_static_is_ok(self):
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.return_value = {
                "id": "api1",
                "parameters": [{"name": "city", "required": True}]
            }
            res = client.post("/enrichment/validate", json={
                "project_id": "p1", "api_id": "api1",
                "param_mapping": {},
                "static_columns": ["city"],
            })
        assert res.status_code == 200
        body = res.json()
        # city is in static_columns so not counted as unmapped
        assert body["valid"] is True or "city" not in body.get("missing_columns", [])

    @patch("app.core.arricchisci.router._load_data", return_value=None)
    def test_df_none_no_data_validation_only(self, _):
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.return_value = {
                "id": "api1", "parameters": []
            }
            res = client.post("/enrichment/validate", json={
                "project_id": "p1", "api_id": "api1",
                "param_mapping": {"addr": "indirizzo"},
            })
        assert res.status_code == 200
        body = res.json()
        assert body["total_rows"] == 0
        assert "Nessun dato trovato" in body["warnings"][0]

    @patch("app.core.arricchisci.router._load_data")
    def test_df_empty_zero_rows_with_header(self, mock_ld):
        mock_ld.return_value = pd.DataFrame(columns=["indirizzo", "citta"])
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.return_value = {
                "id": "api1", "parameters": []
            }
            res = client.post("/enrichment/validate", json={
                "project_id": "p1", "api_id": "api1",
                "param_mapping": {"addr": "indirizzo"},
            })
        assert res.status_code == 200
        body = res.json()
        assert body["total_rows"] == 0
        assert body["valid"] is True
        assert body["missing_columns"] == []

    @patch("app.core.arricchisci.router._load_data")
    def test_df_empty_zero_rows_missing_col(self, mock_ld):
        mock_ld.return_value = pd.DataFrame(columns=["indirizzo"])
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.return_value = {
                "id": "api1", "parameters": []
            }
            res = client.post("/enrichment/validate", json={
                "project_id": "p1", "api_id": "api1",
                "param_mapping": {"addr": "citta"},  # citta not in columns
            })
        assert res.status_code == 200
        body = res.json()
        assert body["valid"] is False
        assert "citta" in body["missing_columns"]

    @patch("app.core.arricchisci.router._load_data")
    def test_missing_columns_in_df(self, mock_ld):
        mock_ld.return_value = pd.DataFrame({"indirizzo": ["via Roma"]})
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.return_value = {
                "id": "api1", "parameters": []
            }
            res = client.post("/enrichment/validate", json={
                "project_id": "p1", "api_id": "api1",
                "param_mapping": {"p1": "col_non_esiste"},
            })
        assert res.status_code == 200
        body = res.json()
        assert body["valid"] is False
        assert "col_non_esiste" in body["missing_columns"]

    @patch("app.core.arricchisci.router._load_data")
    def test_valid_with_nulls_warning(self, mock_ld):
        mock_ld.return_value = pd.DataFrame({"city": ["Roma", None, "Milano"]})
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.return_value = {
                "id": "api1", "parameters": []
            }
            res = client.post("/enrichment/validate", json={
                "project_id": "p1", "api_id": "api1",
                "param_mapping": {"city": "city"},
            })
        assert res.status_code == 200
        body = res.json()
        assert body["valid"] is True
        assert body["total_rows"] == 3
        assert body["null_param_rows_count"] == 1
        assert len(body["warnings"]) == 1

    @patch("app.core.arricchisci.router._load_data")
    def test_valid_no_nulls(self, mock_ld):
        mock_ld.return_value = pd.DataFrame({"city": ["Roma", "Milano"]})
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.return_value = {
                "id": "api1", "parameters": []
            }
            res = client.post("/enrichment/validate", json={
                "project_id": "p1", "api_id": "api1",
                "param_mapping": {"city": "city"},
            })
        assert res.status_code == 200
        body = res.json()
        assert body["valid"] is True
        assert body["null_param_rows_count"] == 0
        assert body["warnings"] == []

    def test_validate_crash_returns_500(self):
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.side_effect = RuntimeError("DB crash")
            res = client.post("/enrichment/validate", json={
                "project_id": "p1", "api_id": "api1", "param_mapping": {},
            })
        assert res.status_code == 500


# ---------------------------------------------------------------------------
# preview_enrichment (lines 265-301)
# ---------------------------------------------------------------------------

class TestPreviewEnrichment:

    def test_api_not_found_404(self):
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.return_value = None
            res = client.post("/enrichment/preview", json={
                "project_id": "p1", "api_id": "bad",
                "param_mapping": {}, "sample_size": 5,
            })
        assert res.status_code == 404

    @patch("app.core.arricchisci.router._load_data", return_value=None)
    def test_no_data_404(self, _):
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.return_value = {"id": "a1"}
            res = client.post("/enrichment/preview", json={
                "project_id": "p1", "api_id": "a1",
                "param_mapping": {}, "sample_size": 5,
            })
        assert res.status_code == 404

    @patch("app.core.arricchisci.router._load_data")
    def test_preview_with_inline_data(self, mock_ld):
        """When request.data is provided, should use it directly instead of _load_data."""
        client, _, api = make_client()
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "sample_size": 2, "success_count": 2, "error_count": 0,
            "success_rate": 100.0, "avg_time_per_row_ms": 10.0,
            "estimated_total_time_seconds": 1.0,
            "estimated_total_time_formatted": "1s",
            "estimated_api_calls": 2, "unique_param_combinations": 2,
            "preview_data": [{"a": 1}], "errors": [], "warnings": [],
        }
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc, \
             patch("app.core.arricchisci.enrichment_service.EnrichmentExecutor") as MockExec:
            mock_svc.get_api_connection.return_value = {"id": "a1"}
            MockExec.return_value.preview.return_value = mock_result
            res = client.post("/enrichment/preview", json={
                "project_id": "p1", "api_id": "a1",
                "param_mapping": {}, "sample_size": 2,
                "data": [{"city": "Roma"}, {"city": "Milano"}],
            })
        assert res.status_code == 200
        mock_ld.assert_not_called()

    def test_preview_crash_returns_500(self):
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.side_effect = RuntimeError("boom")
            res = client.post("/enrichment/preview", json={
                "project_id": "p1", "api_id": "a1",
                "param_mapping": {}, "sample_size": 5,
            })
        assert res.status_code == 500


# ---------------------------------------------------------------------------
# estimate_enrichment (lines 313-349)
# ---------------------------------------------------------------------------

class TestEstimateEnrichment:

    def test_api_not_found_404(self):
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.return_value = None
            res = client.post("/enrichment/estimate", json={
                "project_id": "p1", "api_id": "bad", "param_mapping": {}
            })
        assert res.status_code == 404

    @patch("app.core.arricchisci.router._load_data", return_value=None)
    def test_no_data_returns_zero_estimate(self, _):
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.return_value = {"id": "a1"}
            res = client.post("/enrichment/estimate", json={
                "project_id": "p1", "api_id": "a1", "param_mapping": {}
            })
        assert res.status_code == 200
        body = res.json()
        assert body["total_rows"] == 0
        assert body["estimated_api_calls"] == 0
        assert body["estimated_time_formatted"] == "0s"

    @patch("app.core.arricchisci.router._load_data")
    def test_estimate_with_data(self, mock_ld):
        mock_ld.return_value = pd.DataFrame({"city": ["Roma", "Milano", "Roma"]})
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc, \
             patch("app.core.arricchisci.enrichment_service.EnrichmentExecutor") as MockExec:
            mock_svc.get_api_connection.return_value = {
                "id": "a1", "options": {"rate_limit": 10}
            }
            MockExec.return_value._count_unique_combinations.return_value = 2
            res = client.post("/enrichment/estimate", json={
                "project_id": "p1", "api_id": "a1",
                "param_mapping": {"city": "city"}
            })
        assert res.status_code == 200
        body = res.json()
        assert body["total_rows"] == 3
        assert body["unique_combinations"] == 2
        assert body["estimated_api_calls"] == 2
        assert body["deduplication_savings_percent"] == 33.3

    def test_estimate_crash_returns_500(self):
        client, _, api = make_client()
        with patch("app.core.arricchisci.api_connector.api_connection_service") as mock_svc:
            mock_svc.get_api_connection.side_effect = RuntimeError("boom")
            res = client.post("/enrichment/estimate", json={
                "project_id": "p1", "api_id": "a1", "param_mapping": {}
            })
        assert res.status_code == 500


# ---------------------------------------------------------------------------
# get_api_connection with group_id check (line 377)
# ---------------------------------------------------------------------------

class TestGetApiConnectionGroupFilter:

    def test_non_admin_wrong_group_returns_403(self):
        client, _, api = make_client(user_fn=_mock_user_non_admin)
        api.get_api_connection.return_value = {
            "id": "api1", "name": "Test", "group_id": "group-a"
        }
        res = client.get("/api-connections/api1")
        assert res.status_code == 403

    def test_non_admin_same_group_returns_200(self):
        client, _, api = make_client(user_fn=_mock_user_non_admin)
        api.get_api_connection.return_value = {
            "id": "api1", "name": "Test", "group_id": "group-b"
        }
        res = client.get("/api-connections/api1")
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# update_api_connection error path (lines 407-409)
# ---------------------------------------------------------------------------

class TestUpdateApiConnectionErrors:

    def test_update_crash_returns_500(self):
        client, _, api = make_client()
        api.update_api_connection.side_effect = RuntimeError("DB crash")
        res = client.put("/api-connections/api1", json={"name": "test"})
        assert res.status_code == 500
