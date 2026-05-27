"""Compatibility re-export for file-source blob storage dispatch."""

from app.core.resources.blob_dispatch import BlobDispatchResult, dispatch_blob_storage

__all__ = ["BlobDispatchResult", "dispatch_blob_storage"]
