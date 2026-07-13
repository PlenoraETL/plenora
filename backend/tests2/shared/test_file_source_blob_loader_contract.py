"""Object-storage-only contracts for the file-source blob loader."""

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def _ref(*, superadmin: bool = False):
    from app.core.resources import ResourceRef

    return ResourceRef(
        kind="file_source_blob",
        expected_group_id="g1",
        file_source_id="fs1",
        is_superadmin=superadmin,
    )


def _fake_row(row):
    captured = {}

    def fetch(sql, params=()):
        captured.update(sql=sql, params=params)
        return row

    return captured, fetch


def test_bytes_mode_filters_tenant_and_downloads_object():
    from app.core.resources.loaders.file_source_blob import FileSourceBlobLoader

    captured, fetch = _fake_row({"storage_key": "file-sources/fs1/a.csv"})
    with patch("app.db.pg_connection.fetchone", fetch), patch(
        "app.core.resources.loaders.file_source_blob._download_object_storage",
        return_value=b"a,b\n1,2\n",
    ):
        out = FileSourceBlobLoader().load(_ref(), mode="bytes")

    assert captured["params"] == ("fs1", "g1")
    assert "data" not in captured["sql"].lower()
    assert out.data == b"a,b\n1,2\n"


class _Storage:
    def get_object_stream(self, key):
        return {"body": iter((b"hello", b"world")), "size": 10}


def test_stream_mode_is_lazy_and_bounded():
    from app.core.resources.loaders.file_source_blob import FileSourceBlobLoader

    _, fetch = _fake_row({"storage_key": "file-sources/fs1/a.bin"})
    with patch("app.db.pg_connection.fetchone", fetch), patch(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        return_value=_Storage(),
    ):
        out = FileSourceBlobLoader().load(_ref(), mode="stream")

    assert out.data is None
    assert out.size_bytes == 10
    assert b"".join(out.stream) == b"helloworld"


def test_missing_row_and_missing_storage_key_fail_closed():
    from app.core.resources.loaders.file_source_blob import FileSourceBlobLoader

    _, missing = _fake_row(None)
    with patch("app.db.pg_connection.fetchone", missing):
        with pytest.raises(FileNotFoundError, match="not found"):
            FileSourceBlobLoader().load(_ref(), mode="bytes")

    _, corrupt = _fake_row({"storage_key": None})
    with patch("app.db.pg_connection.fetchone", corrupt):
        with pytest.raises(RuntimeError, match="senza storage_key"):
            FileSourceBlobLoader().load(_ref(), mode="bytes")


def test_path_mode_is_rejected():
    from app.core.resources.loaders.file_source_blob import FileSourceBlobLoader

    with pytest.raises(ValueError, match="not supported"):
        FileSourceBlobLoader().load(_ref(), mode="path")
