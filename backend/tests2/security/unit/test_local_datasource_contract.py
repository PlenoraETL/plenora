"""
Tests the local datasource behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

import pytest

from app.core.datasources.interface import BackendConfig, DataSourcePermissionError
from app.core.datasources.local import LocalDataSource


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


def _make_source(root_path):
    return LocalDataSource(
        BackendConfig(
            id="ds-1",
            type="local",
            root_path=str(root_path),
        )
    )


def test_abs_rejects_parent_traversal(tmp_path):
    ds = _make_source(tmp_path)

    with pytest.raises(DataSourcePermissionError, match="Path traversal rifiutato"):
        ds._abs("../secret.txt")


def test_abs_rejects_absolute_path(tmp_path):
    ds = _make_source(tmp_path)

    with pytest.raises(DataSourcePermissionError, match="Path assoluto non consentito"):
        ds._abs("/etc/passwd")


def test_abs_keeps_paths_inside_root(tmp_path):
    ds = _make_source(tmp_path)

    resolved = ds._abs("nested/data.csv")

    assert resolved.endswith("nested\\data.csv") or resolved.endswith("nested/data.csv")


def test_raw_read_rejects_files_bigger_than_cap(tmp_path, monkeypatch):
    ds = _make_source(tmp_path)
    sample = tmp_path / "huge.bin"
    sample.write_bytes(b"1234")

    monkeypatch.setattr(ds, "_MAX_READ_SIZE", 2)

    with pytest.raises(ValueError, match="File troppo grande"):
        ds._raw_read(str(sample))
