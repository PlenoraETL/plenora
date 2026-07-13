"""Audited access helper for file-source blobs."""

from __future__ import annotations

from typing import Optional

from app.core.resources.registry import default_registry
from app.core.resources.types import ResourceRef
from app.core.resources.types import Resource


def load_file_source_blob_bytes(
    *,
    file_source_id: str,
    expected_group_id: str,
    is_superadmin: bool,
) -> Optional[bytes]:
    """Fetch a file-source blob through the Resource Engine.

    Services pass a computed ``is_superadmin`` decision; this helper owns
    ``ResourceRef`` construction so request-facing services do not assemble
    privileged refs directly. The loader remains the audit site for bypassed
    reads.
    """
    ref = ResourceRef(
        kind="file_source_blob",
        file_source_id=file_source_id,
        expected_group_id=expected_group_id,
        is_superadmin=is_superadmin,
    )
    resource = default_registry().get(ref, mode="bytes")
    return resource.data


def load_file_source_blob_stream(
    *,
    file_source_id: str,
    expected_group_id: str,
    is_superadmin: bool,
) -> Resource:
    """Apre il payload come chunk iterator mantenendo il tenant binding."""
    ref = ResourceRef(
        kind="file_source_blob",
        file_source_id=file_source_id,
        expected_group_id=expected_group_id,
        is_superadmin=is_superadmin,
    )
    return default_registry().get(ref, mode="stream")
