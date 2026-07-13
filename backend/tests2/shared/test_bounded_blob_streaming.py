"""Contratti RAM: niente read unbounded, spool rollover e ZIP chunked."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class _NoUnboundedRead(io.BytesIO):
    def read(self, size=-1):
        assert size >= 0, "read() unbounded materializzerebbe il BLOB completo"
        return super().read(size)


def test_copy_to_spool_reads_chunks_and_rolls_to_disk(monkeypatch):
    from app.core.io import bounded_streams

    monkeypatch.setattr(bounded_streams, "spool_memory_limit", lambda: 4)
    source = _NoUnboundedRead(b"0123456789")
    spool = bounded_streams.copy_to_spool(source, chunk_bytes=3)
    try:
        assert getattr(spool, "_rolled", False) is True
        assert spool.read() == b"0123456789"
    finally:
        spool.close()


def test_spool_zip_member_decompresses_incrementally(monkeypatch):
    from app.core.io import bounded_streams
    from app.core.io.zip_guards import open_zip_stream_safe, spool_zip_member

    monkeypatch.setattr(bounded_streams, "spool_memory_limit", lambda: 8)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as writer:
        writer.writestr("data.csv", b"a\n" + b"1\n" * 20)
    archive.seek(0)

    with open_zip_stream_safe(archive) as reader:
        # Il nuovo path usa ZipFile.open + chunk copy, mai ZipFile.read.
        monkeypatch.setattr(
            reader,
            "read",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("ZipFile.read non deve essere usato")
            ),
        )
        spool = spool_zip_member(reader, "data.csv")
    try:
        assert getattr(spool, "_rolled", False) is True
        assert spool.read().startswith(b"a\n1\n")
    finally:
        spool.close()


def test_zip_expansion_cap_is_checked_before_member_read(monkeypatch):
    from app.core.io import zip_guards

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as writer:
        writer.writestr("large.csv", b"x" * 128)
    archive.seek(0)

    monkeypatch.setattr(zip_guards, "_get_caps", lambda: (64, 64, 10, 100))
    with pytest.raises(zip_guards.ZipBombError, match="troppo grande"):
        with zip_guards.open_zip_stream_safe(archive):
            pytest.fail("il body non deve essere raggiunto")


def test_path_zip_loader_does_not_call_read_bytes(tmp_path, monkeypatch):
    from app.core.dataframes.frame_io import _carica_da_zip

    archive = tmp_path / "data.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as writer:
        writer.writestr("data.csv", "id,name\n1,Ada\n2,Linus\n")

    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: (_ for _ in ()).throw(
            AssertionError("il path ZIP non deve essere materializzato")
        ),
    )
    df = _carica_da_zip(archive)
    assert df.to_dict("records") == [
        {"id": 1, "name": "Ada"},
        {"id": 2, "name": "Linus"},
    ]


def test_s3_upload_fileobj_preserves_stream_and_uses_bounded_reads():
    pytest.importorskip("boto3")
    from app.infrastructure.storage.cloud_backends import AwsS3ObjectStorage

    payload = b"0123456789"
    source = _NoUnboundedRead(payload)
    source.seek(4)
    client = MagicMock()

    def consume(stream, *_args, **_kwargs):
        while stream.read(3):
            pass

    client.upload_fileobj.side_effect = consume
    client.head_object.return_value = {"ContentLength": len(payload)}
    storage = AwsS3ObjectStorage(bucket="bucket", region="eu-south-1")
    with patch("boto3.client", return_value=client):
        storage.upload_fileobj(source, "key.bin", size_bytes=len(payload))

    assert source.tell() == 4
    client.put_object.assert_not_called()
    client.upload_fileobj.assert_called_once()
