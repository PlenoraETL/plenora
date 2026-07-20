"""Provisioning idempotente dei bucket del deployment Windows locale."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


_MISSING_BUCKET_CODES = frozenset({"404", "NoSuchBucket", "NotFound"})


def ensure_bucket(client: Any, bucket: str) -> bool:
    """Crea ``bucket`` se assente; ritorna True soltanto se lo crea."""
    from botocore.exceptions import ClientError

    try:
        client.head_bucket(Bucket=bucket)
        return False
    except ClientError as exc:
        error = exc.response.get("Error") or {}
        metadata = exc.response.get("ResponseMetadata") or {}
        code = str(error.get("Code") or "")
        status = metadata.get("HTTPStatusCode")
        if code not in _MISSING_BUCKET_CODES and status != 404:
            raise

    client.create_bucket(Bucket=bucket)
    return True


def configure_runtime_lifecycle(
    client: Any,
    bucket: str,
    *,
    retention_days: int = 7,
) -> None:
    """Install the ephemeral safety net on the runtime artifact bucket."""
    days = max(1, int(retention_days))
    client.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "tagged-ephemeral-cleanup",
                    "Status": "Enabled",
                    "Filter": {
                        "Tag": {
                            "Key": "plenora-retention",
                            "Value": "ephemeral",
                        }
                    },
                    "Expiration": {"Days": days},
                },
                {
                    "ID": "tenant-incomplete-multipart-cleanup",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "tenants/"},
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                },
                *[
                    {
                        "ID": f"{prefix}-legacy-cleanup",
                        "Status": "Enabled",
                        "Filter": {"Prefix": f"{prefix}/"},
                        "Expiration": {"Days": days},
                        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                    }
                    for prefix in ("eseguibili-outputs", "imdf-bundles")
                ],
            ]
        },
    )


def _required_env(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} non configurato nel backend/.env")
    return value


def provision_configured_buckets() -> list[str]:
    """Carica il .env del backend e assicura runtime + backup bucket."""
    import boto3
    from botocore.config import Config
    from dotenv import load_dotenv

    backend_dir = Path(__file__).resolve().parents[1]
    load_dotenv(backend_dir / ".env", override=True)

    endpoint_url = _required_env("S3_ENDPOINT_URL")
    access_key = _required_env("S3_ACCESS_KEY")
    secret_key = _required_env("S3_SECRET_KEY")
    runtime_bucket = _required_env("OBJECT_STORAGE_BUCKET")
    buckets = tuple(
        dict.fromkeys(
            (
                runtime_bucket,
                _required_env("BACKUP_BUCKET"),
            )
        )
    )
    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        config=Config(s3={"addressing_style": "path"}),
    )

    created = []
    for bucket in buckets:
        if ensure_bucket(client, bucket):
            created.append(bucket)
        print(f"bucket ok: {bucket}")
    configure_runtime_lifecycle(
        client,
        runtime_bucket,
        retention_days=int(os.getenv("EPHEMERAL_RETENTION_DAYS", "7")),
    )
    print(f"lifecycle runtime ok: {runtime_bucket}")
    return created


if __name__ == "__main__":
    provision_configured_buckets()
