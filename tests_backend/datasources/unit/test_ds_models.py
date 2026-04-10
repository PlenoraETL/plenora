"""
Unit tests for datasources/models.py — Pydantic models.
"""

import pytest
from datetime import datetime

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from app.core.datasources.models import (
    DataSourceCreate,
    DataSourceUpdate,
    DataSource,
    DataSourceList,
    DataSourceTestResult,
    FileInfoResponse,
    ScanState,
    ProcessedEntry,
    SnapshotEntry,
    ListFilesRequest,
    WriteFileRequest,
)


# ========================== DataSourceCreate ==============================

class TestDataSourceCreate:
    """Validation and defaults for DataSourceCreate."""

    def test_minimal_valid(self):
        m = DataSourceCreate(name="test", type="local")
        assert m.name == "test"
        assert m.type == "local"
        assert m.description == ""
        assert m.root_path == ""

    def test_all_fields(self):
        m = DataSourceCreate(
            name="my-ftp",
            description="Desc",
            type="ftp",
            root_path="/data",
            host="ftp.example.com",
            port=2121,
            username="user",
            password="pass",
            tls=True,
            passive=False,
        )
        assert m.host == "ftp.example.com"
        assert m.port == 2121
        assert m.tls is True
        assert m.passive is False

    def test_sftp_fields(self):
        m = DataSourceCreate(
            name="my-sftp",
            type="sftp",
            private_key="PEM_DATA",
            private_key_passphrase="secret",
            host_key_policy="reject",
        )
        assert m.private_key == "PEM_DATA"
        assert m.host_key_policy == "reject"

    def test_sharepoint_fields(self):
        m = DataSourceCreate(
            name="sp",
            type="sharepoint",
            tenant_id="t1",
            client_id="c1",
            client_secret="s1",
            site_url="https://example.sharepoint.com/sites/test",
        )
        assert m.tenant_id == "t1"

    def test_invalid_type_rejected(self):
        with pytest.raises(Exception):
            DataSourceCreate(name="x", type="s3")

    def test_missing_name_rejected(self):
        with pytest.raises(Exception):
            DataSourceCreate(type="local")

    def test_defaults_are_none_for_optional(self):
        m = DataSourceCreate(name="n", type="local")
        assert m.host is None
        assert m.port is None
        assert m.tls is False
        assert m.passive is True
        assert m.private_key is None

    def test_model_dump(self):
        m = DataSourceCreate(name="n", type="ftp", host="h")
        d = m.model_dump()
        assert d["name"] == "n"
        assert d["type"] == "ftp"
        assert d["host"] == "h"


# ========================== DataSourceUpdate ==============================

class TestDataSourceUpdate:
    """Validation for DataSourceUpdate — all fields optional."""

    def test_empty_is_valid(self):
        m = DataSourceUpdate()
        assert m.name is None
        assert m.host is None

    def test_partial_update(self):
        m = DataSourceUpdate(name="new-name", port=2222)
        assert m.name == "new-name"
        assert m.port == 2222
        assert m.password is None

    def test_model_dump_exclude_none(self):
        m = DataSourceUpdate(name="x")
        d = m.model_dump(exclude_none=True)
        assert d == {"name": "x"}

    def test_host_key_policy_literal(self):
        m = DataSourceUpdate(host_key_policy="reject")
        assert m.host_key_policy == "reject"

    def test_host_key_policy_invalid(self):
        with pytest.raises(Exception):
            DataSourceUpdate(host_key_policy="invalid_policy")


# ========================== DataSource ====================================

class TestDataSource:
    """Full DataSource response model."""

    def test_minimal(self):
        now = datetime.now()
        m = DataSource(
            id="ds_1", name="n", type="local",
            created_at=now, updated_at=now,
        )
        assert m.id == "ds_1"
        assert m.description == ""
        assert m.group_id == ""

    def test_all_optional_none(self):
        now = datetime.now()
        m = DataSource(
            id="ds_2", name="n", type="ftp",
            created_at=now, updated_at=now,
        )
        assert m.host is None
        assert m.tls is None

    def test_model_dump_includes_timestamps(self):
        now = datetime.now()
        m = DataSource(
            id="ds_3", name="n", type="sftp",
            created_at=now, updated_at=now,
        )
        d = m.model_dump()
        assert "created_at" in d
        assert "updated_at" in d

    def test_password_field_present(self):
        now = datetime.now()
        m = DataSource(
            id="ds_4", name="n", type="ftp",
            password="***",
            created_at=now, updated_at=now,
        )
        assert m.password == "***"


# ========================== DataSourceList ================================

class TestDataSourceList:
    """Wrapper list model."""

    def test_empty_list(self):
        m = DataSourceList(datasources=[])
        assert m.datasources == []

    def test_with_items(self):
        now = datetime.now()
        ds = DataSource(id="a", name="b", type="local", created_at=now, updated_at=now)
        m = DataSourceList(datasources=[ds])
        assert len(m.datasources) == 1
        assert m.datasources[0].id == "a"


# ========================== DataSourceTestResult ==========================

class TestDataSourceTestResult:
    """Test connection result model."""

    def test_success(self):
        m = DataSourceTestResult(success=True, message="OK")
        assert m.success is True
        assert m.details is None

    def test_failure_with_details(self):
        m = DataSourceTestResult(success=False, message="Fail", details={"code": 500})
        assert m.success is False
        assert m.details["code"] == 500


# ========================== FileInfoResponse ==============================

class TestFileInfoResponse:
    """File metadata response model."""

    def test_full(self):
        now = datetime.now()
        m = FileInfoResponse(
            name="data.csv", path="sub/data.csv",
            size=1024, modified_date=now, type="csv", hash="abc123",
        )
        assert m.name == "data.csv"
        assert m.hash == "abc123"

    def test_hash_optional(self):
        now = datetime.now()
        m = FileInfoResponse(
            name="f.txt", path="f.txt", size=0,
            modified_date=now, type="txt",
        )
        assert m.hash is None


# ========================== ScanState =====================================

class TestScanState:
    """Scan state for scheduler."""

    def test_defaults(self):
        m = ScanState()
        assert m.last_check is None
        assert m.strategy == "modified_date"
        assert m.snapshot == []
        assert m.processed == []

    def test_with_strategy(self):
        m = ScanState(strategy="inventory")
        assert m.strategy == "inventory"

    def test_invalid_strategy(self):
        with pytest.raises(Exception):
            ScanState(strategy="unknown_strategy")

    def test_model_dump(self):
        m = ScanState()
        d = m.model_dump()
        assert d["strategy"] == "modified_date"
        assert d["snapshot"] == []

    def test_with_processed(self):
        now = datetime.now()
        pe = ProcessedEntry(name="file.csv", processed_at=now, pipeline_id="p1")
        m = ScanState(processed=[pe])
        assert len(m.processed) == 1
        assert m.processed[0].pipeline_id == "p1"


# ========================== ProcessedEntry ================================

class TestProcessedEntry:
    """Processed file record."""

    def test_fields(self):
        now = datetime.now()
        m = ProcessedEntry(name="x.csv", processed_at=now, pipeline_id="p1")
        assert m.name == "x.csv"
        assert m.pipeline_id == "p1"

    def test_missing_field(self):
        with pytest.raises(Exception):
            ProcessedEntry(name="x.csv")


# ========================== SnapshotEntry =================================

class TestSnapshotEntry:

    def test_defaults(self):
        m = SnapshotEntry(name="f.csv", size=100)
        assert m.modified is None

    def test_with_modified(self):
        m = SnapshotEntry(name="f.csv", size=100, modified="2025-01-01T00:00:00")
        assert m.modified == "2025-01-01T00:00:00"


# ========================== ListFilesRequest / WriteFileRequest ===========

class TestRequestModels:

    def test_list_files_request_defaults(self):
        m = ListFilesRequest()
        assert m.path == ""
        assert m.pattern is None
        assert m.include_hash is False

    def test_write_file_request(self):
        m = WriteFileRequest(path="sub/file.csv")
        assert m.path == "sub/file.csv"
