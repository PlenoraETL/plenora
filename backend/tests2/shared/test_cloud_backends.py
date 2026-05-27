"""
Contract test — app/infrastructure/storage/cloud_backends.py (astrazione cloud-agnostic).

1. Factory `get_secrets_backend()` / explicit object-storage factory dispatch via
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _isolate_bucket_env(monkeypatch):
    """Test-only env isolation per i factory bucket di cloud_backends.

    `_resolve_runtime_bucket()` legge in priorita' OBJECT_STORAGE_BUCKET →
    PLENORA_ARTIFACT_BUCKET → BACKUP_BUCKET → BACKUP_S3_BUCKET. Senza
    questo isolamento i test che asseriscono "ValueError quando bucket
    mancante" falliscono in locale dove `.env` ha OBJECT_STORAGE_BUCKET
    valorizzato (CI passa perche' `.env` non e' presente).

    Ogni test setta esplicitamente le var bucket che gli servono."""
    for var in (
        "OBJECT_STORAGE_BUCKET",
        "PLENORA_ARTIFACT_BUCKET",
        "BACKUP_BUCKET",
        "BACKUP_S3_BUCKET",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(scope="module")
def cb():
    from app.infrastructure.storage import cloud_backends
    return cloud_backends


def _requires_boto3():
    pytest.importorskip("boto3", reason="boto3 non installato su dev")


# 1. Factory — CLOUD_PROVIDER dispatch


def test_factory_default_provider_is_aws(cb, monkeypatch):
    monkeypatch.delenv("CLOUD_PROVIDER", raising=False)
    backend = cb.get_secrets_backend()
    assert isinstance(backend, cb.AwsSsmSecretsBackend)


def test_factory_aws_explicit(cb, monkeypatch):
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    backend = cb.get_secrets_backend()
    assert isinstance(backend, cb.AwsSsmSecretsBackend)
    assert backend.region == "us-east-1"


def test_factory_case_insensitive(cb, monkeypatch):
    monkeypatch.setenv("CLOUD_PROVIDER", "AWS")
    assert isinstance(cb.get_secrets_backend(), cb.AwsSsmSecretsBackend)


def test_factory_unknown_provider_raises(cb, monkeypatch):
    monkeypatch.setenv("CLOUD_PROVIDER", "oracle")
    with pytest.raises(ValueError, match="non supportato"):
        cb.get_secrets_backend()
    # get_backup_object_storage richiede un bucket per arrivare al provider
    # dispatch (la resolution bucket avviene prima). Settiamo un bucket
    # generico cosi' isoliamo la prova del provider check.
    monkeypatch.setenv("BACKUP_BUCKET", "any-bucket-for-provider-check")
    with pytest.raises(ValueError, match="non supportato"):
        cb.get_backup_object_storage()


def test_object_storage_factory_requires_bucket(cb, monkeypatch):
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.delenv("BACKUP_BUCKET", raising=False)
    monkeypatch.delenv("BACKUP_S3_BUCKET", raising=False)
    with pytest.raises(ValueError, match="BACKUP_BUCKET"):
        cb.get_backup_object_storage()


def test_object_storage_factory_accepts_legacy_env_name(cb, monkeypatch):
    """BACKUP_S3_BUCKET e' il nome legacy, BACKUP_BUCKET e' quello nuovo
    cross-provider. Factory accetta entrambi."""
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.delenv("BACKUP_BUCKET", raising=False)
    monkeypatch.setenv("BACKUP_S3_BUCKET", "legacy-bucket")
    storage = cb.get_backup_object_storage()
    assert storage.bucket == "legacy-bucket"


def test_object_storage_factory_prefers_new_env_name(cb, monkeypatch):
    """BACKUP_BUCKET (nuovo) ha priorita' su BACKUP_S3_BUCKET (legacy)."""
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_BUCKET", "new-name")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "legacy")
    storage = cb.get_backup_object_storage()
    assert storage.bucket == "new-name"


# 2. AwsSsmSecretsBackend.get_secrets — contract


def _make_fake_ssm_client(params):
    client = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Parameters": params}]
    client.get_paginator.return_value = paginator
    return client


def test_ssm_strips_prefix(cb):
    _requires_boto3()
    backend = cb.AwsSsmSecretsBackend(region="eu-south-1")
    fake_client = _make_fake_ssm_client([
        {"Name": "/plenora/prod/DATABASE_URL", "Value": "postgresql://..."},
        {"Name": "/plenora/prod/REDIS_URL", "Value": "redis://..."},
    ])
    with patch("boto3.client", return_value=fake_client):
        secrets = backend.get_secrets("/plenora/prod/")
    assert secrets == {
        "DATABASE_URL": "postgresql://...",
        "REDIS_URL": "redis://...",
    }


def test_ssm_subpath_to_underscore(cb):
    _requires_boto3()
    backend = cb.AwsSsmSecretsBackend(region="eu-south-1")
    fake = _make_fake_ssm_client([
        {"Name": "/plenora/prod/db/url", "Value": "postgres://..."},
    ])
    with patch("boto3.client", return_value=fake):
        secrets = backend.get_secrets("/plenora/prod/")
    assert secrets == {"db_url": "postgres://..."}


def test_ssm_empty_returns_empty_dict(cb):
    _requires_boto3()
    backend = cb.AwsSsmSecretsBackend(region="eu-south-1")
    with patch("boto3.client", return_value=_make_fake_ssm_client([])):
        assert backend.get_secrets("/plenora/prod/") == {}


def test_ssm_uses_with_decryption_and_recursive(cb):
    """Sanity: il backend chiama SSM con WithDecryption=True e Recursive=True.
    Senza questi, i SecureString tornano cifrati o le sub-dir sono perse."""
    _requires_boto3()
    backend = cb.AwsSsmSecretsBackend(region="eu-south-1")
    fake = _make_fake_ssm_client([])
    with patch("boto3.client", return_value=fake):
        backend.get_secrets("/plenora/prod/")

    fake.get_paginator.return_value.paginate.assert_called_once()
    kwargs = fake.get_paginator.return_value.paginate.call_args.kwargs
    assert kwargs["Path"] == "/plenora/prod/"
    assert kwargs["Recursive"] is True
    assert kwargs["WithDecryption"] is True


# 3. AwsS3ObjectStorage — wrap contract


def test_s3_list_objects_shape(cb):
    _requires_boto3()
    storage = cb.AwsS3ObjectStorage(bucket="b", region="eu-south-1")
    fake_client = MagicMock()
    paginator = MagicMock()
    fixed_ts = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)
    paginator.paginate.return_value = [{
        "Contents": [
            {"Key": "urby_20260417.dump", "LastModified": fixed_ts, "Size": 12345},
            {"Key": "other.txt", "LastModified": fixed_ts, "Size": 99},
        ]
    }]
    fake_client.get_paginator.return_value = paginator

    with patch("boto3.client", return_value=fake_client):
        result = storage.list_objects("urby_")

    assert len(result) == 2
    # Shape ObjectMeta
    assert result[0]["key"] == "urby_20260417.dump"
    assert result[0]["last_modified"] == fixed_ts
    assert result[0]["size"] == 12345


def test_s3_upload_uses_sse(cb, tmp_path):
    """Contract: upload con ServerSideEncryption=AES256 + StorageClass=STANDARD."""
    _requires_boto3()
    storage = cb.AwsS3ObjectStorage(bucket="my-bucket", region="eu-south-1")
    fake_client = MagicMock()

    local = tmp_path / "dump"
    local.write_bytes(b"payload")

    with patch("boto3.client", return_value=fake_client):
        storage.upload(local, "key.dump")

    fake_client.upload_file.assert_called_once()
    call = fake_client.upload_file.call_args
    args = call.args
    kwargs = call.kwargs
    assert args[0] == str(local)
    assert args[1] == "my-bucket"
    assert args[2] == "key.dump"
    assert kwargs["ExtraArgs"]["ServerSideEncryption"] == "AES256"
    assert kwargs["ExtraArgs"]["StorageClass"] == "STANDARD"


def test_s3_upload_bytes_uses_upload_fileobj_for_multipart(cb):
    """Contract: AwsS3ObjectStorage.upload_bytes deve usare upload_fileobj
    (auto-multipart via TransferConfig), non put_object (5GB cap).

    Garantisce che file grossi non vadano in errore artificiale.
    Il contract multi-cloud richiede l'auto-chunking nativo del provider.
    """
    _requires_boto3()
    storage = cb.AwsS3ObjectStorage(bucket="my-bucket", region="eu-south-1")
    fake_client = MagicMock()

    with patch("boto3.client", return_value=fake_client):
        storage.upload_bytes(b"x" * 100, "key.bin")

    # put_object NON deve essere chiamato (quello ha il cap 5GB)
    fake_client.put_object.assert_not_called()
    # upload_fileobj SI (auto-multipart)
    fake_client.upload_fileobj.assert_called_once()

    # Verifica che TransferConfig sia passato con multipart_threshold
    call = fake_client.upload_fileobj.call_args
    assert "Config" in call.kwargs
    # ExtraArgs preserva encryption + storage class
    extra_args = call.kwargs.get("ExtraArgs", {})
    assert extra_args.get("ServerSideEncryption") == "AES256"
    assert extra_args.get("StorageClass") == "STANDARD"


def test_s3_download_delegates_correctly(cb, tmp_path):
    _requires_boto3()
    storage = cb.AwsS3ObjectStorage(bucket="my-bucket", region="eu-south-1")
    fake_client = MagicMock()
    dest = tmp_path / "out"

    with patch("boto3.client", return_value=fake_client):
        storage.download("key.dump", dest)

    fake_client.download_file.assert_called_once_with("my-bucket", "key.dump", str(dest))


# 4. Protocol conformance (duck-typing sanity)


def test_aws_backends_satisfy_protocols(cb):
    """Le impl AWS devono soddisfare i Protocol via runtime isinstance."""
    ssm = cb.AwsSsmSecretsBackend(region="eu-south-1")
    assert isinstance(ssm, cb.SecretsBackend)

    s3 = cb.AwsS3ObjectStorage(bucket="b", region="eu-south-1")
    assert isinstance(s3, cb.ObjectStorage)


# 5. MinioObjectStorage — provider A1-B2 (ADR-0015)


def _set_minio_env(monkeypatch):
    monkeypatch.setenv("CLOUD_PROVIDER", "minio")
    monkeypatch.setenv("BACKUP_BUCKET", "onprem-bucket")
    monkeypatch.setenv("MINIO_ENDPOINT_URL", "http://minio.internal:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
    monkeypatch.setenv("MINIO_SECRET_KEY", "sk")


def test_factory_minio_requires_endpoint_url(cb, monkeypatch):
    _set_minio_env(monkeypatch)
    monkeypatch.delenv("MINIO_ENDPOINT_URL", raising=False)
    with pytest.raises(ValueError, match="MINIO_ENDPOINT_URL"):
        cb.get_backup_object_storage()


def test_factory_minio_requires_access_key(cb, monkeypatch):
    _set_minio_env(monkeypatch)
    monkeypatch.delenv("MINIO_ACCESS_KEY", raising=False)
    with pytest.raises(ValueError, match="MINIO_ACCESS_KEY"):
        cb.get_backup_object_storage()


def test_factory_minio_requires_secret_key(cb, monkeypatch):
    _set_minio_env(monkeypatch)
    monkeypatch.delenv("MINIO_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="MINIO_SECRET_KEY"):
        cb.get_backup_object_storage()


def test_factory_minio_returns_configured_storage(cb, monkeypatch):
    _set_minio_env(monkeypatch)
    storage = cb.get_backup_object_storage()
    assert isinstance(storage, cb.MinioObjectStorage)
    assert storage.bucket == "onprem-bucket"
    assert storage.endpoint_url == "http://minio.internal:9000"
    assert storage.access_key == "ak"
    assert storage.secret_key == "sk"


def test_minio_client_uses_endpoint_credentials_and_path_style(cb):
    """Contract: `_client()` passa a boto3 endpoint_url, credenziali esplicite,
    e addressing_style=path (MinIO non supporta virtual-hosted di default)."""
    _requires_boto3()
    storage = cb.MinioObjectStorage(
        bucket="b",
        endpoint_url="http://minio:9000",
        access_key="ak",
        secret_key="sk",
        region="us-east-1",
    )
    fake_client = MagicMock()
    with patch("boto3.client", return_value=fake_client) as mock_boto:
        storage._client()

    mock_boto.assert_called_once()
    kwargs = mock_boto.call_args.kwargs
    assert kwargs["region_name"] == "us-east-1"
    assert kwargs["endpoint_url"] == "http://minio:9000"
    assert kwargs["aws_access_key_id"] == "ak"
    assert kwargs["aws_secret_access_key"] == "sk"
    # Config boto3: addressing_style path
    config = kwargs["config"]
    assert config.s3["addressing_style"] == "path"


def test_minio_upload_bytes_omits_sse_by_default(cb):
    """Opzione B: MinIO vanilla non ha SSE configurato. `_extra_args()` non
    deve includere ServerSideEncryption. StorageClass=STANDARD resta."""
    _requires_boto3()
    storage = cb.MinioObjectStorage(
        bucket="b",
        endpoint_url="http://minio:9000",
        access_key="ak",
        secret_key="sk",
    )
    fake_client = MagicMock()
    with patch("boto3.client", return_value=fake_client):
        storage.upload_bytes(b"payload", "key.bin")

    fake_client.upload_fileobj.assert_called_once()
    extra_args = fake_client.upload_fileobj.call_args.kwargs.get("ExtraArgs", {})
    assert "ServerSideEncryption" not in extra_args
    assert extra_args.get("StorageClass") == "STANDARD"


def test_minio_object_storage_satisfies_protocol(cb):
    """MinioObjectStorage deve soddisfare il Protocol ObjectStorage."""
    storage = cb.MinioObjectStorage(
        bucket="b",
        endpoint_url="http://minio:9000",
        access_key="ak",
        secret_key="sk",
    )
    assert isinstance(storage, cb.ObjectStorage)
