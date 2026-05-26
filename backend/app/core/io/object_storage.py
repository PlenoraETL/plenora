"""Object storage facade — boundary I/O orizzontale (re-export).

Re-esporta i punti di ingresso canonical per object storage
cloud-agnostic (boto3/MinIO/S3) dal layer `app/infrastructure/storage/`.
Consolidamento orizzontale (2026-05-26): prima i 11+ caller in
`app/core/*` facevano deep import diretto su
`from app.infrastructure.storage.cloud_backends import ...`; ora
passano per questo modulo, allineato con gli altri boundary I/O
(`vsimem`, `zip_guards`, `geospatial`).

Public surface (i 5 symbol piu' usati dal core layer):
  - `get_runtime_object_storage()`: factory runtime, solleva
    `ObjectStorageDisabledError` se la capability globale e' off.
  - `object_storage_enabled()`: True se la capability e' attiva
    (consultata dai caller che degradano graceful).
  - `ObjectStorage`: Protocol con l'API contract
    (`upload_bytes` / `download` / `delete` / `list_objects` /
    `get_object_stream` / `head_object`).
  - `ObjectStorageDisabledError`: raised quando l'oggetto storage
    non e' configurato (es. dev senza S3/MinIO).
  - `ObjectMeta`: TypedDict shape comune a tutti i provider.

Symbol meno usati (`get_backup_object_storage`, `get_log_archive_storage`,
`validate_storage_config`, `aws_credentials_error_message`, secrets
backend) restano accessibili via `app.infrastructure.storage.cloud_backends`
diretto — sono operational/cross-layer concerns, non core domain.
"""

from __future__ import annotations

from app.infrastructure.storage.cloud_backends import (
    ObjectMeta,
    ObjectStorage,
    ObjectStorageDisabledError,
    get_runtime_object_storage,
    object_storage_enabled,
)

__all__ = [
    "ObjectMeta",
    "ObjectStorage",
    "ObjectStorageDisabledError",
    "get_runtime_object_storage",
    "object_storage_enabled",
]
