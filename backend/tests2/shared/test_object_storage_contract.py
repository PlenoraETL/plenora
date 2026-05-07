"""
Contract parity matrix — Protocol `ObjectStorage` cross-provider (A1-B3).

Verifica che `AwsS3ObjectStorage` e `MinioObjectStorage` implementino il
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# Provider matrix — baseline moto + opt-in real MinIO

_MINIO_MOTO_ENDPOINT = "http://minio-moto.test:9000"  # valore usato solo dal
                                                      # client; moto intercetta


def _providers_available():
    """Matrix runnabile: baseline moto sempre, real MinIO opt-in via env."""
    providers = [("aws", "moto"), ("minio", "moto")]
    if os.getenv("MINIO_E2E_ENDPOINT_URL"):
        providers.append(("minio", "real"))
    return providers


@pytest.fixture(
    params=_providers_available(),
    ids=lambda p: f"{p[0]}-{p[1]}",
)
def provider(request, monkeypatch):
    """Setup ObjectStorage instance + bucket di test + teardown.

    Baseline moto:
      - Setta `MOTO_S3_CUSTOM_ENDPOINTS` PRIMA di `mock_aws()` per
        intercettare anche MinioObjectStorage (endpoint non-AWS).
      - Entra in `mock_aws()` come context manager -> isolamento per test.
      - Crea bucket `contract-test`.

    Real MinIO:
      - Richiede env: MINIO_E2E_ENDPOINT_URL, MINIO_E2E_ACCESS_KEY,
        MINIO_E2E_SECRET_KEY, e opzionalmente MINIO_E2E_BUCKET
        (default 'contract-test').
      - Cleanup: rimuove le object keys create al termine del test.
    """
    provider_name, mode = request.param

    if mode == "moto":
        pytest.importorskip("moto", reason="moto non installato (dev dep)")
        # moto legge MOTO_S3_CUSTOM_ENDPOINTS dinamicamente a request-time
        # per decidere se intercettare; monkeypatch.setenv e' sufficiente
        # e viene ripristinato in teardown automaticamente.
        monkeypatch.setenv("MOTO_S3_CUSTOM_ENDPOINTS", _MINIO_MOTO_ENDPOINT)
        from moto import mock_aws
        import boto3

        with mock_aws():
            boto3.client("s3", region_name="us-east-1").create_bucket(
                Bucket="contract-test"
            )
            yield _make_moto_provider(provider_name, bucket="contract-test")
    else:  # mode == "real"
        yield from _make_real_minio_provider()


def _make_moto_provider(name: str, bucket: str):
    """Istanza provider configurata contro moto (in-process)."""
    from app.shared import cloud_backends as cb
    if name == "aws":
        return cb.AwsS3ObjectStorage(bucket=bucket, region="us-east-1")
    if name == "minio":
        return cb.MinioObjectStorage(
            bucket=bucket,
            endpoint_url=_MINIO_MOTO_ENDPOINT,
            access_key="testing",
            secret_key="testing",
            region="us-east-1",
        )
    raise ValueError(f"provider name sconosciuto: {name!r}")


def _make_real_minio_provider():
    """Istanza MinioObjectStorage contro server reale + cleanup.

    Attende env: MINIO_E2E_ENDPOINT_URL, MINIO_E2E_ACCESS_KEY,
    MINIO_E2E_SECRET_KEY. Opzionale MINIO_E2E_BUCKET (default 'contract-test').
    Crea il bucket idempotentemente (ignorando errore se esiste).
    Dopo il test, rimuove tutte le key residue (best-effort).
    """
    from app.shared.cloud_backends import MinioObjectStorage

    endpoint = os.environ["MINIO_E2E_ENDPOINT_URL"]
    access = os.environ["MINIO_E2E_ACCESS_KEY"]
    secret = os.environ["MINIO_E2E_SECRET_KEY"]
    bucket = os.getenv("MINIO_E2E_BUCKET", "contract-test")

    storage = MinioObjectStorage(
        bucket=bucket,
        endpoint_url=endpoint,
        access_key=access,
        secret_key=secret,
    )
    try:
        storage._client().create_bucket(Bucket=bucket)
    except Exception:
        pass  # bucket gia' esistente: atteso in runs ripetuti

    try:
        yield storage
    finally:
        # Cleanup keys (best-effort, non blocca teardown)
        try:
            for obj in storage.list_objects(""):
                try:
                    storage.delete(obj["key"])
                except Exception:
                    pass
        except Exception:
            pass


# C1 — upload + download roundtrip small (<1 KB)


def test_upload_download_roundtrip_small(provider, tmp_path):
    """upload(path) + download(key, path) su file piccolo -> byte-exact."""
    payload = b"hello world " * 20  # 240 bytes
    src = tmp_path / "src.bin"
    src.write_bytes(payload)

    provider.upload(src, "small.bin")

    dst = tmp_path / "dst.bin"
    provider.download("small.bin", dst)
    assert dst.read_bytes() == payload


# C2 — upload_bytes multipart >8MB: ESCLUSO dal baseline (vedi docstring file).
# Il test resta commentato e NON registrato nella suite finche' non ci sara'
# un path pulito contro server reale (opt-in MinIO) separato dal baseline moto.


# C3 — list_objects con prefix (set semantic)


def test_list_objects_prefix_semantic(provider):
    """list_objects(prefix) ritorna solo oggetti che iniziano col prefix;
    shape ObjectMeta stabile (key, last_modified, size)."""
    provider.upload_bytes(b"a", "prefix-a/1.bin")
    provider.upload_bytes(b"bb", "prefix-a/2.bin")
    provider.upload_bytes(b"ccc", "other/1.bin")  # NON deve apparire

    listed = provider.list_objects("prefix-a/")
    keys = {obj["key"] for obj in listed}

    assert keys == {"prefix-a/1.bin", "prefix-a/2.bin"}
    # Shape ObjectMeta: ogni entry deve avere le 3 chiavi contract
    for obj in listed:
        assert "key" in obj
        assert "last_modified" in obj
        assert "size" in obj
        assert isinstance(obj["size"], int)
        assert obj["size"] >= 0


# C4 — delete idempotente (S3/MinIO spec: 204 no error su key inesistente)


def test_delete_idempotent(provider):
    """Il delete di una key inesistente non deve sollevare: sia S3 sia MinIO
    rispondono 204 No Content (spec). Il Protocol deve replicare questa
    semantica."""
    # Key mai caricata: il delete deve passare senza exception
    provider.delete("never-existed.bin")
    # Idempotenza su key esistente: upload + delete + delete di nuovo
    provider.upload_bytes(b"x", "exists.bin")
    provider.delete("exists.bin")
    provider.delete("exists.bin")  # secondo delete: no error


# C5 — overwrite (upload su key esistente sostituisce)


def test_overwrite_replaces(provider):
    """upload_bytes su una key che esiste gia' sovrascrive senza errore.
    Il contenuto letto dopo il secondo upload e' quello nuovo."""
    provider.upload_bytes(b"version-1", "key.bin")
    provider.upload_bytes(b"version-2", "key.bin")  # overwrite

    stream = provider.get_object_stream("key.bin")
    content = b"".join(stream["body"])
    assert content == b"version-2"
    assert stream["size"] == len(b"version-2")


# C6 — content_type default: ESCLUSO dal baseline (vedi docstring file).
# Non e' un contract cross-provider: S3/moto/MinIO server tutti ritornano
# `binary/octet-stream`, mentre la docstring del Protocol dichiara
# `application/octet-stream` come fallback in-code. Allineare richiede
# cambio runtime o docstring, fuori scope A1-B3.
