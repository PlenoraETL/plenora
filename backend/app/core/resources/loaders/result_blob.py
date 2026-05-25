"""
Result-blob loader — pilot Resource Engine kind.

"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from app.core.resources.exceptions import MemoryLimitExceededError

from app.core.resources.resource_caps import resolve_resource_max_bytes
from app.core.resources.types import (
    OutputMode,
    Resource,
    ResourceKind,
    ResourceRef,
)
from app.core.resources.loaders._shared import (
    _download_object_storage_impl,
)
from app.core.resources.loaders.result_blob_store import (
    fetch_result_blob_lookup_row,
)


# bound the previously unbounded `b"".join(list(stream["body"]))` read
# The pre-010 path materialized the entire object-storage iterator into
# memory before the loader knew whether the blob was 100 KB or 100 GB
# That made the engine an OOM amplifier: any caller of
# ``mode='bytes'`` / ``mode='path'`` could be DoS'd by an oversized
# result row
# 010 inverts the read: chunks are accumulated into a `bytearray` with
# a per-chunk pre-check; the iterator is aborted as soon as the cap
# would be exceeded. The cap is read from
# ``RESOURCE_MAX_BYTES`` env via the shared
# ``app.core.resources.resource_caps.resolve_resource_max_bytes``
# helper ; the helper handles the
# 200 MB default + invalid / non-positive fallback in one place so
# this loader and ``file_source_blob.py`` cannot drift on the
# resolution rules
# Anti-god-engine note: NO ``app.config`` import (the helper is
# stdlib-only too). The loader continues to read env via the helper
# rather than importing ``app.config`` from a leaf module


# the loader-local
# ``_resolve_max_bytes`` compat alias was removed. The canonical
# entry point is ``resolve_resource_max_bytes`` from
# ``app.core.resources.resource_caps`` (already imported above).
# Tests and canonical result-blob readers call the helper directly.


# Internal helpers — log key abbreviation + SQL row fetch


@dataclass(frozen=True)
class ResultBlobLookupRow:
    """Typed row for result blob dispatch."""

    storage_key: Optional[str]
    project_id: Optional[str]
    data_size: Optional[int]
    data: Any

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ResultBlobLookupRow":
        return cls(
            storage_key=_optional_str(row.get("storage_key")),
            project_id=_optional_str(row.get("project_id")),
            data_size=_optional_int(row.get("data_size")),
            data=row.get("data"),
        )


def _optional_str(value: object) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> Optional[int]:
    if value is None:
        return None
    return int(value)


def _fetch_row(
    result_id: str,
    *,
    max_bytes: int,
    group_id_filter: Optional[str],
    project_id_filter: Optional[str] = None,
) -> Optional[ResultBlobLookupRow]:
    """SELECT row metadata + bounded BYTEA via SQL preflight.

     (R-010.1 closure): the BYTEA
    cap is enforced **inside the SQL** via a CASE expression that
    NULLs out ``data`` when ``length(data) > max_bytes``. The
    oversized BYTEA payload therefore never crosses the PG/Python
    wire -- pre-013 the row was loaded into the psycopg2 driver
    before the cap could fire. ``data_size`` (= ``length(data)``)
    is also returned so the dispatch raises with the actual
    observed size and an audit log records the preflight value.

    Returns a row dict with keys:

      - ``storage_key``: ``Optional[str]``
      - ``project_id``: ``Optional[str]``
      - ``data_size``: ``Optional[int]`` -- ``length(data)``;
        ``NULL`` when ``data`` is ``NULL`` (e.g. on the
        object-storage row branch or a degenerate empty row)
      - ``data``: ``Optional[bytes]`` -- ALWAYS ``NULL`` when
        ``storage_key`` is set OR when ``length(data) > max_bytes``
        (the wire-level preflight contract)

    `group_id_filter=None` -> no WHERE clause on group_id (back-compat
    fail-open path; only used by the legacy shim).

    `project_id_filter=None` (default) -> no WHERE clause on
    project_id (back-compat for existing callers). Non-empty
    string -> additional AND filter (RESOURCE-ENGINE-007 row-metadata
    project-level binding). The clause is built dynamically so the
    SQL uses native psycopg2 `%s` placeholders for every value (no string
    interpolation; psycopg2 quotes every filter).

    SQL shape note:
    ``length(data)`` is referenced twice (once for the ``data_size``
    projection, once inside the CASE). PostgreSQL stores the
    uncompressed byte length in the BYTEA varlena header, so
    ``length(data)`` is an O(1) header read regardless of TOAST;
    the dual evaluation cost is negligible relative to a wire
    transfer of the bytes themselves. Single-query was preferred
    over a metadata-then-data two-step both to avoid the extra
    round trip and to keep the read atomic (no race window between
    preflight and fetch where the row could be mutated).
    """
    clauses = ["id = %s"]
    where_params: list = [result_id]
    if group_id_filter:
        clauses.append("group_id = %s")
        where_params.append(group_id_filter)
    if project_id_filter:
        clauses.append("project_id = %s")
        where_params.append(project_id_filter)

    # SQL fragment layout note: the SELECT projection is kept on
    # a single source line so source-bound contract tests
    # anti-god-engine lock) can grep the canonical token
    # ``storage_key, project_id, length(data) AS data_size`` as
    # one literal substring. The CASE / WHERE / FROM clauses span
    # multiple lines for readability
    sql = (
        "SELECT storage_key, project_id, length(data) AS data_size, "
        "CASE "
        "WHEN storage_key IS NOT NULL THEN NULL "
        "WHEN length(data) > %s THEN NULL "
        "ELSE data "
        "END AS data "
        "FROM results WHERE " + " AND ".join(clauses)
    )
    # Parameter order matches placeholder order in the SQL text
    # 1. CASE WHEN length(data) > %s -> max_bytes
    # 2. WHERE id = %s -> result_id
    # 3. AND group_id = %s -> group_id_filter (if set
    # 4. AND project_id = %s -> project_id_filter (if set
    params = (max_bytes, *where_params)
    row = fetch_result_blob_lookup_row(sql, params)
    if row is None:
        return None
    return ResultBlobLookupRow.from_mapping(row)


def _download_object_storage(storage_key: str) -> Optional[bytes]:
    """Object storage GET — thin adapter for ``result_blob`` kind.

    Delegates to ``_download_object_storage_impl`` with the
    kind-specific log prefix ``[FS-4]`` and stage
    ``"result_blob_oversize"``. Kept as a module-level name so
    existing monkeypatch targets (tests that patch
    ``app.core.resources.loaders.result_blob._download_object_storage``)
    continue to work unchanged.
    """
    return _download_object_storage_impl(
        storage_key,
        log_prefix="[FS-4]",
        stage="result_blob_oversize",
    )


def _dispatch_by_id(
    result_id: str,
    *,
    group_id_filter: Optional[str],
    project_id_filter: Optional[str] = None,
) -> Tuple[Optional[bytes], str]:
    """Shared dispatch core: SQL row lookup + BYTEA-vs-S3 branch.

    Returns ``(data | None, backend_tag)`` where ``backend_tag`` is
    one of ``"object_storage"``, ``"db_legacy"``, or ``"none"`` (no
    row).

    This is the canonical de-duplicated dispatch. Both
    ``ResultBlobLoader.load`` and ``app.core.results.blob.get_result_data``
    funnel through here.

    ``project_id_filter`` (RESOURCE-ENGINE-007) is row-metadata
    only: the SQL ``WHERE project_id = %s`` clause is what
    enforces the binding. The fail-closed surface is identical to
    the group-level filter — a row owned by another project
    simply does not return (becomes ``"none"`` backend tag).
    """
    from app.utils.logger import get_logger

    logger = get_logger(__name__)
    _t0 = time.monotonic()
    # Resolve once and pass through; the SQL preflight + the
    # downstream cap enforcement MUST agree on the same cap value per
    # dispatch. A runtime env mutation between two
    # `resolve_resource_max_bytes` calls would otherwise create a
    # tiny window of disagreement that would be hard to reason about
    max_bytes = resolve_resource_max_bytes()

    row = _fetch_row(
        result_id,
        max_bytes=max_bytes,
        group_id_filter=group_id_filter,
        project_id_filter=project_id_filter,
    )
    if not row:
        return None, "none"

    storage_key = row.storage_key
    if storage_key:
        data = _download_object_storage(storage_key)
        duration_ms = round((time.monotonic() - _t0) * 1000, 1)
        size = len(data) if data else 0
        logger.info(
            f"[PERF] get_result_data op=fetch_blob backend=object_storage "
            f"result_id={result_id} size_bytes={size} duration_ms={duration_ms}"
        )
        return data, "object_storage"

    # R-010.1 closure): BYTEA cap
    # is now SQL preflight. ``data_size`` carries the authoritative
    # ``length(data)`` projection; the SQL CASE has already nulled
    # out ``data`` if oversize, so the BYTEA payload never crossed
    # the PG/Python wire. Pre-013 the cap was post-fetch (PG had
    # already loaded the bytes into the driver before any check
    # could run
    data_size = row.data_size
    if data_size is not None and data_size > max_bytes:
        duration_ms = round((time.monotonic() - _t0) * 1000, 1)
        logger.error(
            f"[PERF] get_result_data op=fetch_blob backend=db_legacy "
            f"result_id={result_id} ABORTED oversize: "
            f"observed_bytes={data_size} limit_bytes={max_bytes} "
            f"duration_ms={duration_ms} preflight=length(data)"
        )
        raise MemoryLimitExceededError(
            observed_bytes=data_size,
            limit_bytes=max_bytes,
            path_basename=f"result_id={result_id}",
            stage="result_blob_oversize",
        )

    data = row.data
    if isinstance(data, bytearray):
        data = bytes(data)
    if isinstance(data, memoryview):
        data = bytes(data)
    elif data is not None and not isinstance(data, bytes):
        raise TypeError(
            "ResultBlobLookupRow.data must be bytes, memoryview, "
            f"bytearray or None; got {type(data).__name__}"
        )
    duration_ms = round((time.monotonic() - _t0) * 1000, 1)
    size = len(data) if data else 0
    logger.info(
        f"[PERF] get_result_data op=fetch_blob backend=db_legacy "
        f"result_id={result_id} size_bytes={size} duration_ms={duration_ms}"
    )
    return data, "db_legacy"


# ResultBlobLoader — Protocol-compliant fail-closed entry point.
#
# Post-Phase5: mode="path" rimossa (zero production caller). Il loader
# materializza solo bytes in-memory; chi ha bisogno di un path filesystem
# deve usare `app.core.io.materialize.temporary_path_from_bytes` come
# context manager (escape hatch esplicito al boundary I/O).


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
        if mode != "bytes":
            # Post-Phase5: solo `bytes` supportato. Path mode rimossa
            # (zero production caller; chi ha bisogno di un path deve
            # usare `app.core.io.materialize.temporary_path_from_bytes`
            # come context manager).
            raise ValueError(
                f"ResultBlobLoader.load: mode={mode!r} not supported. "
                "Only mode='bytes' is accepted (path mode removed)."
            )

        # Tenant binding:
        # is_superadmin=False -> SQL filter on group_id (fail-closed,
        # a row owned by another group does not return).
        # is_superadmin=True -> SQL filter skipped; the bypass is
        # recorded in the audit log for symmetry.
        if ref.is_superadmin:
            from app.utils.logger import get_logger

            get_logger(__name__).info(
                f"[AUDIT] ResultBlobLoader.load is_superadmin=True "
                f"result_id={ref.result_id} mode={mode} "
                f"expected_group_id={ref.expected_group_id!r} "
                f"expected_project_id={ref.expected_project_id!r}"
            )
            group_id_filter: Optional[str] = None
        else:
            group_id_filter = ref.expected_group_id

        # RESOURCE-ENGINE-007: optional row-metadata project-level
        # binding. None => no project filter (back-compat for
        # every existing caller). Non-empty => SQL `AND project_id
        # = %s`. is_superadmin does NOT bypass project filter when
        # the caller explicitly opts in: the audit-bypass semantic
        # only applies to the primary group filter; project
        # binding is a row-level invariant the caller asked for
        project_id_filter: Optional[str] = ref.expected_project_id or None

        # Single dispatch site: bytes and path modes share the SAME
        # fail-closed lookup + tenant filter + BYTEA/S3 branch
        # No second SQL query; no second tenant check; no parallel
        # dispatch path. The mode only affects the materialization
        # step (in-memory bytes vs tempfile path
        data, backend = _dispatch_by_id(
            ref.result_id,
            group_id_filter=group_id_filter,
            project_id_filter=project_id_filter,
        )
        if backend == "none":
            raise FileNotFoundError(
                f"ResultBlobLoader.load: result_id={ref.result_id!r} "
                f"not found (or not visible to "
                f"group_id={ref.expected_group_id!r}"
                + (
                    f", project_id={ref.expected_project_id!r}"
                    if ref.expected_project_id
                    else ""
                )
                + ")"
            )

        # mode == "bytes" (l'unico supportato).
        # data=None indica una row visibile ma blob non disponibile
        # per storage remoto disabilitato o download fallito.
        size = len(data) if data else 0
        return Resource(
            kind="result_blob",
            size_bytes=size,
            data=data,
            format_hint=None,
            is_temporary=False,
        )


__all__ = ["ResultBlobLoader"]
