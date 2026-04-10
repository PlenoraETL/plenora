"""
Unit tests for datasources/interface.py — helpers, FileInfo, BackendConfig.
"""

import pytest
import hashlib
from datetime import datetime

pytestmark = pytest.mark.unit

from app.core.datasources.interface import (
    DataSourceInterface,
    DataSourceError,
    DataSourceConnectionError,
    DataSourceFileNotFoundError,
    DataSourcePermissionError,
    FileInfo,
    BackendConfig,
)


# ---------------------------------------------------------------------------
# Concrete stub so we can test non-abstract helpers
# ---------------------------------------------------------------------------

class _Stub(DataSourceInterface):
    def connect(self): pass
    def disconnect(self): pass
    def test_connection(self): return {}
    def list_files(self, **kw): return []
    def file_info(self, path): ...
    def read_file(self, path): return b""
    def write_file(self, path, content): pass
    def delete_file(self, path): pass
    def file_exists(self, path): return False


def _make_stub(**extra):
    cfg = BackendConfig(id="t", type="local", root_path="/tmp", extra=extra)
    return _Stub(cfg)


# ========================== _extension ====================================

class TestExtension:

    def test_simple(self):
        s = _make_stub()
        assert s._extension("data.csv") == "csv"

    def test_uppercase(self):
        s = _make_stub()
        assert s._extension("DATA.CSV") == "csv"

    def test_double_ext(self):
        s = _make_stub()
        assert s._extension("archive.tar.gz") == "gz"

    def test_no_ext(self):
        s = _make_stub()
        assert s._extension("Makefile") == ""

    def test_dotfile(self):
        s = _make_stub()
        # ".gitignore" has extension "gitignore"
        assert s._extension(".gitignore") == "gitignore"

    def test_empty_string(self):
        s = _make_stub()
        assert s._extension("") == ""


# ========================== _matches_pattern ==============================

class TestMatchesPattern:

    def test_none_pattern(self):
        s = _make_stub()
        assert s._matches_pattern("anything.csv", None) is True

    def test_star_csv(self):
        s = _make_stub()
        assert s._matches_pattern("data.csv", "*.csv") is True
        assert s._matches_pattern("data.txt", "*.csv") is False

    def test_prefix(self):
        s = _make_stub()
        assert s._matches_pattern("export_20250101.csv", "export_*.csv") is True
        assert s._matches_pattern("import_20250101.csv", "export_*.csv") is False

    def test_question_mark(self):
        s = _make_stub()
        assert s._matches_pattern("file1.txt", "file?.txt") is True
        assert s._matches_pattern("file12.txt", "file?.txt") is False

    def test_exact_match(self):
        s = _make_stub()
        assert s._matches_pattern("readme.md", "readme.md") is True


# ========================== _compute_hash =================================

class TestComputeHash:

    def test_known_value(self):
        s = _make_stub()
        data = b"hello world"
        expected = hashlib.md5(data).hexdigest()
        assert s._compute_hash(data) == expected

    def test_empty(self):
        s = _make_stub()
        expected = hashlib.md5(b"").hexdigest()
        assert s._compute_hash(b"") == expected

    def test_returns_hex_string(self):
        s = _make_stub()
        result = s._compute_hash(b"abc")
        assert isinstance(result, str)
        assert len(result) == 32  # md5 hex length


# ========================== _join_path ====================================

class TestJoinPath:

    def test_simple(self):
        s = _make_stub()
        assert s._join_path("a", "b") == "a/b"

    def test_strips_slashes(self):
        s = _make_stub()
        assert s._join_path("/a/", "/b/") == "a/b"

    def test_three_parts(self):
        s = _make_stub()
        assert s._join_path("a", "b", "c.txt") == "a/b/c.txt"

    def test_empty_parts(self):
        s = _make_stub()
        assert s._join_path("", "b") == "b"
        assert s._join_path("a", "") == "a"

    def test_all_empty(self):
        s = _make_stub()
        assert s._join_path("", "") == ""

    def test_double_slashes(self):
        s = _make_stub()
        assert s._join_path("a//b", "c") == "a/b/c"


# ========================== FileInfo ======================================

class TestFileInfo:

    def test_to_dict(self):
        now = datetime(2025, 1, 15, 10, 30, 0)
        fi = FileInfo(
            name="data.csv", path="sub/data.csv",
            size=100, modified_date=now, type="csv", hash="abc",
        )
        d = fi.to_dict()
        assert d["name"] == "data.csv"
        assert d["path"] == "sub/data.csv"
        assert d["size"] == 100
        assert d["modified_date"] == now.isoformat()
        assert d["type"] == "csv"
        assert d["hash"] == "abc"

    def test_to_dict_hash_none(self):
        now = datetime(2025, 1, 1)
        fi = FileInfo(name="f", path="f", size=0, modified_date=now, type="txt")
        d = fi.to_dict()
        assert d["hash"] is None

    def test_defaults(self):
        now = datetime.now()
        fi = FileInfo(name="f", path="f", size=0, modified_date=now, type="txt")
        assert fi.hash is None


# ========================== BackendConfig =================================

class TestBackendConfig:

    def test_defaults(self):
        cfg = BackendConfig(id="x", type="local", root_path="/data")
        assert cfg.extra == {}

    def test_extra(self):
        cfg = BackendConfig(id="x", type="ftp", root_path="/", extra={"host": "h"})
        assert cfg.extra["host"] == "h"


# ========================== Context Manager ===============================

class TestContextManager:

    def test_enter_exit(self):
        s = _make_stub()
        with s as ds:
            assert ds is s

    def test_exit_returns_false(self):
        s = _make_stub()
        result = s.__exit__(None, None, None)
        assert result is False


# ========================== Exception Hierarchy ===========================

class TestExceptions:

    def test_hierarchy(self):
        assert issubclass(DataSourceConnectionError, DataSourceError)
        assert issubclass(DataSourceFileNotFoundError, DataSourceError)
        assert issubclass(DataSourcePermissionError, DataSourceError)

    def test_raise(self):
        with pytest.raises(DataSourceError):
            raise DataSourceConnectionError("test")
