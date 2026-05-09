"""Compatibility re-export for the object-storage infrastructure boundary.

Runtime code must import from ``app.infrastructure.storage.cloud_backends``.
This module remains as a thin re-export for older tests / external callers
that still import from the historical shared path. The previous form used a
``sys.modules`` proxy assignment which is forbidden by
``test_final_compat_surface_guard``; the explicit re-export below carries
the same observable behaviour without the bridge.
"""

from __future__ import annotations

from app.infrastructure.storage.cloud_backends import (  # noqa: F401
    AwsS3ObjectStorage,
    AwsSsmSecretsBackend,
    MinioObjectStorage,
    ObjectMeta,
    ObjectStorage,
    ObjectStorageDisabledError,
    SecretsBackend,
    StorageConfigError,
    aws_credentials_error_message,
    get_backup_object_storage,
    get_log_archive_storage,
    get_object_storage,
    get_runtime_object_storage,
    get_secrets_backend,
    object_storage_enabled,
    validate_storage_config,
)

__all__ = [
    "AwsS3ObjectStorage",
    "AwsSsmSecretsBackend",
    "MinioObjectStorage",
    "ObjectMeta",
    "ObjectStorage",
    "ObjectStorageDisabledError",
    "SecretsBackend",
    "StorageConfigError",
    "aws_credentials_error_message",
    "get_backup_object_storage",
    "get_log_archive_storage",
    "get_object_storage",
    "get_runtime_object_storage",
    "get_secrets_backend",
    "object_storage_enabled",
    "validate_storage_config",
]
