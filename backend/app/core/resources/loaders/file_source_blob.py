"""
File-source blob loader — second Resource Engine kind.

"""

from __future__ import annotations

import time
from typing import Iterator, Mapping, Optional, Tuple

from app.core.resources.exceptions import MemoryLimitExceededError
from app.shared.contracts.payloads import ObjectMap

from app.core.resources.resource_caps import resolve_resource_max_bytes
from app.core.resources.bounded_reader import iter_stream_bounded
from app.core.resources.types import (
    OutputMode,
    Resource,
    ResourceKind,
    ResourceRef,
)
from app.core.resources.loaders._shared import (
    _download_object_storage_impl,
)
from app.core.resources.stores.file_source_blob import (
    fetch_file_source_blob_lookup_row,
)


# Cap resolution — single source of truth shared with
# ``result_blob.py`` via
# ``app.core.resources.resource_caps.resolve_resource_max_bytes``
# Both loaders agree on the
# 200 MB default + the invalid/non-positive env fallback in one
# place; the source-bound contract in
# ``tests2/shared/test_resource_caps_centralization_contract.py``
# bans local env parsing here


# the loader-local
# ``_resolve_max_bytes`` compat alias was removed. The canonical
# entry point is ``resolve_resource_max_bytes`` from
# ``app.core.resources.resource_caps`` (already imported above


# Internal helpers — log key abbreviation + SQL row fetch


def _fetch_row(
    file_source_id: str,
    *,
    max_bytes: int,
    group_id_filter: Optional[str],
) -> Optional[ObjectMap]:
    """SELECT row metadata + bounded BYTEA via SQL preflight.

    Mirror of ``result_blob._fetch_row``: the BYTEA cap is enforced
    INSIDE the SQL via a ``CASE`` expression that NULLs out
    ``data`` when ``length(data) > max_bytes``. The oversized
    BYTEA payload therefore never crosses the PG/Python wire.

    Returns a row dict with keys:

      - ``storage_key``: ``Optional[str]``
      - ``data_size``: ``Optional[int]`` -- ``length(data)``;
        ``NULL`` when ``data`` is ``NULL``
      - ``data``: ``Optional[bytes]`` -- ALWAYS ``NULL`` when
        ``storage_key`` is set OR when ``length(data) > max_bytes``

    ``group_id_filter=None`` -> no WHERE clause on group_id (only
    used when ``is_superadmin=True`` at the loader entrypoint, the
    bypass is logged there).
    """
    clauses = ["id = %s"]
    where_params: list = [file_source_id]
    if group_id_filter:
        clauses.append("group_id = %s")
        where_params.append(group_id_filter)

    # Single-line SELECT projection so source-bound contract tests
    # can grep the canonical token as one literal substring
    sql = (
        "SELECT storage_key, length(data) AS data_size, "
        "CASE "
        "WHEN storage_key IS NOT NULL THEN NULL "
        "WHEN length(data) > %s THEN NULL "
        "ELSE data "
        "END AS data "
        "FROM file_sources WHERE " + " AND ".join(clauses)
    )
    # Parameter order matches placeholder order
    # 1. CASE WHEN length(data) > %s -> max_bytes
    # 2. WHERE id = %s -> file_source_id
    # 3. AND group_id = %s -> group_id_filter (if set
    params = (max_bytes, *where_params)
    row = fetch_file_source_blob_lookup_row(sql, params)
    return dict(row) if isinstance(row, Mapping) else row


def _download_object_storage(storage_key: str) -> Optional[bytes]:
    """Object storage GET — thin adapter for ``file_source_blob`` kind.

    Delegates to ``_download_object_storage_impl`` with the
    kind-specific log prefix ``[FS-BLOB]`` and stage
    ``"file_source_blob_oversize"``. Kept as a module-level name so
    existing monkeypatch targets on
    ``file_source_blob._download_object_storage`` work unchanged.
    """
    return _download_object_storage_impl(
        storage_key,
        log_prefix="[FS-BLOB]",
        stage="file_source_blob_oversize",
    )


def _dispatch_by_id(
    file_source_id: str,
    *,
    group_id_filter: Optional[str],
) -> Tuple[Optional[bytes], str]:
    """Shared dispatch core: SQL row lookup + BYTEA-vs-S3 branch.

    Returns ``(data | None, backend_tag)`` where ``backend_tag`` is
    one of ``"object_storage"``, ``"db_legacy"``, or ``"none"`` (no
    row).
    """
    from app.utils.logger import get_logger

    logger = get_logger(__name__)
    _t0 = time.monotonic()
    # Resolve once and pass through; the SQL preflight + the
    # downstream cap enforcement MUST agree on the same cap value
    max_bytes = resolve_resource_max_bytes()

    row = _fetch_row(
        file_source_id,
        max_bytes=max_bytes,
        group_id_filter=group_id_filter,
    )
    if not row:
        return None, "none"

    storage_key = row.get("storage_key")
    if storage_key:
        data = _download_object_storage(storage_key)
        duration_ms = round((time.monotonic() - _t0) * 1000, 1)
        size = len(data) if data else 0
        logger.info(
            f"[PERF] get_file_source_blob op=fetch_blob "
            f"backend=object_storage file_source_id={file_source_id} "
            f"size_bytes={size} duration_ms={duration_ms}"
        )
        return data, "object_storage"

    # BYTEA preflight already nulled out oversize via SQL CASE
    # If the data_size exceeds the cap, raise loudly (same contract
    # as result_blob): the caller can then decide whether to
    # surface a 413 or downgrade to fail-soft
    data_size = row.get("data_size")
    if data_size is not None and data_size > max_bytes:
        duration_ms = round((time.monotonic() - _t0) * 1000, 1)
        logger.error(
            f"[PERF] get_file_source_blob op=fetch_blob backend=db_legacy "
            f"file_source_id={file_source_id} ABORTED oversize: "
            f"observed_bytes={data_size} limit_bytes={max_bytes} "
            f"duration_ms={duration_ms} preflight=length(data)"
        )
        raise MemoryLimitExceededError(
            observed_bytes=data_size,
            limit_bytes=max_bytes,
            path_basename=f"file_source_id={file_source_id}",
            stage="file_source_blob_oversize",
        )

    data = row.get("data")
    if isinstance(data, memoryview):
        data = bytes(data)
    duration_ms = round((time.monotonic() - _t0) * 1000, 1)
    size = len(data) if data else 0
    logger.info(
        f"[PERF] get_file_source_blob op=fetch_blob backend=db_legacy "
        f"file_source_id={file_source_id} size_bytes={size} "
        f"duration_ms={duration_ms}"
    )
    return data, "db_legacy"


def _stream_by_id(
    file_source_id: str,
    *,
    group_id_filter: Optional[str],
) -> Tuple[Optional[Iterator[bytes]], int, str]:
    """Dispatch bounded senza concatenare il payload object-storage."""
    from app.utils.logger import get_logger

    logger = get_logger(__name__)
    max_bytes = resolve_resource_max_bytes()
    row = _fetch_row(
        file_source_id,
        max_bytes=max_bytes,
        group_id_filter=group_id_filter,
    )
    if not row:
        return None, 0, "none"

    storage_key = row.get("storage_key")
    if storage_key:
        try:
            from app.core.io.object_storage import get_runtime_object_storage

            opened = get_runtime_object_storage().get_object_stream(storage_key)
            declared_size = int(opened.get("size") or 0)
            if declared_size > max_bytes:
                raise MemoryLimitExceededError(
                    observed_bytes=declared_size,
                    limit_bytes=max_bytes,
                    path_basename=f"file_source_id={file_source_id}",
                    stage="file_source_blob_oversize",
                )
            bounded = iter_stream_bounded(
                opened.get("body", ()),
                max_bytes=max_bytes,
                label=f"file_source_id={file_source_id}",
                stage="file_source_blob_oversize",
            )
            return bounded, declared_size, "object_storage"
        except MemoryLimitExceededError:
            raise
        except Exception as exc:
            logger.error(
                "[FS-BLOB] apertura stream fallita file_source_id=%s: %s",
                file_source_id,
                exc,
                exc_info=True,
            )
            return None, 0, "object_storage"

    data_size = row.get("data_size")
    if data_size is not None and data_size > max_bytes:
        raise MemoryLimitExceededError(
            observed_bytes=data_size,
            limit_bytes=max_bytes,
            path_basename=f"file_source_id={file_source_id}",
            stage="file_source_blob_oversize",
        )
    data = row.get("data")
    if isinstance(data, memoryview):
        data = bytes(data)
    if data is None:
        return None, 0, "db_legacy"
    if not isinstance(data, bytes):
        raise TypeError(
            "FileSource blob legacy deve essere bytes/memoryview/None; "
            f"ricevuto {type(data).__name__}"
        )

    chunk_size = 1024 * 1024

    def _legacy_chunks() -> Iterator[bytes]:
        for start in range(0, len(data), chunk_size):
            yield data[start:start + chunk_size]

    return _legacy_chunks(), len(data), "db_legacy"


# FileSourceBlobLoader — Protocol-compliant loader for `file_source_blob`


class FileSourceBlobLoader:
    """File-source blob loader. Handles ``kind="file_source_blob"``.

    Supporta ``bytes`` per compatibilita' e ``stream`` per il data-plane
    canonico. ``path`` resta intenzionalmente escluso.

    Tenant binding (mirror of ``ResultBlobLoader``):
      - ``is_superadmin=False`` -> SQL ``WHERE group_id = %s``
        filter; row owned by another group does not return.
      - ``is_superadmin=True`` -> SQL filter skipped; bypass
        recorded in the audit log. Used by
        ``FileSourceService._get_blob_from_db`` for the
        shared-via-table case where ``_assert_visible`` already
        authorized the cross-group fetch.
    """

    @property
    def kind(self) -> ResourceKind:
        return "file_source_blob"

    def load(self, ref: ResourceRef, *, mode: OutputMode) -> Resource:
        if ref.kind != "file_source_blob":
            raise ValueError(
                f"FileSourceBlobLoader does not handle kind={ref.kind!r}. "
                "Use the registry to dispatch to the correct loader."
            )
        if not ref.file_source_id:
            raise ValueError(
                "FileSourceBlobLoader.load: ref.file_source_id required "
                f"(got {ref.file_source_id!r})."
            )
        if mode not in ("bytes", "stream"):
            raise ValueError(
                f"FileSourceBlobLoader.load: mode={mode!r} not supported. "
                "Supported modes: 'bytes', 'stream'."
            )

        # Tenant binding
        # is_superadmin=False -> SQL filter on group_id (fail-closed
        # a row owned by another group does not return
        # is_superadmin=True -> SQL filter skipped; the bypass is
        # recorded in the audit log for symmetry
        if ref.is_superadmin:
            from app.utils.logger import get_logger

            get_logger(__name__).info(
                f"[AUDIT] FileSourceBlobLoader.load is_superadmin=True "
                f"file_source_id={ref.file_source_id} mode={mode} "
                f"expected_group_id={ref.expected_group_id!r}"
            )
            group_id_filter: Optional[str] = None
        else:
            group_id_filter = ref.expected_group_id

        if mode == "stream":
            stream, size, backend = _stream_by_id(
                ref.file_source_id,
                group_id_filter=group_id_filter,
            )
            if backend == "none":
                raise FileNotFoundError(
                    f"FileSourceBlobLoader.load: file_source_id="
                    f"{ref.file_source_id!r} not found (or not visible to "
                    f"group_id={ref.expected_group_id!r})"
                )
            return Resource(
                kind="file_source_blob",
                size_bytes=size,
                stream=stream,
                format_hint=None,
                is_temporary=False,
            )

        data, backend = _dispatch_by_id(
            ref.file_source_id, group_id_filter=group_id_filter,
        )
        if backend == "none":
            raise FileNotFoundError(
                f"FileSourceBlobLoader.load: file_source_id="
                f"{ref.file_source_id!r} not found (or not visible to "
                f"group_id={ref.expected_group_id!r})"
            )

        # data may legitimately be None when object storage is
        # disabled or download failed. Surface as size_bytes=0 with
        # data=None so the caller can branch (mirrors result_blob
        size = len(data) if data else 0
        return Resource(
            kind="file_source_blob",
            size_bytes=size,
            data=data,
            format_hint=None,
            is_temporary=False,
        )


__all__ = ["FileSourceBlobLoader"]
