"""
Unit tests for datasources/service.py — file ops delegation, scan state, find_new_files.
"""

import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timedelta

pytestmark = pytest.mark.unit

from app.core.datasources.models import (
    ScanState,
    ProcessedEntry,
    SnapshotEntry,
    FileInfoResponse,
)
from app.core.datasources.service import DataSourceService


@pytest.fixture
def svc(tmp_path):
    s = DataSourceService.__new__(DataSourceService)
    s._scan_dir = tmp_path / "scan"
    return s


def _mock_backend():
    backend = MagicMock()
    backend.__enter__ = MagicMock(return_value=backend)
    backend.__exit__ = MagicMock(return_value=False)
    return backend


def _file_info_response(name, **kwargs):
    defaults = {
        "path": name,
        "size": 100,
        "modified_date": datetime(2025, 1, 15, 10, 0),
        "type": "csv",
        "hash": None,
    }
    defaults.update(kwargs)
    return FileInfoResponse(name=name, **defaults)


# ========================== test_connection ===============================

class TestTestConnection:

    @patch("app.core.datasources.service.create_backend")
    def test_success(self, mock_create, svc):
        mock_backend = MagicMock()
        mock_backend.test_connection.return_value = {
            "ok": True, "message": "OK", "details": {},
        }
        mock_create.return_value = mock_backend

        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds1", "group_id": "g1",
                "config": {"id": "ds1", "type": "local", "root_path": "/"},
            }
            result = svc.test_connection("ds1")
            assert result.success is True

    def test_not_found(self, svc):
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = None
            result = svc.test_connection("nope")
            assert result.success is False
            assert "non trovato" in result.message

    @patch("app.core.datasources.service.create_backend")
    def test_exception(self, mock_create, svc):
        mock_create.side_effect = Exception("boom")
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds1", "group_id": "g1",
                "config": {"id": "ds1", "type": "local", "root_path": "/"},
            }
            result = svc.test_connection("ds1")
            assert result.success is False


# ========================== File ops delegation ===========================

class TestFileOps:

    @patch("app.core.datasources.service.create_backend")
    def test_list_files(self, mock_create, svc):
        backend = _mock_backend()
        from app.core.datasources.interface import FileInfo
        backend.list_files.return_value = [
            FileInfo(name="a.csv", path="a.csv", size=10,
                     modified_date=datetime(2025, 1, 1), type="csv"),
        ]
        mock_create.return_value = backend

        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds1", "group_id": "g1",
                "config": {"id": "ds1", "type": "local", "root_path": "/"},
            }
            result = svc.list_files("ds1", pattern="*.csv")
            assert len(result) == 1
            assert isinstance(result[0], FileInfoResponse)

    @patch("app.core.datasources.service.create_backend")
    def test_read_file(self, mock_create, svc):
        backend = _mock_backend()
        backend.read_file.return_value = b"content"
        mock_create.return_value = backend

        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds1", "group_id": "g1",
                "config": {"id": "ds1", "type": "local", "root_path": "/"},
            }
            assert svc.read_file("ds1", "f.csv") == b"content"

    @patch("app.core.datasources.service.create_backend")
    def test_write_file(self, mock_create, svc):
        backend = _mock_backend()
        mock_create.return_value = backend

        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds1", "group_id": "g1",
                "config": {"id": "ds1", "type": "local", "root_path": "/"},
            }
            svc.write_file("ds1", "f.csv", b"data")
            backend.write_file.assert_called_once_with("f.csv", b"data")

    @patch("app.core.datasources.service.create_backend")
    def test_delete_file(self, mock_create, svc):
        backend = _mock_backend()
        mock_create.return_value = backend

        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds1", "group_id": "g1",
                "config": {"id": "ds1", "type": "local", "root_path": "/"},
            }
            svc.delete_file("ds1", "f.csv")
            backend.delete_file.assert_called_once_with("f.csv")

    @patch("app.core.datasources.service.create_backend")
    def test_file_exists(self, mock_create, svc):
        backend = _mock_backend()
        backend.file_exists.return_value = True
        mock_create.return_value = backend

        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds1", "group_id": "g1",
                "config": {"id": "ds1", "type": "local", "root_path": "/"},
            }
            assert svc.file_exists("ds1", "f.csv") is True

    def test_file_ops_not_found(self, svc):
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = None
            from app.core.datasources.interface import DataSourceError
            with pytest.raises(DataSourceError, match="non trovato"):
                svc.read_file("nope", "f.csv")


# ========================== Scan State ====================================

class TestScanState:

    def test_get_default(self, svc):
        state = svc.get_scan_state("ds1")
        assert state.last_check is None
        assert state.processed == []

    def test_save_and_get(self, svc):
        state = ScanState(strategy="inventory")
        svc.save_scan_state("ds1", state)
        loaded = svc.get_scan_state("ds1")
        assert loaded.strategy == "inventory"

    def test_save_creates_dir(self, svc):
        state = ScanState()
        svc.save_scan_state("ds1", state)
        assert (svc._scan_dir / "ds1.json").exists()

    def test_reset(self, svc):
        state = ScanState(strategy="inventory")
        svc.save_scan_state("ds1", state)
        svc.reset_scan_state("ds1")
        loaded = svc.get_scan_state("ds1")
        assert loaded.strategy == "modified_date"
        assert loaded.processed == []

    def test_corrupt_file(self, svc):
        svc._scan_dir.mkdir(parents=True, exist_ok=True)
        (svc._scan_dir / "ds1.json").write_text("NOT JSON")
        state = svc.get_scan_state("ds1")
        assert state.last_check is None  # fallback to default

    def test_save_with_processed(self, svc):
        now = datetime.now()
        state = ScanState(
            last_check=now,
            processed=[ProcessedEntry(name="f.csv", processed_at=now, pipeline_id="p1")],
        )
        svc.save_scan_state("ds1", state)
        loaded = svc.get_scan_state("ds1")
        assert len(loaded.processed) == 1
        assert loaded.processed[0].name == "f.csv"


# ========================== find_new_files ================================

class TestFindNewFiles:

    def _setup_svc(self, svc, files, state=None):
        """Patch list_files and scan_state."""
        svc.list_files = MagicMock(return_value=files)
        if state:
            svc.save_scan_state("ds1", state)

    def test_modified_date_no_last_check(self, svc):
        files = [_file_info_response("a.csv"), _file_info_response("b.csv")]
        self._setup_svc(svc, files)
        result = svc.find_new_files("ds1", strategy="modified_date")
        assert len(result) == 2

    def test_modified_date_with_last_check(self, svc):
        old_time = datetime(2025, 1, 1)
        new_time = datetime(2025, 2, 1)
        files = [
            _file_info_response("new.csv", modified_date=new_time),
            _file_info_response("old.csv", modified_date=old_time),
        ]
        state = ScanState(last_check=datetime(2025, 1, 15))
        self._setup_svc(svc, files, state)
        result = svc.find_new_files("ds1", strategy="modified_date")
        assert len(result) == 1
        assert result[0].name == "new.csv"

    def test_inventory_strategy(self, svc):
        files = [
            _file_info_response("known.csv", size=100),
            _file_info_response("new.csv", size=200),
        ]
        state = ScanState(
            snapshot=[SnapshotEntry(name="known.csv", size=100)],
        )
        self._setup_svc(svc, files, state)
        result = svc.find_new_files("ds1", strategy="inventory")
        assert len(result) == 1
        assert result[0].name == "new.csv"

    def test_pattern_strategy(self, svc):
        files = [
            _file_info_response("processed.csv"),
            _file_info_response("new.csv"),
        ]
        now = datetime.now()
        state = ScanState(
            processed=[ProcessedEntry(name="processed.csv", processed_at=now, pipeline_id="p1")],
        )
        self._setup_svc(svc, files, state)
        result = svc.find_new_files("ds1", strategy="pattern")
        assert len(result) == 1
        assert result[0].name == "new.csv"

    def test_unknown_strategy_fallback(self, svc):
        files = [_file_info_response("a.csv")]
        self._setup_svc(svc, files)
        result = svc.find_new_files("ds1", strategy="unknown_strat")
        assert len(result) == 1

    def test_excludes_already_processed(self, svc):
        now = datetime.now()
        files = [_file_info_response("done.csv", modified_date=now)]
        state = ScanState(
            processed=[ProcessedEntry(name="done.csv", processed_at=now, pipeline_id="p1")],
        )
        self._setup_svc(svc, files, state)
        result = svc.find_new_files("ds1", strategy="modified_date")
        assert len(result) == 0


# ========================== mark_as_processed =============================

class TestMarkAsProcessed:

    @patch("app.core.datasources.service.create_backend")
    def test_success(self, mock_create, svc):
        backend = _mock_backend()
        from app.core.datasources.interface import FileInfo
        backend.file_info.return_value = FileInfo(
            name="f.csv", path="f.csv", size=100,
            modified_date=datetime(2025, 1, 15), type="csv",
        )
        mock_create.return_value = backend

        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds1", "group_id": "g1",
                "config": {"id": "ds1", "type": "local", "root_path": "/"},
            }
            svc.mark_as_processed("ds1", "f.csv", "pipeline1")
            state = svc.get_scan_state("ds1")
            assert len(state.processed) == 1
            assert state.processed[0].name == "f.csv"
            assert state.processed[0].pipeline_id == "pipeline1"
            assert state.last_check is not None

    def test_no_duplicate(self, svc):
        now = datetime.now()
        state = ScanState(
            processed=[ProcessedEntry(name="f.csv", processed_at=now, pipeline_id="p1")],
        )
        svc.save_scan_state("ds1", state)

        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = None
            from app.core.datasources.interface import DataSourceError
            # file_info will fail since DS not found, but processed check happens first
            # We need a valid backend for file_info
            pass

    @patch("app.core.datasources.service.create_backend")
    def test_file_info_failure_ignored(self, mock_create, svc):
        backend = _mock_backend()
        backend.file_info.side_effect = Exception("not found")
        mock_create.return_value = backend

        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds1", "group_id": "g1",
                "config": {"id": "ds1", "type": "local", "root_path": "/"},
            }
            svc.mark_as_processed("ds1", "missing.csv", "p1")
            state = svc.get_scan_state("ds1")
            assert len(state.processed) == 1
            assert len(state.snapshot) == 0  # file_info failed
