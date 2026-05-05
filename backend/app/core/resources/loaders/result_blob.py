"""Result-blob loader — pilot Resource Engine kind.

BE-GO-READY-RESOURCE-ENGINE-002-RESULT-BLOB-LOADER-PILOT.
BE-GO-READY-RESOURCE-ENGINE-004-RESULT-BLOB-PATH-MODE.

Closes the duplication between
``app/db/results_db_service.py::get_result_data`` and
``app/db/results_db_service.py::_s3_download_bytes``. Both leaf
readers shared the same dispatch pattern (storage_key non-null ->
object storage; else PG BYTEA fallback) and the same byte-level
output. The loader now owns:

  - the SQL row lookup with optional ``group_id`` filter;
  - the BYTEA-vs-object-storage dispatch;
  - the structured timing log shape;
  - tempfile materialization for ``mode='path'`` (stdlib-only).

What this loader does NOT own (anti-god-engine):

  - parsing the bytes (CSV/Parquet/Geo dispatch lives in
    ``app/utils/dataframe.py``);
  - DataFrame / GeoDataFrame construction;
  - run-artifact materialization (``ArtifactResolver`` owns it;
    its project-level tenant guard is orthogonal to the engine's
    group-level guard — see audit R-003.1);
  - delete / save / list (those are `storage_reconciliation` /
    `result_store` / domain-specific endpoints).

Tenant binding contract
-----------------------
The Protocol-compliant ``load(ref, mode)`` is **fail-closed** by
construction (``ResourceRef.__post_init__`` rejects empty
``expected_group_id``). The full path:

  1. SQL row lookup keyed on ``result_id`` AND ``group_id`` (if
     ``is_superadmin=False``); the WHERE clause itself produces
     the fail-closed: a row owned by another group simply does
     not return.
  2. ``is_superadmin=True`` skips the SQL filter but the bypass
     is recorded in the structured log for audit symmetry.
  3. If the lookup yields no row, raise ``FileNotFoundError``.
  4. Dispatch on ``storage_key`` (non-null -> object storage;
     null -> PG BYTEA fallback).
  5. Return a ``Resource`` matching the requested ``OutputMode``.

Back-compat shim
----------------
``app/db/results_db_service.py::get_result_data(result_id,
group_id=None)`` keeps its public surface. When ``group_id`` is
None, the shim calls the loader's package-private
``_dispatch_by_id(result_id, group_id_filter=None)`` helper that
shares the dispatch code but skips the fail-closed type-level
guard. New callers should use ``ResourceRef`` directly via the
registry; old callers (router endpoints, project_service) keep
working without atomic migration.

Boundary
--------
Pure leaf module:

  - imports: stdlib + ``app.db.pg_connection`` + lazy imports of
    ``app.utils.logger`` and ``app.shared.cloud_backends``;
  - banned: ``pandas`` / ``geopandas`` / ``fiona`` / ``polars`` /
    ``fastapi`` / ``app.core.auth`` / ``app.core.artifacts``.
  - locked by
    ``tests2/shared/test_resource_engine_anti_god_engine_lock.py``.
"""

from __future__ import annotations

import os as _os
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

from app.core.resources.types import (
    OutputMode,
    Resource,
    ResourceKind,
    ResourceRef,
)


# ---------------------------------------------------------------------------
# Internal helpers — log key abbreviation + SQL row fetch
# ---------------------------------------------------------------------------


def _short_key(storage_key: Optional[str], max_len: int = 60) -> str:
    """Abbrevia una storage_key per log: prime 12 + '...' + ultimi 30 char.

    Mirror of the helper in ``results_db_service`` so the loader's log
    line shape stays identical to the pre-pilot logs (no ops dashboard
    breakage).
    """
    if not storage_key:
        return "<none>"
    if len(storage_key) <= max_len:
        return storage_key
    return f"{storage_key[:12]}...{storage_key[-30:]}"


def _fetch_row(
    result_id: str,
    *,
    group_id_filter: Optional[str],
) -> Optional[dict]:
    """SELECT data, storage_key FROM results WHERE id = ? [AND group_id = ?].

    `group_id_filter=None` -> no WHERE clause on group_id (back-compat
    fail-open path; only used by the legacy shim).
    """
    from app.db.pg_connection import fetchone

    if group_id_filter:
        return fetchone(
            "SELECT data, storage_key FROM results "
            "WHERE id = ? AND group_id = ?",
            (result_id, group_id_filter),
        )
    return fetchone(
        "SELECT data, storage_key FROM results WHERE id = ?",
        (result_id,),
    )


def _download_object_storage(storage_key: str) -> Optional[bytes]:
    """Object storage GET. Returns None on disabled capability or
    download failure (best-effort logging; mirrors pre-pilot
    ``_s3_download_bytes`` behavior bit-for-bit)."""
    from app.utils.logger import get_logger

    logger = get_logger(__name__)
    _t0 = time.monotonic()
    try:
        from app.shared.cloud_backends import (
            ObjectStorageDisabledError,
            get_runtime_object_storage,
        )

        try:
            storage = get_runtime_object_storage()
        except ObjectStorageDisabledError as _disabled:
            duration_ms = round((time.monotonic() - _t0) * 1000, 1)
            logger.warning(
                f"[FS-4] S3 download skip su {_short_key(storage_key)}: "
                f"object storage disabilitato (duration_ms={duration_ms}). "
                f"{_disabled}"
            )
            return None
        stream = storage.get_object_stream(storage_key)
        chunks = list(stream["body"])
        data = b"".join(chunks)
        duration_ms = round((time.monotonic() - _t0) * 1000, 1)
        logger.info(
            f"[PERF] s3_download op=get_bytes "
            f"storage_key={_short_key(storage_key)} "
            f"size_bytes={len(data)} duration_ms={duration_ms}"
        )
        return data
    except Exception as e:
        duration_ms = round((time.monotonic() - _t0) * 1000, 1)
        logger.error(
            f"[FS-4] S3 download {_short_key(storage_key)} fallito "
            f"(duration_ms={duration_ms}): {e}",
            exc_info=True,
        )
        return None


def _dispatch_by_id(
    result_id: str,
    *,
    group_id_filter: Optional[str],
) -> Tuple[Optional[bytes], str]:
    """Shared dispatch core: SQL row lookup + BYTEA-vs-S3 branch.

    Returns ``(data | None, backend_tag)`` where ``backend_tag`` is
    one of ``"object_storage"``, ``"db_legacy"``, or ``"none"`` (no
    row).

    This is the canonical de-duplicated dispatch. Both the
    Protocol-compliant ``ResultBlobLoader.load()`` and the
    back-compat shim in ``results_db_service.get_result_data``
    funnel through here.
    """
    from app.utils.logger import get_logger

    logger = get_logger(__name__)
    _t0 = time.monotonic()

    row = _fetch_row(result_id, group_id_filter=group_id_filter)
    if not row:
        return None, "none"

    storage_key = row.get("storage_key")
    if storage_key:
        data = _download_object_storage(storage_key)
        duration_ms = round((time.monotonic() - _t0) * 1000, 1)
        size = len(data) if data else 0
        logger.info(
            f"[PERF] get_result_data op=fetch_blob backend=object_storage "
            f"result_id={result_id} size_bytes={size} duration_ms={duration_ms}"
        )
        return data, "object_storage"

    data = row.get("data")
    if isinstance(data, memoryview):
        data = bytes(data)
    duration_ms = round((time.monotonic() - _t0) * 1000, 1)
    size = len(data) if data else 0
    logger.info(
        f"[PERF] get_result_data op=fetch_blob backend=db_legacy "
        f"result_id={result_id} size_bytes={size} duration_ms={duration_ms}"
    )
    return data, "db_legacy"


# ---------------------------------------------------------------------------
# Path-mode helper: write bytes to a stdlib tempfile (anti-god-engine: no
# settings imports, no domain-specific tempdir resolution; the engine
# returns a raw stdlib tempfile path and the caller owns its lifecycle
# via Resource.is_temporary=True).
# ---------------------------------------------------------------------------


def _materialize_to_temp_path(data: bytes) -> Path:
    """Write ``data`` to a fresh tempfile and return its Path.

    Stdlib-only: ``tempfile.NamedTemporaryFile(delete=False)``. The
    caller is responsible for cleanup (the loader returns
    ``Resource(is_temporary=True)`` to signal that contract).

    Cleanup on write failure: the partial tempfile is closed and
    unlinked best-effort, then the original exception is re-raised.
    Mirrors the hardening pattern used by
    ``ArtifactResolver._write_temp_blob`` (disk-full / permission /
    EROFS surface as the original I/O exception, never as a leaked
    half-written tempfile).

    The loader does NOT honor a domain-specific TEMP_DIR (e.g.
    ``settings.TEMP_DIR``) on purpose: the anti-god-engine rule is
    that the resource engine has zero ``app.config`` imports.
    Callers that need a specific tempdir override can either set
    the stdlib ``TMPDIR`` / ``TEMP`` / ``TMP`` env vars (which
    ``tempfile`` honors transparently) or copy the result to their
    preferred root after the loader returns.
    """
    tf = tempfile.NamedTemporaryFile(delete=False)
    path = Path(tf.name)
    try:
        tf.write(data)
    except Exception:
        try:
            tf.close()
        except Exception:
            pass
        try:
            if path.exists():
                _os.unlink(path)
        except Exception:
            pass
        raise
    else:
        tf.close()
        return path


# ---------------------------------------------------------------------------
# ResultBlobLoader — Protocol-compliant fail-closed entry point
# ---------------------------------------------------------------------------


class ResultBlobLoader:
    """Loader for ``ResourceKind = "result_blob"``.

    Stateless. Constructed once at registry build time and shared
    across the process.
    """

    @property
    def kind(self) -> ResourceKind:
        return "result_blob"

    def load(self, ref: ResourceRef, *, mode: OutputMode) -> Resource:
        if ref.kind != "result_blob":
            raise ValueError(
                f"ResultBlobLoader does not handle kind={ref.kind!r}. "
                "Use the registry to dispatch to the correct loader."
            )
        if not ref.result_id:
            raise ValueError(
                "ResultBlobLoader.load: ref.result_id required "
                f"(got {ref.result_id!r})."
            )
        if mode not in ("bytes", "path"):
            # bytes (RESOURCE-ENGINE-002) + path (RESOURCE-ENGINE-004).
            # `stream` stays unsupported — adding it requires a
            # safe Iterator[bytes] passthrough for both BYTEA (already
            # fully read into memory) and object storage (we currently
            # consume the stream into a list inside _download_object_storage).
            # Implementing it without re-reading or losing seekability
            # is non-trivial; deferred to a follow-up stream.
            raise ValueError(
                f"ResultBlobLoader.load: mode={mode!r} not supported. "
                "v1 supports mode='bytes' and mode='path'; stream is "
                "deferred to a follow-up stream."
            )

        # Tenant binding (shared between bytes and path modes):
        #   - is_superadmin=False -> SQL filter on group_id (fail-closed:
        #     a row owned by another group does not return).
        #   - is_superadmin=True  -> SQL filter skipped; the bypass is
        #     recorded in the audit log for symmetry.
        if ref.is_superadmin:
            from app.utils.logger import get_logger

            get_logger(__name__).info(
                f"[AUDIT] ResultBlobLoader.load is_superadmin=True "
                f"result_id={ref.result_id} mode={mode} "
                f"expected_group_id={ref.expected_group_id!r}"
            )
            group_id_filter: Optional[str] = None
        else:
            group_id_filter = ref.expected_group_id

        # Single dispatch site: bytes and path modes share the SAME
        # fail-closed lookup + tenant filter + BYTEA/S3 branch.
        # No second SQL query; no second tenant check; no parallel
        # dispatch path. The mode only affects the materialization
        # step (in-memory bytes vs tempfile path).
        data, backend = _dispatch_by_id(
            ref.result_id, group_id_filter=group_id_filter
        )
        if backend == "none":
            raise FileNotFoundError(
                f"ResultBlobLoader.load: result_id={ref.result_id!r} "
                f"not found (or not visible to group_id={ref.expected_group_id!r})"
            )

        if mode == "bytes":
            # data may legitimately be None when object storage is
            # disabled or download failed. The pre-pilot caller
            # treated this as "blob not available"; we surface it as
            # size_bytes=0 with data=None so the caller can branch.
            # The Resource envelope always carries size_bytes; a
            # None-data Resource is a signal "row exists but blob
            # unavailable".
            size = len(data) if data else 0
            return Resource(
                kind="result_blob",
                size_bytes=size,
                data=data,
                format_hint=None,
                is_temporary=False,
            )

        # mode == "path"
        # In path mode a None-data Resource is NOT useful: callers
        # asking for a path want a file they can open. Surface the
        # "row exists but blob unavailable" case as FileNotFoundError
        # rather than silently materializing a zero-byte tempfile
        # (that would hide object-storage outages from the caller).
        if data is None:
            raise FileNotFoundError(
                f"ResultBlobLoader.load: result_id={ref.result_id!r} "
                "blob data unavailable (object storage download "
                "returned None or row carries empty BYTEA). "
                "mode='path' cannot materialize a missing blob."
            )
        tmp_path = _materialize_to_temp_path(data)
        return Resource(
            kind="result_blob",
            size_bytes=len(data),
            path=tmp_path,
            format_hint=None,
            is_temporary=True,
        )


__all__ = ["ResultBlobLoader"]
