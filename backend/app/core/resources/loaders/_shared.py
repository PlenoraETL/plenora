"""
Shared micro-utilities for Resource Engine loaders.

"""

from __future__ import annotations

import time
from typing import Optional

from app.core.resources.exceptions import MemoryLimitExceededError
from app.core.resources.bounded_reader import read_stream_bounded
from app.core.resources.resource_caps import resolve_resource_max_bytes

__all__: list[str] = []  # intentionally empty; callers import directly


def _short_key(key: Optional[str], max_len: int = 60) -> str:
    """Abbrevia una chiave per log: prime 12 + '...' + ultimi 30 char.

    Used by both loaders so log line shapes stay consistent across
    kinds (no ops dashboard breakage when switching backends).
    """
    if not key:
        return "<none>"
    if len(key) <= max_len:
        return key
    return f"{key[:12]}...{key[-30:]}"


def _download_object_storage_impl(
    storage_key: str,
    *,
    log_prefix: str,
    stage: str,
) -> Optional[bytes]:
    """Object storage GET, bounded by ``resolve_resource_max_bytes()``.

    Shared core called by ``result_blob._download_object_storage`` and
    ``file_source_blob._download_object_storage``. Each loader passes
    its own ``log_prefix`` and ``stage`` so log lines and cap-error
    reports stay kind-specific.

    Contract (identical to the per-loader copies it replaces):

    - Returns ``None`` on disabled capability or download failure
      (best-effort logging; preserves pre-010 connectivity-error
      semantics bit-for-bit).
    - Raises :class:`MemoryLimitExceededError` on cap exceeded -
      this is NOT swallowed by the outer broad-except because oversize
      is a security/resource cap signal, not a connectivity issue, and
      the caller MUST learn about it.
    - The bounded read aborts iteration as soon as a chunk would push
      the running size over the cap; subsequent chunks are NOT consumed.

    Parameters
    ----------
    storage_key:
        Object-storage key passed to ``get_object_stream``.
    log_prefix:
        Ops-visible tag prepended to every log line:
        ``"[FS-4]"`` for ``result_blob``,
        ``"[FS-BLOB]"`` for ``file_source_blob``.
    stage:
        Stage name passed to ``read_stream_bounded`` for
        :class:`MemoryLimitExceededError` reporting:
        ``"result_blob_oversize"`` or ``"file_source_blob_oversize"``.
    """
    from app.utils.logger import get_logger
    from app.shared.cloud_backends import (
        ObjectStorageDisabledError,
        get_runtime_object_storage,
    )

    logger = get_logger(__name__)
    _t0 = time.monotonic()
    max_bytes = resolve_resource_max_bytes()
    try:
        try:
            storage = get_runtime_object_storage()
        except ObjectStorageDisabledError as _disabled:
            duration_ms = round((time.monotonic() - _t0) * 1000, 1)
            logger.warning(
                f"{log_prefix} S3 download skip su {_short_key(storage_key)}: "
                f"object storage disabilitato (duration_ms={duration_ms}). "
                f"{_disabled}"
            )
            return None
        stream = storage.get_object_stream(storage_key)
        # Bounded read: aborts iteration on first cap-exceeding chunk
        # MemoryLimitExceededError propagates past the broad-except
        # below (oversize is a security/resource signal, not a
        # connectivity error
        try:
            data = read_stream_bounded(
                stream["body"],
                max_bytes=max_bytes,
                label=_short_key(storage_key),
                stage=stage,
            )
        except MemoryLimitExceededError as oversize:
            duration_ms = round((time.monotonic() - _t0) * 1000, 1)
            logger.error(
                f"{log_prefix} S3 download {_short_key(storage_key)} ABORTED "
                f"oversize: observed_bytes={oversize.observed_bytes} "
                f"limit_bytes={oversize.limit_bytes} "
                f"duration_ms={duration_ms}"
            )
            raise
        duration_ms = round((time.monotonic() - _t0) * 1000, 1)
        logger.info(
            f"[PERF] s3_download op=get_bytes "
            f"storage_key={_short_key(storage_key)} "
            f"size_bytes={len(data)} duration_ms={duration_ms}"
        )
        return data
    except MemoryLimitExceededError:
        # Oversize is the contract for the caller; do NOT swallow
        raise
    except Exception as e:
        duration_ms = round((time.monotonic() - _t0) * 1000, 1)
        logger.error(
            f"{log_prefix} S3 download {_short_key(storage_key)} fallito "
            f"(duration_ms={duration_ms}): {e}",
            exc_info=True,
        )
        return None
