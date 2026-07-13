"""Object-storage-only dispatch boundary contracts."""

import io
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _intent(monkeypatch):
    from app.core.resources import blob_dispatch

    monkeypatch.setattr(
        blob_dispatch.reconciliation,
        "record_upload_intent",
        lambda **kwargs: "intent-1",
    )


def test_bytes_always_upload_even_with_legacy_db_env(monkeypatch):
    from app.core.resources.blob_dispatch import dispatch_blob_storage

    monkeypatch.setenv("STORAGE_POLICY", "db")
    storage = MagicMock()
    logger = MagicMock()
    with patch(
        "app.core.io.object_storage.get_runtime_object_storage",
        return_value=storage,
    ):
        out = dispatch_blob_storage("f1", b"abc", "a.csv", logger=logger)

    assert out == {
        "storage_key": "file-sources/f1/a.csv",
        "size_bytes": 3,
    }
    storage.upload_bytes.assert_called_once_with(b"abc", "file-sources/f1/a.csv")


def test_stream_is_forwarded_without_materializing():
    from app.core.resources.blob_dispatch import dispatch_blob_storage

    stream = io.BytesIO(b"stream")
    storage = MagicMock()
    with patch(
        "app.core.io.object_storage.get_runtime_object_storage",
        return_value=storage,
    ):
        out = dispatch_blob_storage(
            "f2", stream, "b.bin", logger=MagicMock(), size_bytes=6
        )

    assert out["storage_key"] == "file-sources/f2/b.bin"
    storage.upload_fileobj.assert_called_once_with(
        stream, "file-sources/f2/b.bin", size_bytes=6
    )


def test_upload_failure_is_fail_closed():
    from app.core.resources.blob_dispatch import dispatch_blob_storage

    storage = MagicMock()
    storage.upload_bytes.side_effect = RuntimeError("S3 down")
    with patch(
        "app.core.io.object_storage.get_runtime_object_storage",
        return_value=storage,
    ):
        with pytest.raises(RuntimeError, match="S3 down"):
            dispatch_blob_storage("f3", b"x", "x.bin", logger=MagicMock())
