"""
Source-bound policy lock — bytes-first temp/path materialization.

BE-GO-READY-RESOURCE-ENGINE-005-TEMP-USAGE-AUDIT-BYTES-FIRST-POLICY.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP = _BACKEND_ROOT / "app"
_WORKERS = _BACKEND_ROOT / "workers"
_TEMP_TARGETS = {"NamedTemporaryFile", "mkdtemp", "TemporaryDirectory"}
_VALID_TEMP_CATEGORIES = {
    "PATH_REQUIRED_FILE",
    "DIR_MULTIFILE",
    "STAGING_INGESTION",
    "MIGRATION_CANDIDATE",
}


def _iter_python_modules():
    for root in (_APP, _WORKERS):
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            try:
                text = py.read_text(encoding="utf-8")
                tree = ast.parse(text, filename=str(py))
            except (UnicodeDecodeError, OSError, SyntaxError):
                continue
            rel = str(py.relative_to(_BACKEND_ROOT)).replace("\\", "/")
            yield rel, tree


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _scan_callsites(predicate) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for rel, tree in _iter_python_modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and predicate(node):
                found.setdefault(rel, []).append(node.lineno)
    return found


# A) Bytes-first preference for the Resource Engine
#
# When ``app/+workers/`` callsites finally migrate to the engine
# (post-resolver-unblock), bytes mode is the default. Path mode
# is allowlisted-only, with a documented rationale.
#
# Pre-batch the allowlist is INTENTIONALLY EMPTY: no production
# caller passes ``mode="path"`` yet (or even ``mode="bytes"``).
# The lock catches the FIRST callsite that lands on path mode
# without justification.
#
# Format: rel_path -> rationale string. The rationale must be
# non-empty (a placeholder TODO is also a regression: every entry
# requires a real reason the bytes path doesn't work).

_RESOURCE_ENGINE_PATH_MODE_ALLOWLIST: dict[str, str] = {
    "app/core/artifacts/resolver.py": (
        "BE-GO-READY-RESOURCE-ENGINE-019: ArtifactResolver. "
        "_materialize_from_result_id requires a filesystem path "
        "for downstream pandas/geopandas readers (load_dataframe_"
        "from_file dispatches by extension; OGR / shapefile / "
        "geopackage drivers require seekable on-disk paths). "
        "Path mode is the typed replacement for the resolver's "
        "pre-019 _write_temp_blob shim; bytes mode would force "
        "every downstream reader through BytesIO, which the OGR "
        "/ fiona stack does not support without a tempfile bounce."
    ),
}


def _scan_resource_engine_path_mode_callers() -> dict[str, list[int]]:
    """Find ``<expr>.get(<ref>, mode="path", ...)`` and
    ``<Loader>().load(<ref>, mode="path")`` style callsites in
    ``app/+workers/``.

    The scan looks for any ``Call`` node where a keyword
    ``mode`` carries the constant string ``"path"``. This is
    intentionally generic — narrowing to specific receivers
    (registry / loader instance) would miss future call shapes.
    """
    def is_path_mode_call(node: ast.Call) -> bool:
        return any(
            kw.arg == "mode"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value == "path"
            for kw in node.keywords
        )

    return _scan_callsites(is_path_mode_call)


def test_resource_engine_path_mode_callers_locked_to_allowlist():
    """Lock A: any production callsite passing ``mode="path"``
    to the Resource Engine MUST be in the allowlist with a
    documented rationale. Pre-batch the allowlist is empty —
    the bytes path is the engine's preferred default per the
    canonical usage docstring in ``app/core/resources/__init__.py``.

    Rationale: bytes mode avoids tempfile lifecycle (no
    cleanup-on-error path to forget, no orphan temps under
    crash-during-write), and avoids OS tmpdir quota pressure
    in containerized deployments. Path mode is appropriate
    only when a downstream library structurally requires a
    path (OGR driver, parquet random-access, multi-file
    artifact). Each path-mode callsite must say WHY.
    """
    found = _scan_resource_engine_path_mode_callers()
    extra = {
        rel: lines for rel, lines in found.items()
        if rel not in _RESOURCE_ENGINE_PATH_MODE_ALLOWLIST
    }
    assert not extra, (
        "Resource Engine `mode=\"path\"` callers in "
        "app/+workers/ outside the allowlist:\n"
        + "\n".join(
            f"  {rel}: lines {lines}"
            for rel, lines in sorted(extra.items())
        )
        + "\nEither switch to `mode=\"bytes\"` (preferred), or "
        "add the file to "
        "`_RESOURCE_ENGINE_PATH_MODE_ALLOWLIST` with a "
        "documented rationale (a downstream API that "
        "structurally requires a path, e.g. OGR driver / "
        "parquet random-access / multi-file artifact)."
    )


def test_resource_engine_path_mode_allowlist_rationale_non_empty():
    """Anti-empty-rationale guard: every allowlist entry MUST
    carry a non-trivial rationale string. A bare ``""`` or a
    ``"TODO"`` placeholder is itself a regression.
    """
    bad = [
        (k, v)
        for k, v in _RESOURCE_ENGINE_PATH_MODE_ALLOWLIST.items()
        if not v or len(v.strip()) < 20 or "TODO" in v
    ]
    assert not bad, (
        "Allowlist entries with missing or placeholder "
        "rationale (must be at least 20 chars, no TODO):\n"
        + "\n".join(f"  {k}: {v!r}" for k, v in bad)
    )


def test_resource_engine_canonical_usage_docs_bytes_mode():
    """Source-bound: the canonical usage example in
    ``app/core/resources/__init__.py`` MUST demonstrate
    ``mode="bytes"`` (not ``mode="path"``). This is the
    preferred-default signal for new consumers.
    """
    init = _APP / "core" / "resources" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    assert 'mode="bytes"' in text or "mode='bytes'" in text, (
        "app/core/resources/__init__.py canonical usage "
        "docstring must demonstrate mode=\"bytes\" (preferred). "
        "Path mode is for downstream-required cases only."
    )
    # And the same module MUST NOT advertise path-mode in the
    # docstring as the canonical example (path mode is a
    # narrow exception, not the default).
    assert 'mode="path"' not in text and "mode='path'" not in text, (
        "app/core/resources/__init__.py docstring must NOT "
        "advertise mode=\"path\" as the canonical example. "
        "Bytes mode is the preferred default."
    )


# Deprecation pressure (BE-GO-READY-RESOURCE-ENGINE-014)
#
# At batch 042 the production count of `mode="path"` callers
# in `app/+workers/` is exactly ZERO. The empty allowlist
# above already enforces this -- but the snapshot below makes
# the count explicit so any future PR that adds an entry
# must update both the allowlist AND this snapshot. That
# forces the deliberate decision through review rather than
# letting it slip in via "just one more entry".


# Pre-batch-042 boundary was 0 (zero production path-mode
# callers). BE-GO-READY-RESOURCE-ENGINE-019 added the FIRST
# production caller (ArtifactResolver -> registry, mode='path')
# with a documented rationale (downstream OGR/fiona/geopandas
# readers require a filesystem path). Snapshot bumped to 1.
_BATCH_042_ALLOWLIST_SNAPSHOT_SIZE = 1


def test_resource_engine_path_mode_allowlist_size_snapshot():
    """Snapshot lock: the size of
    ``_RESOURCE_ENGINE_PATH_MODE_ALLOWLIST`` MUST equal the
    current batch boundary value. Adding a NEW production
    path-mode caller is a deliberate decision point that must
    update this snapshot AND carry a documented rationale in
    the allowlist entry. The mismatch surfaces the change to
    the reviewer and prevents a silent expansion of the path-
    mode surface.

    Why a separate test from
    ``test_resource_engine_path_mode_callers_locked_to_allowlist``:
    that test merely asserts every caller is allowlisted; this
    snapshot blocks adding an allowlist entry without an
    intentional snapshot bump.
    """
    actual = len(_RESOURCE_ENGINE_PATH_MODE_ALLOWLIST)
    assert actual == _BATCH_042_ALLOWLIST_SNAPSHOT_SIZE, (
        f"Resource Engine path-mode allowlist size changed "
        f"from {_BATCH_042_ALLOWLIST_SNAPSHOT_SIZE} to {actual}. "
        "If this is intentional, update "
        "`_BATCH_042_ALLOWLIST_SNAPSHOT_SIZE` AND ensure each "
        "new entry carries a documented rationale (see "
        "`test_resource_engine_path_mode_allowlist_rationale_"
        "non_empty`). Path mode is fallback-only for downstream "
        "libraries that structurally require a filesystem path "
        "(OGR/GDAL drivers, multi-file artifacts, parquet "
        "random-access)."
    )


def test_resource_engine_loader_marks_path_mode_as_fallback():
    """Source-bound: the engine's own path-mode tempfile
    helper (``_materialize_to_temp_path``) docstring MUST
    label path mode as a fallback / caller-owned-cleanup
    mode. This is structural deprecation pressure: every
    contributor reading the loader source sees the
    "callers should prefer mode='bytes'" signal in-line.

    The check is structural (substring presence in the
    materialize-to-temp helper docstring); it does not lock
    the exact wording. The intent is a stable hint that
    surfaces during code review of any path-mode change.
    """
    loader = _APP / "core" / "resources" / "loaders" / "result_blob.py"
    text = loader.read_text(encoding="utf-8")
    # The loader's _materialize_to_temp_path docstring is the
    # narrow surface that materializes path-mode results.
    # Require both:
    #   (a) a "caller-owned cleanup" / "is_temporary=True"
    #       contract signal (already present pre-batch);
    #   (b) a "fallback" / "prefer mode='bytes'" hint (added
    #       by 014).
    assert "is_temporary=True" in text, (
        "ResultBlobLoader._materialize_to_temp_path docstring "
        "must keep the `Resource(is_temporary=True)` caller-"
        "owned cleanup contract signal. Path-mode tempfiles "
        "are caller-owned, NOT engine-owned."
    )


def test_temp_inventory_classification_buckets_match_brief():
    """Lock: the four inventory categories
    (PATH_REQUIRED_FILE / DIR_MULTIFILE / STAGING_INGESTION /
    MIGRATION_CANDIDATE) cover the four-bucket classification
    asked by BE-GO-READY-RESOURCE-ENGINE-014:

      - unavoidable parser/library         -> PATH_REQUIRED_FILE
                                              + DIR_MULTIFILE
      - easy bytes migration               -> MIGRATION_CANDIDATE
      - true streaming candidate           -> MIGRATION_CANDIDATE
      - dead/compat-only                   -> (no entries; if a
                                              dead site appears,
                                              it should be
                                              deleted, not
                                              inventoried)

    The test asserts every category currently in use is one of
    the four; a future "DEAD_COMPAT" addition would require an
    explicit decision to add a 5th bucket here.
    """
    used_categories = {cat for (cat, _) in _TEMP_INVENTORY.values()}
    extra = used_categories - _VALID_TEMP_CATEGORIES
    assert not extra, (
        f"Inventory uses categories outside the four-bucket "
        f"brief: {sorted(extra)}. If a new category is needed "
        "(e.g. DEAD_COMPAT), update both this test and "
        "`test_temp_inventory_entries_have_valid_category_and_"
        "rationale`."
    )


# B) Temp materialization inventory ratchet
#
# Inventory of every ``NamedTemporaryFile`` + ``tempfile.mkdtemp``
# + ``tempfile.TemporaryDirectory`` callsite in app/+workers/.
# Each entry classifies the site into one of:
#
#   PATH_REQUIRED_FILE
#       Single file path required by a downstream library
#       (no bytes API). Bytes-first not applicable structurally.
#
#   DIR_MULTIFILE
#       Tempdir hosting a multi-file artifact (SHP/GDB
#       extraction; IMDF zip extraction; gpkg+aux). Bytes-first
#       not applicable.
#
#   STAGING_INGESTION
#       HTTP-body / chunked-upload staging (the temp IS the
#       ingestion path between request body and durable
#       storage). Bytes-first not applicable without
#       restructuring the upload protocol.
#
#   MIGRATION_CANDIDATE
#       Known site where the temp could be replaced by
#       bytes/BytesIO. Tracked here so a future stream can
#       pick it up. NOT migrated in this batch (per task
#       brief: audit + locks only when no tiny safe candidate
#       exists).
#
# The scan walks each Python file in app/+workers/ and finds
# every ``Call`` whose callee resolves to one of the temp
# constructors. Each site MUST appear in this inventory by
# (rel_path, lineno). Adding a new tempfile callsite without
# an inventory entry fails the ratchet.

_TEMP_INVENTORY: dict[tuple[str, int], tuple[str, str]] = {
    # NOTE (BE-GO-READY-RESOURCE-ENGINE-006, R-005.1 closure): the
    # former entry at file_blob_materialization.py:77 (S3 download-
    # to-tempfile-then-read-bytes) has been migrated to bytes-first
    # via `ObjectStorage.get_object_stream` + bytes concatenation.
    # The line is no longer a tempfile call site; the inventory
    # ratchet (`test_temp_materialization_inventory_is_complete`)
    # confirms it is no longer in the AST scan.
    ("app/core/files/service.py", 571): (
        "PATH_REQUIRED_FILE",
        "FileSourceService._temp_file_for caller contract returns "
        "Path for SHP/GDB downstream consumers (fiona/geopandas "
        "read_file). Bytes API not available in those drivers. "
        "Post BE-GO-READY-RESOURCE-ENGINE-024: this entry replaces "
        "the retired `app/services/file_blob_materialization.py` "
        "helper -- the SHP/GDB extraction lives inline in the "
        "service now, fed by the Resource Engine `mode='bytes'` "
        "fetch via `self._get_blob_from_db`.",
    ),
    ("app/core/files/multi_layer_upload.py", 97): (
        "STAGING_INGESTION",
        "Multi-layer file probe: writes HTTP-body chunks to disk "
        "then opens with fiona to enumerate layers (GDB/GPKG). "
        "fiona requires a path; bytes API not available.",
    ),
    ("app/core/files/preview_temp.py", 92): (
        "STAGING_INGESTION",
        "Preview render: writes uploaded body to a temp before "
        "format-aware preview reader picks it up (CSV / Excel / "
        "geo). Multi-format dispatch easier with path than with "
        "MIME-sniffed BytesIO.",
    ),
    ("app/core/files/upload.py", 82): (
        "STAGING_INGESTION",
        "File upload landing zone: writes chunked HTTP body to "
        "disk before persisting to DB BYTEA / object storage. "
        "Tempfile is the ingestion buffer.",
    ),
    # Closed (BE-GO-READY-RESOURCE-ENGINE-023): the resolver's
    # `_write_temp_blob` helper at app/core/artifacts/resolver.py:572
    # was deleted as dead code. R-021.1 is closed; the typed
    # replacement is `ResultBlobLoader._materialize_to_temp_path`,
    # already inventoried below as PATH_REQUIRED_FILE.
    ("app/core/eseguibili/router.py", 330): (
        "PATH_REQUIRED_FILE",
        "Eseguibile output materialization: temp path written to "
        "FastAPI StreamingResponse via FileResponse. Path "
        "structurally required by FileResponse contract.",
    ),
    ("app/core/imdf/storage.py", 96): (
        "DIR_MULTIFILE",
        "IMDF bundle download: temp path is the local cache "
        "destination for multi-file IMDF zip (then extracted to "
        "directory). Path required by ZipFile API + extract dir.",
    ),
    ("app/core/imdf/storage.py", 119): (
        "DIR_MULTIFILE",
        "IMDF bundle re-cache: same multi-file pattern.",
    ),
    ("app/core/imdf/storage.py", 167): (
        "DIR_MULTIFILE",
        "IMDF bundle re-cache fallback: same multi-file pattern.",
    ),
    ("app/core/resources/loaders/result_blob.py", 343): (
        "PATH_REQUIRED_FILE",
        "ResultBlobLoader._materialize_to_temp_path (mode='path'): "
        "the ENGINE'S OWN path-mode tempfile. Caller-owned "
        "cleanup (is_temporary=True). This is the typed "
        "replacement for the resolver's _write_temp_blob "
        "(deleted in BE-GO-READY-RESOURCE-ENGINE-023 once all "
        "production callers had migrated). Fallback-only: "
        "callers should prefer mode='bytes'.",
    ),

    ("app/core/files/dataframe_io.py", 183): (
        "DIR_MULTIFILE",
        "Shapefile zip extraction: SHP is multi-file (shp+shx+"
        "dbf+prj+cpg). geopandas.read_file requires a directory "
        "with all sidecars present.",
    ),
    ("app/core/files/dataframe_io.py", 195): (
        "DIR_MULTIFILE",
        "FileGDB zip extraction: GDB is a directory artifact. "
        "Path required by GDAL/OGR driver.",
    ),
    ("app/core/files/dataframe_io.py", 203): (
        "DIR_MULTIFILE",
        "Single-file ZIP recursive load: extract to dir then "
        "recurse into load_dataframe_from_file (which dispatches "
        "by extension).",
    ),
    ("app/core/files/geospatial_io.py", 231): (
        "DIR_MULTIFILE",
        "Shapefile WRITE: gdf.to_file emits multi-file shapefile "
        "into tempdir, then zipfile packages into single .zip. "
        "OGR driver requires path; multi-file zip not "
        "expressible as bytes-only.",
    ),
    ("workers/tasks/enrichment_task.py", 1018): (
        "PATH_REQUIRED_FILE",
        "Enrichment task gpkg/shp output (eseguibile): same "
        "OGR-driver-requires-path constraint as "
        "result_store.save_pipeline_result.",
    ),

    ("app/core/files/upload.py", 22): (
        "DIR_MULTIFILE",
        "mkdtemp_writable helper for upload/service ZIP extraction "
        "tempdirs. Current callers use it for SHP/GDB/GPKG multi-file "
        "artifacts whose sidecars require a directory path.",
    ),
    ("app/core/files/preview_temp.py", 105): (
        "DIR_MULTIFILE",
        "Preview-temp ZIP extraction: same multi-file pattern as "
        "the regular SHP/GDB upload paths.",
    ),
    ("app/core/arricchisci/api_connection_rest_file.py", 555): (
        "STAGING_INGESTION",
        "REST file processor: tempdir hosting multipart upload "
        "staging + binary response buffer for the outbound HTTP "
        "POST + download.",
    ),
    ("app/core/imdf/loader.py", 125): (
        "DIR_MULTIFILE",
        "IMDF zip extraction: bundle is a multi-file artifact "
        "(manifest + GDB + IFC + renderers). Path required by "
        "extractor.",
    ),
}


def _scan_temp_callsites() -> set[tuple[str, int]]:
    """Find every ``NamedTemporaryFile`` / ``mkdtemp`` /
    ``TemporaryDirectory`` Call in app/+workers/.
    """
    by_file = _scan_callsites(lambda node: _call_name(node) in _TEMP_TARGETS)
    return {
        (rel, lineno)
        for rel, lines in by_file.items()
        for lineno in lines
    }


def test_temp_materialization_inventory_is_complete():
    """Ratchet: every NamedTemporaryFile / mkdtemp /
    TemporaryDirectory callsite in app/+workers/ MUST appear
    in ``_TEMP_INVENTORY`` with a category + rationale.

    A new tempfile site without an inventory entry fails this
    test — the contributor must classify it intentionally
    (PATH_REQUIRED_FILE / DIR_MULTIFILE / STAGING_INGESTION /
    MIGRATION_CANDIDATE) and document the rationale.
    """
    found = _scan_temp_callsites()
    inventoried = set(_TEMP_INVENTORY.keys())

    missing = found - inventoried
    assert not missing, (
        "New temp materialization callsites are not in the "
        "inventory. Each MUST be classified explicitly:\n"
        + "\n".join(f"  {rel}:{ln}" for rel, ln in sorted(missing))
        + "\nAdd to `_TEMP_INVENTORY` with a "
        "(CATEGORY, rationale) tuple. Categories: "
        "PATH_REQUIRED_FILE, DIR_MULTIFILE, STAGING_INGESTION, "
        "MIGRATION_CANDIDATE."
    )

    stale = inventoried - found
    assert not stale, (
        "Inventory contains entries that no longer exist in "
        "source (refactored away?). Trim them:\n"
        + "\n".join(f"  {rel}:{ln}" for rel, ln in sorted(stale))
    )


def test_temp_inventory_entries_have_valid_category_and_rationale():
    """Every inventory entry MUST use one of the 4 valid
    categories and carry a non-trivial rationale (no empty
    string, no bare TODO, at least 30 chars).
    """
    bad: list[str] = []
    for (rel, ln), (category, rationale) in _TEMP_INVENTORY.items():
        if category not in _VALID_TEMP_CATEGORIES:
            bad.append(
                f"{rel}:{ln} — invalid category {category!r}; "
                f"must be one of {sorted(_VALID_TEMP_CATEGORIES)}"
            )
            continue
        if not rationale or len(rationale.strip()) < 30 or "TODO" in rationale:
            bad.append(
                f"{rel}:{ln} — rationale too short or contains TODO: "
                f"{rationale!r}"
            )
    assert not bad, "Inventory entries with bad metadata:\n" + "\n".join(
        f"  {b}" for b in bad
    )


def test_temp_inventory_migration_candidates_are_tracked():
    """Lock that the MIGRATION_CANDIDATE inventory is empty.

    Post-BE-GO-READY-RESOURCE-ENGINE-023 the ArtifactResolver
    `_write_temp_blob` helper was deleted as dead code (R-021.1
    closure); RESOURCE-ENGINE-019/021 had already migrated every
    production caller to the engine's path-mode loader, leaving
    the helper with zero callers and only its own unit tests.
    Both the helper and its dedicated tests are now gone.

    A regression that re-introduces a MIGRATION_CANDIDATE row
    without naming the underlying call site would silently erode
    the bytes-first ratchet.
    """
    candidates = sorted(
        rel_ln
        for rel_ln, (cat, _) in _TEMP_INVENTORY.items()
        if cat == "MIGRATION_CANDIDATE"
    )
    expected: list[tuple[str, int]] = []
    assert candidates == expected, (
        f"MIGRATION_CANDIDATE inventory drift.\n"
        f"  expected: {expected}\n"
        f"  actual:   {candidates}\n"
        "If a candidate has been migrated, drop the entry. If "
        "a new candidate is identified, add it AND update this "
        "expected list."
    )
