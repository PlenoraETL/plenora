"""
Contract test per `_assert_upload_integrity` — verifica post-upload size
del backend S3/MinIO.

Bug coperto (plenora-bug-data-layer P0):
  `artifact_writer.py:274,307` chiamava `storage.upload_bytes(...)` senza
  verifica end-to-end: boto3 solleva solo su errori di rete/HTTP, NON su
  silent data corruption (chunks corrotti accettati, byte mancanti per
  streaming I/O instabile). L'artifact veniva marcato
  `artifact_status="complete"` anche su upload corrotto, i downstream
  readers leggevano dati malformati.

Post-fix: `AwsS3ObjectStorage.upload_bytes` chiama `head_object`
post-upload e verifica `ContentLength == len(data)`. Su mismatch tenta
cleanup best-effort + solleva `StorageIntegrityError`. Il caller
(artifact_writer) propaga eccezione → `artifact_status="failed"`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.infrastructure.storage.cloud_backends import (
    StorageIntegrityError,
    _assert_upload_integrity,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.contract,
    pytest.mark.security,
    pytest.mark.regression,
]


def _make_client_with_head(content_length: int | None) -> MagicMock:
    client = MagicMock()
    if content_length is None:
        client.head_object.side_effect = Exception("simulated AWS 503")
    else:
        client.head_object.return_value = {"ContentLength": content_length}
    return client


class TestAssertUploadIntegrity:
    """Table-driven scenarios per la verifica integrita' upload."""

    def test_returns_silently_when_size_matches(self):
        client = _make_client_with_head(content_length=100)
        # No exception, no delete tentato.
        _assert_upload_integrity(
            client=client, bucket="b", key="k", expected_size=100,
        )
        client.delete_object.assert_not_called()

    def test_raises_storage_integrity_error_on_size_mismatch(self):
        client = _make_client_with_head(content_length=42)
        with pytest.raises(StorageIntegrityError, match="size mismatch"):
            _assert_upload_integrity(
                client=client, bucket="b", key="k", expected_size=100,
            )

    def test_deletes_corrupted_object_on_size_mismatch(self):
        client = _make_client_with_head(content_length=42)
        with pytest.raises(StorageIntegrityError):
            _assert_upload_integrity(
                client=client, bucket="bkt", key="path/data.parquet",
                expected_size=100,
            )
        # Cleanup tentato (best-effort) per evitare orphan in bucket.
        client.delete_object.assert_called_once_with(
            Bucket="bkt", Key="path/data.parquet",
        )

    def test_raises_when_head_object_fails(self):
        client = _make_client_with_head(content_length=None)
        with pytest.raises(StorageIntegrityError, match="head_object"):
            _assert_upload_integrity(
                client=client, bucket="b", key="k", expected_size=100,
            )

    def test_swallows_cleanup_delete_failure(self):
        """Se delete_object fallisce dopo size mismatch, l'eccezione
        StorageIntegrityError deve comunque propagare al caller (l'orphan
        verra' raccolto dal riconciliator).
        """
        client = MagicMock()
        client.head_object.return_value = {"ContentLength": 42}
        client.delete_object.side_effect = Exception("delete failed too")
        with pytest.raises(StorageIntegrityError, match="size mismatch"):
            _assert_upload_integrity(
                client=client, bucket="b", key="k", expected_size=100,
            )

    @pytest.mark.parametrize("expected,actual", [
        (1, 0),               # empty bytes upload, server got nothing
        (1024, 1023),         # 1-byte truncation
        (1024 * 1024, 0),     # complete failure
        (100, 200),           # over-receive (header malformed)
    ])
    def test_any_mismatch_raises(self, expected, actual):
        client = _make_client_with_head(content_length=actual)
        with pytest.raises(StorageIntegrityError):
            _assert_upload_integrity(
                client=client, bucket="b", key="k", expected_size=expected,
            )
