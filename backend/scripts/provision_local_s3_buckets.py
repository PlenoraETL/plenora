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
    buckets = tuple(
        dict.fromkeys(
            (
                _required_env("OBJECT_STORAGE_BUCKET"),
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
    return created


if __name__ == "__main__":
    provision_configured_buckets()
