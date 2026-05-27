"""Object storage boundary I/O.

Front door stabile per object storage cloud-agnostic (boto3/MinIO/S3).
Il core passa da questo modulo invece di importare direttamente il layer
infrastrutturale.

Public surface (runtime data plane):
  - `get_runtime_object_storage()`: factory runtime per result/IMDF/
    eseguibili. Solleva `ObjectStorageDisabledError` se la capability
    globale e' off.
  - `object_storage_enabled()`: True se la capability e' attiva.
  - `ObjectStorage`: Protocol API contract.
  - `ObjectStorageDisabledError`: raised dalla factory.
  - `ObjectMeta`: TypedDict shape comune.

Public surface (specializzati — operational/cross-cluster):
  - `get_backup_object_storage()`: factory dedicato al backup bucket
    (usato da `admin_backups`).
  - `get_log_archive_storage()`: factory dedicato all'archive log
    (usato da `beat_scheduler`).
  - `aws_credentials_error_message(exc)`: error message formatter
    per credential-related boto3 errors (usato da result blob writer).
"""

from __future__ import annotations

from importlib import import_module

from app.infrastructure.storage.cloud_backends import (
    ObjectMeta,
    ObjectStorage,
    ObjectStorageDisabledError,
)


def _cloud_backends():
    return import_module("app.infrastructure.storage.cloud_backends")


def aws_credentials_error_message(exc: Exception) -> str:
    return _cloud_backends().aws_credentials_error_message(exc)


def get_backup_object_storage():
    return _cloud_backends().get_backup_object_storage()


def get_log_archive_storage():
    return _cloud_backends().get_log_archive_storage()


def get_runtime_object_storage():
    return _cloud_backends().get_runtime_object_storage()


def object_storage_enabled() -> bool:
    return _cloud_backends().object_storage_enabled()

__all__ = [
    "ObjectMeta",
    "ObjectStorage",
    "ObjectStorageDisabledError",
    "aws_credentials_error_message",
    "get_backup_object_storage",
    "get_log_archive_storage",
    "get_runtime_object_storage",
    "object_storage_enabled",
]
