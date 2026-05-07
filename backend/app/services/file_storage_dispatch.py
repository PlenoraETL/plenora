"""KEEP_COMPAT wrapper for file-source blob storage dispatch.

Canonical implementation lives in `app.core.files.storage_dispatch`.
This module remains importable for legacy callers and test patch surfaces.
"""

from __future__ import annotations

from app.core.files.storage_dispatch import (
    _OBJECT_STORAGE_PREFIX,
    BlobDispatchResult,
    dispatch_blob_storage,
)

__all__ = [
    "_OBJECT_STORAGE_PREFIX",
    "BlobDispatchResult",
    "dispatch_blob_storage",
]
