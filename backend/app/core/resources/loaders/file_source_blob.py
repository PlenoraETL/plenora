"""File-source blob loader — second Resource Engine kind.

BE-GO-READY-RESOURCE-ENGINE-020-FILE-SOURCE-BLOB-LOADER.

Closes the duplication between the engine's result-blob storage
read path (``app/core/resources/loaders/result_blob.py``) and the
pre-020 service-layer file-source read path (formerly
``app/services/file_blob_materialization.py``, retired in
BE-GO-READY-RESOURCE-ENGINE-024). Both leaf readers shared the
same dispatch shape (storage_key non-null -> object storage;
else PG BYTEA fallback) AND the same bounded-bytes contract; v1
of the engine had only the result loader, leaving the
file-source path as a parallel implementation in the service
layer.

This loader graduates the pilot by adding ``file_source_blob`` as
the second real kind, sharing:

  - the same SQL preflight shape (``length(data)`` projection +
    ``CASE WHEN length(data) > ?`` so oversized BYTEA never
    crosses the PG/Python wire);
  - the same ``read_stream_bounded`` cap on the object-storage
    branch (``RESOURCE_MAX_BYTES``);
  - the same ``MemoryLimitExceededError`` typed propagation on
    cap exceeded (the caller MUST learn about it);
  - the same fail-closed tenant binding (SQL ``WHERE id = ? AND
    group_id = ?`` when ``is_superadmin=False``).

What this loader does NOT own (anti-god-engine):

  - parsing the bytes (CSV/Parquet/Geo dispatch lives in
    ``app/utils/dataframe.py`` and ``app/services/file_load_dispatch``);
  - ZIP extraction for SHP/GDB temp-file materialization (lives
    inline in ``FileSourceService._temp_file_for`` post-024,
    which fetches the blob bytes via this loader's `mode='bytes'`
    surface and then owns the SHP/GDB ZIP extraction + tempdir
    cleanup lifecycle);
  - the service-layer sharing check (``FileSourceService._assert_visible``
    handles owned-vs-shared visibility before this loader is
    invoked);
  - mode='path' / mode='stream' (path is fallback-only; the
    file-source consumers that need a path go through
    ``temp_file_for`` which preserves the SHP/GDB extraction
    contract at the service layer).

Tenant binding contract
-----------------------
Identical to ``ResultBlobLoader``:

  1. SQL row lookup keyed on ``file_source_id`` AND ``group_id``
     (when ``is_superadmin=False``); the WHERE clause itself
     produces the fail-closed: a row owned by another group
     simply does not return.
  2. ``is_superadmin=True`` skips the SQL filter but the bypass
     is recorded in the structured log for audit symmetry. Used
     by ``FileSourceService._get_blob_from_db`` for the
     shared-via-table case where ``_assert_visible`` has
     already authorized the cross-group fetch (see migration
     note in ``file_service.py``).
  3. If the lookup yields no row, return ``(None, "none")`` ->
     loader raises ``FileNotFoundError``.
  4. Dispatch on ``storage_key`` (non-null -> object storage;
     null -> PG BYTEA fallback).
  5. Return a ``Resource(kind='file_source_blob',
     size_bytes=N, data=bytes)``.

Mode support
------------
v1 ships exactly ``mode='bytes'``. ``mode='path'`` and
``mode='stream'`` raise ``ValueError`` — the file-source
consumers that need a path use the SHP/GDB-aware
``FileSourceService._temp_file_for`` (which fetches via this
loader and owns its own zip extraction + tempdir lifecycle); a
generic engine ``mode='path'`` would not preserve those
semantics. Adding a generic path mode (with the
``plenora-file-source-blob-`` prefix + ``cleanup_temp_path``
audit guard mirroring ``result_blob.py``) is residual scope.

Boundary
--------
Pure leaf module, mirroring the result-blob loader:

  - imports: stdlib + ``app.db.pg_connection`` + lazy imports of
    ``app.utils.logger`` and ``app.shared.cloud_backends``;
  - banned: ``pandas`` / ``geopandas`` / ``fiona`` / ``polars`` /
    ``fastapi`` / ``app.core.auth`` / ``app.core.artifacts`` /
    ``app.core.intermediate``.
  - locked by
    ``tests2/shared/test_resource_engine_anti_god_engine_lock.py``.
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

from app.shared.exceptions import MemoryLimitExceededError

from app.core.resources.resource_caps import resolve_resource_max_bytes
from app.core.resources.types import (
    OutputMode,
    Resource,
    ResourceKind,
    ResourceRef,
)
from app.core.resources.loaders._shared import (
    _short_key,
    _download_object_storage_impl,
)


# Cap resolution — single source of truth shared with
# ``result_blob.py`` via
# ``app.core.resources.resource_caps.resolve_resource_max_bytes``
# (BE-GO-READY-MEMORY-ENGINE-006). Both loaders agree on the
# 200 MB default + the invalid/non-positive env fallback in one
# place; the source-bound contract in
# ``tests2/shared/test_resource_caps_centralization_contract.py``
# bans local env parsing here.


# BE-GO-READY-MEMORY-ENGINE-007: the loader-local
# ``_resolve_max_bytes`` compat alias was removed. The canonical
# entry point is ``resolve_resource_max_bytes`` from
# ``app.core.resources.resource_caps`` (already imported above).


# Internal helpers — log key abbreviation + SQL row fetch


def _fetch_row(
    file_source_id: str,
    *,
    max_bytes: int,
    group_id_filter: Optional[str],
) -> Optional[dict]:
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
    from app.db.pg_connection import fetchone

    clauses = ["id = ?"]
    where_params: list = [file_source_id]
    if group_id_filter:
        clauses.append("group_id = ?")
        where_params.append(group_id_filter)

    # Single-line SELECT projection so source-bound contract tests
    # can grep the canonical token as one literal substring.
    sql = (
        "SELECT storage_key, length(data) AS data_size, "
        "CASE "
        "WHEN storage_key IS NOT NULL THEN NULL "
        "WHEN length(data) > ? THEN NULL "
        "ELSE data "
        "END AS data "
        "FROM file_sources WHERE " + " AND ".join(clauses)
    )
    # Parameter order matches placeholder order:
    #   1. CASE WHEN length(data) > ?  -> max_bytes
    #   2. WHERE id = ?                -> file_source_id
    #   3. AND group_id = ?            -> group_id_filter (if set)
    params = (max_bytes, *where_params)
    return fetchone(sql, params)


def _download_object_storage(storage_key: str) -> Optional[bytes]:
    """Object storage GET — thin adapter for ``file_source_blob`` kind.

    Delegates to ``_download_object_storage_impl`` with the
    kind-specific log prefix ``[FS-BLOB]`` and stage
    ``"file_source_blob_oversize"``.  Kept as a module-level name so
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
    # downstream cap enforcement MUST agree on the same cap value.
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

    # BYTEA preflight already nulled out oversize via SQL CASE.
    # If the data_size exceeds the cap, raise loudly (same contract
    # as result_blob): the caller can then decide whether to
    # surface a 413 or downgrade to fail-soft.
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


# FileSourceBlobLoader — Protocol-compliant loader for `file_source_blob`


class FileSourceBlobLoader:
    """File-source blob loader. Handles ``kind="file_source_blob"``.

    v1 supports ``mode="bytes"`` only. ``mode="path"`` and
    ``mode="stream"`` raise ``ValueError`` -- the file-source
    consumers that need a filesystem path use
    ``FileSourceService._temp_file_for`` which fetches the bytes
    via this loader and owns its SHP/GDB ZIP-extraction lifecycle
    inline (post BE-GO-READY-RESOURCE-ENGINE-024 the legacy
    ``app.services.file_blob_materialization`` helper module was
    retired).

    Tenant binding (mirror of ``ResultBlobLoader``):
      - ``is_superadmin=False`` -> SQL ``WHERE group_id = ?``
        filter; row owned by another group does not return.
      - ``is_superadmin=True``  -> SQL filter skipped; bypass
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
        if mode != "bytes":
            # path / stream are deferred. v1 keeps the surface narrow:
            # the file-source consumers that need a path (SHP/GDB
            # extraction) call FileSourceService._temp_file_for at
            # the service layer, which fetches via this loader and
            # owns its own zip-aware lifecycle.
            raise ValueError(
                f"FileSourceBlobLoader.load: mode={mode!r} not supported. "
                "v1 supports mode='bytes' only; path/stream are deferred. "
                "For path-mode SHP/GDB consumers, use "
                "`FileSourceService._temp_file_for` (post-024 lives "
                "inline in the service, not in a separate module)."
            )

        # Tenant binding:
        #   - is_superadmin=False -> SQL filter on group_id (fail-closed:
        #     a row owned by another group does not return).
        #   - is_superadmin=True  -> SQL filter skipped; the bypass is
        #     recorded in the audit log for symmetry.
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

        data, backend = _dispatch_by_id(
            ref.file_source_id,
            group_id_filter=group_id_filter,
        )
        if backend == "none":
            raise FileNotFoundError(
                f"FileSourceBlobLoader.load: file_source_id="
                f"{ref.file_source_id!r} not found (or not visible to "
                f"group_id={ref.expected_group_id!r})"
            )

        # data may legitimately be None when object storage is
        # disabled or download failed. Surface as size_bytes=0 with
        # data=None so the caller can branch (mirrors result_blob).
        size = len(data) if data else 0
        return Resource(
            kind="file_source_blob",
            size_bytes=size,
            data=data,
            format_hint=None,
            is_temporary=False,
        )


__all__ = ["FileSourceBlobLoader"]
