"""Typed contracts for the Resource Engine.

BE-GO-READY-RESOURCE-ENGINE-002-RESULT-BLOB-LOADER-PILOT.
BE-GO-READY-RESOURCE-ENGINE-020-FILE-SOURCE-BLOB-LOADER.

Only four canonical names live here:
  - ``ResourceKind``        : Literal of supported kinds
                              (v1: result_blob, file_source_blob).
  - ``ResourceRef``         : frozen typed reference (kind + id + tenant).
  - ``Resource``            : frozen result envelope (bytes|path|stream).
  - ``ResourceLoader``      : Protocol implemented per kind.
  - ``ResourceRegistry``    : Protocol that routes ref -> loader.

Boundary
--------
Pure leaf module:
  - stdlib only;
  - zero `pandas` / `geopandas` / `fiona` / `polars` /
    `fastapi` / `app.core.auth` / `app.core.artifacts` imports.

Locked by `tests2/shared/test_resource_engine_anti_god_engine_lock.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# ResourceKind
# ---------------------------------------------------------------------------
#
# v1 ships two kinds (`result_blob`, `file_source_blob`). Other
# kinds remain in the audit (sec. 2.1) and join only after their
# tenant binding is plumbed through. Adding a kind to this Literal
# without adding the matching loader registration in `registry.py`
# is a regression (the registry's `get()` raises `KeyError` for
# unregistered kinds).

ResourceKind = Literal[
    "result_blob",
    "file_source_blob",
]


OutputMode = Literal["bytes", "path", "stream"]


# ---------------------------------------------------------------------------
# ResourceRef — frozen typed reference, fail-closed by construction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceRef:
    """Typed reference to a fetchable resource.

    Exactly one of the id fields (``result_id``, ``file_source_id``,
    ``artifact_uri``) is set; the kind determines which. Mutual
    exclusion is checked by ``__post_init__`` (locked by the
    anti-god-engine guard test).

    ``expected_group_id`` is REQUIRED and must be non-empty:
    every loader call is fail-closed by construction. If a caller
    legitimately needs to bypass the tenant check (admin scan,
    background reconciliation), it MUST pass ``is_superadmin=True``
    AND a non-empty ``expected_group_id`` (the bypass is recorded
    in the loader's structured log for audit symmetry).

    ``expected_project_id`` is OPTIONAL (default ``None``).
    BE-GO-READY-RESOURCE-ENGINE-007: when set to a non-empty
    string, the loader applies a SECONDARY row-level filter on
    the resource's `project_id` column (in addition to the
    primary `group_id` filter). When ``None``, no project-level
    filter is applied — back-compat for every existing caller.

    The project-level check is intentionally row-metadata only:
    the loader compares the value against the column on the
    resource row. There is NO call into project services, no
    project lifecycle check, no recompute path. This narrow
    contract preserves the engine's anti-god-engine boundary —
    no imports from projects/auth/FastAPI/dataframe/GIS — while
    unlocking ``ArtifactResolver`` migration where the resolver
    needs both group AND project tenant binding (its
    project-level guard is orthogonal to the engine's
    group-level guard, see audit R-003.1).
    """

    kind: ResourceKind
    expected_group_id: str

    # mutually-exclusive id fields. Exactly one MUST be set.
    result_id: Optional[str] = None
    file_source_id: Optional[str] = None
    artifact_uri: Optional[str] = None

    is_superadmin: bool = False

    # Optional project-level binding (RESOURCE-ENGINE-007). None
    # means no project-level filter. Non-empty string adds an
    # AND filter on the resource row's `project_id` column.
    expected_project_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.expected_group_id:
            raise ValueError(
                "ResourceRef.expected_group_id required (fail-closed). "
                "Pass an empty string ONLY in legacy back-compat shims "
                "that explicitly opt out of the engine path."
            )
        active = sum(
            1
            for v in (self.result_id, self.file_source_id, self.artifact_uri)
            if v
        )
        if active != 1:
            raise ValueError(
                f"ResourceRef must have exactly one id set "
                f"(result_id|file_source_id|artifact_uri); got {active}. "
                f"kind={self.kind!r} ref={self!r}"
            )


# ---------------------------------------------------------------------------
# Resource — frozen result envelope, byte-level only
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Resource:
    """Result of a loader call. Byte-level only.

    Exactly one of ``data`` / ``path`` / ``stream`` is populated
    (matches the requested ``OutputMode``). ``size_bytes`` is
    always populated; loaders raise rather than return an unsized
    resource.

    Anti-god-engine invariant: this dataclass NEVER carries a
    parsed object (DataFrame, GeoDataFrame, dict-of-rows). Domain
    parsing belongs to higher-level wrappers (``SourceLoader``,
    ``ArtifactResolver``, ``load_dataframe_from_file``).
    """

    kind: ResourceKind
    size_bytes: int
    data: Optional[bytes] = None
    path: Optional[Path] = None
    # streams are not frozen-friendly (Iterator is consumed). The
    # frozen=True keeps the metadata immutable; the iterator handle
    # itself is single-shot and the caller owns its lifecycle.
    stream: Optional[Iterator[bytes]] = field(default=None, compare=False)
    format_hint: Optional[str] = None
    # path-mode only: the loader has materialized to a temp file
    # that the caller MUST clean up. False means the path is
    # caller-managed (e.g. pre-existing local FS path).
    is_temporary: bool = False


# ---------------------------------------------------------------------------
# ResourceLoader Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ResourceLoader(Protocol):
    """Per-kind loader. Stateless. No domain interpretation.

    A loader's contract:
      1. Validate ``ref.expected_group_id`` against the resource's
         tenant binding. Fail-closed on mismatch (raise, do not
         return None or empty bytes).
      2. Resolve the storage backing (DB BYTEA vs object storage
         vs local FS) via the appropriate dispatch.
      3. Honor size caps relevant to the kind. Caps are enforced
         loudly: `MemoryLimitExceededError` (or kind-specific typed
         error) for over-cap, NOT a soft `None`.
      4. Materialize into the requested ``OutputMode``. A loader
         MAY refuse a mode it cannot satisfy (e.g. ``stream`` over
         a BYTEA payload it has already fully read into memory) by
         raising; the caller is responsible for asking for a mode
         the loader can deliver.

    Out-of-scope (anti-god-engine):
      - parsing the bytes (csv vs parquet vs shp);
      - extracting ZIPs;
      - building DataFrames / GeoDataFrames;
      - returning preview-as-rows;
      - feature flag dispatch beyond what `cloud_backends` already
        encapsulates.
    """

    @property
    def kind(self) -> ResourceKind:
        """The single kind this loader handles. Used by the
        registry for routing."""
        ...

    def load(self, ref: ResourceRef, *, mode: OutputMode) -> Resource:
        """Load the referenced resource in the requested mode.

        Raises:
            ValueError on mode the loader cannot satisfy.
            PermissionError on tenant mismatch (fail-closed).
            FileNotFoundError on missing resource.
            Other typed errors per loader contract.
        """
        ...


# ---------------------------------------------------------------------------
# ResourceRegistry Protocol
# ---------------------------------------------------------------------------


class ResourceRegistry(Protocol):
    """Dispatcher only. Holds {kind -> ResourceLoader}.

    The registry does NOT do tenant checking, size guarding, or
    backing dispatch; it just routes a `ResourceRef` to the
    kind-specific loader. Each loader owns its own checks.

    Anti-god-engine: the registry MUST NOT grow `parse()`,
    `delete()`, `save()`, `list()`, or any other capability beyond
    `register` + `get`. Adding methods here is the canonical
    god-engine failure mode.
    """

    def register(self, loader: ResourceLoader) -> None:
        """Register a loader for its declared kind. Re-registering
        the same kind raises."""
        ...

    def get(self, ref: ResourceRef, *, mode: OutputMode) -> Resource:
        """Route to the loader matching `ref.kind`. Raises
        `KeyError` if the kind has no registered loader."""
        ...


__all__ = [
    "OutputMode",
    "Resource",
    "ResourceKind",
    "ResourceLoader",
    "ResourceRef",
    "ResourceRegistry",
]
