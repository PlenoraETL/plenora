"""
Unit tests for datasources/router.py — file operation endpoints + scan state endpoints.
Uses FastAPI TestClient with dependency overrides.
"""

import io
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

from app.core.datasources.models import (
    DataSource,
    DataSourceList,
    FileInfoResponse,
    ScanState,
    ProcessedEntry,
)
from app.core.datasources.router import router, init_datasources
from app.core.datasources.interface import (
    DataSourceError,
    DataSourceConnectionError,
    DataSourceFileNotFoundError,
    DataSourcePermissionError,
)
from app.core.auth.auth import get_current_user, CurrentUser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fake_user():
    return CurrentUser(user_id="u1", username="test", group_id="g1", role="admin")


def _ds_model(ds_id="ds_1", group_id="g1"):
    now = datetime.now()
    return DataSource(
        id=ds_id, name="Test", type="local", root_path="/data",
        group_id=group_id, created_at=now, updated_at=now,
    )


def _file_info(name="data.csv"):
    return FileInfoResponse(
        name=name, path=f"sub/{name}", size=1024,
        modified_date=datetime(2025, 1, 15, 10, 0), type="csv",
    )


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api")

    mock_service = MagicMock()
    init_datasources(mock_service)

    app.dependency_overrides[get_current_user] = _fake_user

    yield TestClient(app), mock_service

    app.dependency_overrides.clear()
    init_datasources(None)


# ========================== GET /datasources/{id}/files ===================

class TestListFiles:

    def test_success(self, client):
        tc, svc = client
        svc.list_files.return_value = [_file_info()]
        resp = tc.get("/api/datasources/ds_1/files")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "data.csv"

    def test_with_params(self, client):
        tc, svc = client
        svc.list_files.return_value = []
        resp = tc.get("/api/datasources/ds_1/files?path=sub&pattern=*.csv&include_hash=true")
        assert resp.status_code == 200
        svc.list_files.assert_called_once_with(
            "ds_1", path="sub", pattern="*.csv", include_hash=True,
        )

    def test_ds_error_404(self, client):
        tc, svc = client
        svc.list_files.side_effect = DataSourceFileNotFoundError("not found")
        resp = tc.get("/api/datasources/ds_1/files")
        assert resp.status_code == 404

    def test_ds_error_502(self, client):
        tc, svc = client
        svc.list_files.side_effect = DataSourceConnectionError("conn failed")
        resp = tc.get("/api/datasources/ds_1/files")
        assert resp.status_code == 502

    def test_generic_error_500(self, client):
        tc, svc = client
        svc.list_files.side_effect = Exception("boom")
        resp = tc.get("/api/datasources/ds_1/files")
        assert resp.status_code == 500


# ========================== GET /datasources/{id}/files/download ==========

class TestDownloadFile:

    def test_success(self, client):
        tc, svc = client
        svc.read_file.return_value = b"file-content"
        resp = tc.get("/api/datasources/ds_1/files/download?path=data.csv")
        assert resp.status_code == 200
        assert resp.content == b"file-content"
        assert "attachment" in resp.headers.get("content-disposition", "")

    def test_not_found(self, client):
        tc, svc = client
        svc.read_file.side_effect = DataSourceFileNotFoundError("nope")
        resp = tc.get("/api/datasources/ds_1/files/download?path=nope.csv")
        assert resp.status_code == 404

    def test_permission_denied(self, client):
        tc, svc = client
        svc.read_file.side_effect = DataSourcePermissionError("denied")
        resp = tc.get("/api/datasources/ds_1/files/download?path=locked.csv")
        assert resp.status_code == 403


# ========================== POST /datasources/{id}/files/upload ===========

class TestUploadFile:

    def test_success(self, client):
        tc, svc = client
        resp = tc.post(
            "/api/datasources/ds_1/files/upload?path=sub/new.csv",
            files={"file": ("new.csv", b"csv-content", "text/csv")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["ok"] is True
        assert data["path"] == "sub/new.csv"
        assert data["size"] == len(b"csv-content")
        svc.write_file.assert_called_once()

    def test_ds_error(self, client):
        tc, svc = client
        svc.write_file.side_effect = DataSourcePermissionError("denied")
        resp = tc.post(
            "/api/datasources/ds_1/files/upload?path=f.csv",
            files={"file": ("f.csv", b"x", "text/csv")},
        )
        assert resp.status_code == 403

    def test_generic_error(self, client):
        tc, svc = client
        svc.write_file.side_effect = Exception("boom")
        resp = tc.post(
            "/api/datasources/ds_1/files/upload?path=f.csv",
            files={"file": ("f.csv", b"x", "text/csv")},
        )
        assert resp.status_code == 500


# ========================== _ds_error_to_http =============================

class TestErrorMapping:

    def test_file_not_found_404(self, client):
        tc, svc = client
        svc.list_files.side_effect = DataSourceFileNotFoundError("x")
        resp = tc.get("/api/datasources/ds_1/files")
        assert resp.status_code == 404

    def test_permission_403(self, client):
        tc, svc = client
        svc.list_files.side_effect = DataSourcePermissionError("x")
        resp = tc.get("/api/datasources/ds_1/files")
        assert resp.status_code == 403

    def test_connection_502(self, client):
        tc, svc = client
        svc.list_files.side_effect = DataSourceConnectionError("x")
        resp = tc.get("/api/datasources/ds_1/files")
        assert resp.status_code == 502

    def test_generic_ds_error_500(self, client):
        tc, svc = client
        svc.list_files.side_effect = DataSourceError("x")
        resp = tc.get("/api/datasources/ds_1/files")
        assert resp.status_code == 500
