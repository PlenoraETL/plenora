"""
Contract test — classificazione multi-host per DataSource local.

R4 multi-host audit: verifica path locali non accessibili ai worker remoti.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# _classify_path_for_multi_host


@pytest.mark.parametrize("path,expected", [
    # UNC Windows
    (r"\\server\share", "network"),
    (r"\\fileserver\data\plenora", "network"),
    # UNC-style Linux (rara ma possibile)
    ("//server/share", "network"),
    # Linux mount conventions
    ("/mnt/nfs/data", "network"),
    ("/media/share", "network"),
    ("/net/s3fuse/bucket", "network"),
    ("/nfs/plenora", "network"),
    ("/efs/plenora", "network"),
    # Windows drive letter
    ("C:\\data", "local"),
    ("D:\\plenora\\data", "local"),
    ("c:\\Users\\Marco", "local"),
    # Linux absolute local
    ("/home/ubuntu/data", "local"),
    ("/srv/plenora", "local"),
    ("/var/plenora", "local"),
    ("/opt/plenora/files", "local"),
    ("/tmp/test", "local"),
    # Ambiguo / vuoto
    ("", "unknown"),
    ("relative/path", "unknown"),
    ("/data", "unknown"),  # non in nessuna whitelist
    ("/custom/mount", "unknown"),
])
def test_classify_path_for_multi_host(path, expected):
    from app.core.datasources.local import _classify_path_for_multi_host
    assert _classify_path_for_multi_host(path) == expected


def test_classify_none_or_invalid_returns_unknown():
    from app.core.datasources.local import _classify_path_for_multi_host
    assert _classify_path_for_multi_host(None) == "unknown"  # type: ignore
    # Path con solo un separatore
    assert _classify_path_for_multi_host("//") == "unknown"


# test_connection include advisory


def _make_local_datasource(tmp_path):
    """Helper: crea una LocalDataSource con root_path = tmp_path."""
    from app.core.datasources.local import LocalDataSource
    from app.core.datasources.interface import BackendConfig

    config = BackendConfig(id="test", type="local", root_path=str(tmp_path))
    return LocalDataSource(config)


def test_test_connection_advisory_on_local_path(tmp_path, monkeypatch):
    """Un path locale (tmp_path = /tmp/... o C:\\...) produce advisory."""
    from app.core.datasources.local import LocalDataSource, _classify_path_for_multi_host
    from app.core.datasources.interface import BackendConfig

    # Forziamo sempre "local" per isolare il test dalla heuristic del path reale
    monkeypatch.setattr(
        "app.core.datasources.local._classify_path_for_multi_host",
        lambda p: "local",
    )

    config = BackendConfig(id="t", type="local", root_path=str(tmp_path))
    ds = LocalDataSource(config)
    result = ds.test_connection()

    assert result["ok"] is True
    assert result["details"]["path_class"] == "local"
    assert result["details"]["multi_host_advisory"] is not None
    assert "UNC" in result["details"]["multi_host_advisory"] \
        or "multi-host" in result["details"]["multi_host_advisory"].lower()


def test_test_connection_no_advisory_on_network_path(tmp_path, monkeypatch):
    """Path classificato network non produce advisory."""
    from app.core.datasources.local import LocalDataSource
    from app.core.datasources.interface import BackendConfig

    monkeypatch.setattr(
        "app.core.datasources.local._classify_path_for_multi_host",
        lambda p: "network",
    )

    config = BackendConfig(id="t", type="local", root_path=str(tmp_path))
    ds = LocalDataSource(config)
    result = ds.test_connection()

    assert result["ok"] is True
    assert result["details"]["path_class"] == "network"
    assert result["details"]["multi_host_advisory"] is None


def test_test_connection_advisory_on_unknown_path(tmp_path, monkeypatch):
    """Path non classificabile produce advisory di verifica."""
    from app.core.datasources.local import LocalDataSource
    from app.core.datasources.interface import BackendConfig

    monkeypatch.setattr(
        "app.core.datasources.local._classify_path_for_multi_host",
        lambda p: "unknown",
    )

    config = BackendConfig(id="t", type="local", root_path=str(tmp_path))
    ds = LocalDataSource(config)
    result = ds.test_connection()

    assert result["ok"] is True
    assert result["details"]["path_class"] == "unknown"
    advisory = result["details"]["multi_host_advisory"]
    assert advisory is not None
    assert "verifica" in advisory.lower() or "classificare" in advisory.lower()
