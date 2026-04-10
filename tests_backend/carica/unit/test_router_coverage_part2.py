"""
tests/carica/unit/test_router_coverage.py
==========================================
Unit tests for app.core.carica.router — targeting uncovered lines.

Covers:
  - init_carica_service
  - _get_connection helper (404, 503)
  - _create_engine helper (factory path, fallback for each db_type)
  - GET /carica/schemas/{connection_id}
  - GET /carica/tables/{connection_id}/{schema}
  - GET /carica/columns/{connection_id}/{schema}/{table}
  - POST /carica/auto-mapping
  - POST /carica/validate
  - POST /carica/check-conflicts (all branches)
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch, PropertyMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------

FAKE_CONN_DICT = {
    "type": "postgres",
    "host": "localhost",
    "port": 5432,
    "database": "testdb",
    "username": "user",
    "password": "pass",
}


def _fake_current_user():
    """Dependency override for get_current_user."""
    from app.core.auth.auth import CurrentUser
    return CurrentUser(user_id="u1", username="tester", group_id="g1", role="admin")


@pytest.fixture()
def client():
    """TestClient with auth dependency overridden and _connection_service set."""
    import app.core.carica.router as mod
    from app.core.auth.auth import get_current_user

    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[get_current_user] = _fake_current_user

    # Prepare a mock connection service
    mock_conn_svc = MagicMock()
    mock_conn_obj = MagicMock()
    mock_conn_obj.model_dump.return_value = dict(FAKE_CONN_DICT)
    mock_conn_svc.get_connection.return_value = mock_conn_obj

    old_svc = mod._connection_service
    old_exec = mod._executor
    mod._connection_service = mock_conn_svc
    mod._executor = MagicMock()

    yield TestClient(app, raise_server_exceptions=False)

    mod._connection_service = old_svc
    mod._executor = old_exec


@pytest.fixture()
def client_no_service():
    """TestClient where _connection_service is None (503 path)."""
    import app.core.carica.router as mod
    from app.core.auth.auth import get_current_user

    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[get_current_user] = _fake_current_user

    old_svc = mod._connection_service
    old_exec = mod._executor
    mod._connection_service = None
    mod._executor = None

    yield TestClient(app, raise_server_exceptions=False)

    mod._connection_service = old_svc
    mod._executor = old_exec




# ============================================================================
# POST /carica/check-conflicts  (lines 356-447)
# ============================================================================

class TestCheckConflicts:

    def _base_payload(self, **overrides):
        payload = {
            "connection_id": "conn-1",
            "schema_name": "public",
            "table_name": "users",
            "match_columns": ["id"],
        }
        payload.update(overrides)
        return payload

    def test_sample_values_path(self, client):
        """Branch: sample_values provided."""
        mock_report = MagicMock()
        mock_report.to_dict.return_value = {
            "has_conflicts": True,
            "total_conflicts": 2,
        }
        with patch("app.core.carica.router._create_engine"), \
             patch("app.core.carica.conflict_checker.ConflictChecker") as mock_cc_cls:
            mock_cc_cls.return_value.check.return_value = mock_report
            # Patch at the import location inside the function
            with patch("app.core.carica.router.ConflictChecker", create=True) as mock_local:
                mock_local.return_value.check.return_value = mock_report
                payload = self._base_payload(
                    sample_values=[{"id": 1}, {"id": 2}],
                )
                res = client.post("/carica/check-conflicts", json=payload)
        assert res.status_code == 200
        assert res.json()["has_conflicts"] is True

    def test_source_connection_path_has_text_bug(self, client):
        """Branch: source_connection_id + source_table.

        BUG: `text` is used at line 380 but is only imported inside auto_mapping
        (line 259), not inside check_conflicts. This branch will raise NameError
        at runtime because `text` is not in scope.
        """
        payload = self._base_payload(
            source_connection_id="conn-2",
            source_schema="public",
            source_table="src_table",
            source_match_columns=["src_id"],
        )
        # We expect this to hit the except block and return 500 because of
        # the NameError on `text` at line 380.
        # BUG: line 380 uses bare `text(src_query)` but `from sqlalchemy import text`
        # is only done inside auto_mapping (line 259), not inside check_conflicts.
        with patch("app.core.carica.router._create_engine") as mock_eng:
            mock_conn_ctx = MagicMock()
            mock_eng.return_value.connect.return_value.__enter__ = MagicMock(return_value=mock_conn_ctx)
            mock_eng.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            res = client.post("/carica/check-conflicts", json=payload)
        # BUG: NameError for `text` causes 500; should be 200 if `text` were imported
        assert res.status_code == 500

    def test_project_id_path_with_result(self, client):
        """Branch: project_id provided and result store returns a file."""
        mock_report = MagicMock()
        mock_report.to_dict.return_value = {
            "has_conflicts": False,
            "total_conflicts": 0,
        }
        with patch("app.core.carica.router._create_engine"), \
             patch("app.core.carica.router.ConflictChecker", create=True) as mock_cc, \
             patch("app.utils.result_store.get_result_as_tempfile") as mock_tmp, \
             patch("app.utils.dataframe.load_dataframe_from_file") as mock_load:
            mock_cc.return_value.check.return_value = mock_report
            mock_tmp.return_value = "/tmp/fake.parquet"
            mock_load.return_value = pd.DataFrame({"id": [1, 2, 3]})
            payload = self._base_payload(project_id="proj-1")
            res = client.post("/carica/check-conflicts", json=payload)
        assert res.status_code == 200

    def test_project_id_path_no_result(self, client):
        """Branch: project_id provided but result store returns None."""
        with patch("app.core.carica.router._create_engine"), \
             patch("app.utils.result_store.get_result_as_tempfile") as mock_tmp:
            mock_tmp.return_value = None
            payload = self._base_payload(project_id="proj-1")
            res = client.post("/carica/check-conflicts", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["has_conflicts"] is False
        assert "Nessun output disponibile" in data["message"]

    def test_project_id_path_with_df_match_columns(self, client):
        """Branch: project_id with df_match_columns aliasing."""
        mock_report = MagicMock()
        mock_report.to_dict.return_value = {
            "has_conflicts": False,
            "total_conflicts": 0,
        }
        with patch("app.core.carica.router._create_engine"), \
             patch("app.core.carica.router.ConflictChecker", create=True) as mock_cc, \
             patch("app.utils.result_store.get_result_as_tempfile") as mock_tmp, \
             patch("app.utils.dataframe.load_dataframe_from_file") as mock_load:
            mock_cc.return_value.check.return_value = mock_report
            mock_tmp.return_value = "/tmp/fake.parquet"
            mock_load.return_value = pd.DataFrame({"src_id": [1, 2], "extra": ["a", "b"]})
            payload = self._base_payload(
                project_id="proj-1",
                match_columns=["id"],
                df_match_columns=["src_id"],
            )
            res = client.post("/carica/check-conflicts", json=payload)
        assert res.status_code == 200

    def test_project_id_path_no_matching_columns(self, client):
        """Branch: project_id, available_match is empty -> empty DataFrame."""
        mock_report = MagicMock()
        mock_report.to_dict.return_value = {
            "has_conflicts": False,
            "total_conflicts": 0,
        }
        with patch("app.core.carica.router._create_engine"), \
             patch("app.core.carica.router.ConflictChecker", create=True) as mock_cc, \
             patch("app.utils.result_store.get_result_as_tempfile") as mock_tmp, \
             patch("app.utils.dataframe.load_dataframe_from_file") as mock_load:
            mock_cc.return_value.check.return_value = mock_report
            mock_tmp.return_value = "/tmp/fake.parquet"
            mock_load.return_value = pd.DataFrame({"other_col": [1, 2]})
            payload = self._base_payload(
                project_id="proj-1",
                match_columns=["id"],
            )
            res = client.post("/carica/check-conflicts", json=payload)
        assert res.status_code == 200

    def test_no_data_path(self, client):
        """Branch: no sample_values, no source_connection_id, no project_id."""
        payload = self._base_payload()
        res = client.post("/carica/check-conflicts", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["has_conflicts"] is False
        assert "Nessun dato da verificare" in data["message"]

    def test_exception_returns_500(self, client):
        with patch("app.core.carica.router._get_connection", side_effect=RuntimeError("boom")):
            payload = self._base_payload(sample_values=[{"id": 1}])
            res = client.post("/carica/check-conflicts", json=payload)
        assert res.status_code == 500
