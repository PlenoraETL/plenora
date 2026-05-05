"""Architectural guard for storage create/delete side-effect callsites.

BE-GO-READY-STORAGE-ENGINE-021-RECONCILIATION-OUTBOX-AUDIT-FIRST-GUARD.

Goal
====
Prevent new untracked storage side-effects from slipping in. Every
file under `backend/app/` that calls `<storage>.upload(...)`,
`<storage>.upload_bytes(...)` or `<storage>.delete(...)` on the
canonical `ObjectStorage` Protocol must be in this guard's
classification map. A new callsite in a new file fails the test
until the developer:
  - adds the file to `_CALLSITE_CLASSIFICATION` with an explicit
    classification (`tracked` / `safe_no_persistent_artifact` /
    `intentionally_excluded`); OR
  - moves the call behind the canonical
    `app.shared.storage_compensation.compensating_delete` /
    `app.shared.storage_reconciliation.record_pending` boundaries
    (in which case the file moves from a `MISSING` state to
    `tracked` once the dev declares it).

This guard does NOT replace the runtime contract enforced by
`storage_reconciliation.py` + `storage_compensation.py`. It just
prevents new orphans from being silently introduced without an
explicit classification decision.

Out of scope (locked elsewhere)
================================
- Adding `upload_intent` runtime support in the reconciler
  (filtered to `operation='delete'` today; closure path
  `BE-GO-READY-STORAGE-ENGINE-008-UPLOAD-INTENT-PATTERN`).
- Replacing the subsystem with a broker / saga / outbox framework
  (explicitly out of brief).

Classification taxonomy
=======================
- `tracked` - the callsite is paired with `record_pending` +
  `compensating_delete` (or equivalent reconciliation log entry)
  in the same transactional scope.
- `safe_no_persistent_artifact` - the upload/delete is
  self-tracking (e.g. `log_archive` post-upload verify before
  source DELETE).
- `intentionally_excluded` - the artifact is governed by a
  domain-specific lifecycle (S3 lifecycle rules + dedicated
  delete endpoint, paired-DB-row tracker like
  `intermediate.runs.artifact_uri`+`artifact_status`, or
  eseguibili task-bound eager cleanup). Reason MUST be present.
- `missing` - known orphan candidate. The closure path is named in
  the file's entry. NEW files MUST NOT be classified `missing`
  without a paired residual in the audit report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"


Classification = Literal[
    "tracked",
    "safe_no_persistent_artifact",
    "intentionally_excluded",
    "missing",
]


# ---------------------------------------------------------------------------
# Classification map - POST-021 BASELINE
# ---------------------------------------------------------------------------
#
# Keys: `<rel-path-from-backend>` (forward slashes, no leading slash).
# Values: (classification, reason).
#
# A new storage callsite anywhere in `app/` MUST appear here BEFORE the
# guard test will pass. New entries require explicit classification and
# reason. `missing` entries MUST be paired with an audit residual
# (R-021.x) naming the next bounded stream that closes them.
#
# Update protocol:
#   1. add the file path with classification + reason here;
#   2. update `BE_STORAGE_ENGINE_AUDIT.md` if the entry is `missing`;
#   3. tests pass; commit.
#
# DO NOT widen entries to "everything in app/" - the guard is specifically
# designed to surface NEW files that haven't been audited.

_CALLSITE_CLASSIFICATION: dict[str, tuple[Classification, str]] = {
    # --- Tracked: paired record_pending + compensating_delete -----------
    #
    # Note: the consumer files that go through the canonical
    # `compensating_delete` wrapper (results_db_service, file_sources,
    # intermediate/db, file_service) do NOT contain raw `storage.upload(...)`
    # or `storage.delete(...)` calls - they delegate to the helper. The
    # scanner therefore does not flag them, and they are not listed
    # below. The runtime contract for those files is locked separately
    # by `test_storage_reconciliation_boundedness_lock.py`.
    "app/utils/result_store.py": (
        "tracked",
        "save_result_blob and save_result_blob_from_bytes "
        "(upload-then-DB-save). Compensating delete on DB save "
        "failure via _cleanup_uploaded_result_blob. The DB-side "
        "deletes go through results_db_service which records "
        "pending entries pre-commit.",
    ),

    # --- Safe / no persistent artifact ---------------------------------
    "app/services/log_archive.py": (
        "safe_no_persistent_artifact",
        "ADR-0014 contract: post-upload verify "
        "(list_objects(prefix=key) size>0) BEFORE DELETE of source "
        "DB rows. If upload fails, source rows stay; if upload "
        "succeeds + DELETE fails, the worst case is duplicate "
        "rows (rerun safe). No orphan possible by construction.",
    ),

    # --- Intentionally excluded ----------------------------------------
    "app/core/intermediate/artifact_writer.py": (
        "intentionally_excluded",
        "Pair (data.parquet + manifest.json) artifact whose "
        "lifecycle is tracked by intermediate.runs.artifact_uri + "
        "artifact_status. On partial-pair failure status='failed' "
        "becomes the operator-visible signal. Pair atomicity is "
        "tracked as audit residual R-014.6 (next stream "
        "`STORAGE-ENGINE-010-INTERMEDIATE-PAIR-ATOMICITY`).",
    ),
    "app/core/eseguibili/storage.py": (
        "intentionally_excluded",
        "Task-bound output. Lifecycle managed by eager "
        "delete_by_task post-download + S3 lifecycle on "
        "`eseguibili-outputs/` prefix (7-day TTL). The task_id "
        "IS the lifecycle key - no DB-row pairing required.",
    ),
    "app/core/imdf/storage.py": (
        "intentionally_excluded",
        "Bundle pair (zip + meta.json) with dedicated "
        "delete_bundle endpoint + S3 lifecycle on "
        "`imdf-bundles/` prefix. Operator-triggered cleanup; "
        "lifecycle is the safety net.",
    ),
    "app/services/file_storage_dispatch.py": (
        "missing",
        "W2 race: upload-then-DB-save without pre-upload "
        "record_pending(operation='upload_intent'). If process "
        "dies between upload and the file_sources INSERT, the "
        "blob is orphaned with no application-side trail. "
        "Closure path: `BE-GO-READY-STORAGE-ENGINE-008-"
        "UPLOAD-INTENT-PATTERN` - requires paired update to the "
        "reconciler (today filters `operation='delete'` only). "
        "Tracked as audit residual R-021.1.",
    ),
    "app/shared/cloud_backends.py": (
        "intentionally_excluded",
        "ObjectStorage Protocol implementation + docstring "
        "narrative. The `def upload(self, ...)` / `def upload_bytes(...)` "
        "/ `def delete(...)` definitions ARE the Protocol surface "
        "consumed by every other classified callsite; the file also "
        "documents the API in module/class docstrings (e.g. "
        "`ObjectStorage.upload(path, key)`). NOT a business "
        "callsite. Internal SDK boundary.",
    ),
}


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


# Storage-method invocations. `.upload(` and `.upload_bytes(` are
# specific to the ObjectStorage Protocol (no false positives from
# stdlib / third-party APIs). `.delete(` is overloaded across many
# APIs (Redis, dict-likes, files), so we scope it to lines where the
# receiver name contains `storage` to avoid false positives.
_STORAGE_UPLOAD_PATTERNS = (
    ".upload(",
    ".upload_bytes(",
)
_STORAGE_DELETE_RECEIVER_HINTS = (
    "storage.delete(",
    "_storage.delete(",
    "_storage().delete(",
)

# Skip patterns that are NOT storage SDK calls but match the suffix.
# Intentional: the regex used by the original audit deliberately
# matched only the canonical ObjectStorage method names. Keep parity.
_SKIP_FILE_SUFFIXES = ("/__init__.py",)


def _iter_app_py(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if any(str(path).replace("\\", "/").endswith(s) for s in _SKIP_FILE_SUFFIXES):
            continue
        yield path


def _is_storage_method_call(line: str) -> bool:
    """Heuristic: line contains a storage-method invocation on something
    that looks like a storage handle.

    Matches patterns like ``storage.upload(...)``,
    ``self._storage.upload_bytes(...)``, ``target.delete(key)``,
    ``_storage().delete(...)``, ``get_runtime_object_storage().upload(...)``.

    Excludes:
      - lines with leading ``#`` (comments);
      - lines clearly inside a docstring (line stripped starts with
        a triple-quote token or with a leading asterisk);
      - method definitions (``def upload(...)`` / ``def delete(...)``).
    """
    stripped = line.lstrip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return False
    triple_double = chr(34) * 3
    triple_single = chr(39) * 3
    if (
        stripped.startswith(triple_double)
        or stripped.startswith(triple_single)
        or stripped.startswith("*")
    ):
        return False
    if stripped.startswith("def ") or stripped.startswith("async def "):
        return False
    # Method definitions inside a class with annotation
    if "def upload(" in stripped or "def upload_bytes(" in stripped or "def delete(" in stripped:
        return False
    for pat in _STORAGE_UPLOAD_PATTERNS:
        if pat in line:
            return True
    for pat in _STORAGE_DELETE_RECEIVER_HINTS:
        if pat in line:
            return True
    return False


def _scan_for_storage_callsites() -> dict[str, list[tuple[int, str]]]:
    """Return `{rel_path: [(lineno, line), ...]}` for every file under
    `app/` that contains at least one storage method call line."""
    hits: dict[str, list[tuple[int, str]]] = {}
    for path in _iter_app_py(_APP_ROOT):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        file_hits: list[tuple[int, str]] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _is_storage_method_call(line):
                file_hits.append((lineno, line.rstrip()))
        if file_hits:
            rel = str(path.relative_to(_BACKEND_ROOT)).replace("\\", "/")
            hits[rel] = file_hits
    return hits


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_every_storage_callsite_file_is_classified():
    """Each file with `<storage>.upload(...)` / `.upload_bytes(...)` /
    `.delete(...)` MUST be in `_CALLSITE_CLASSIFICATION`."""
    found = _scan_for_storage_callsites()
    unclassified = sorted(set(found.keys()) - set(_CALLSITE_CLASSIFICATION.keys()))

    if unclassified:
        details = "\n".join(
            f"  {p}:\n"
            + "\n".join(f"    line {n}: {l}" for (n, l) in found[p][:3])
            for p in unclassified
        )
        raise AssertionError(
            "Storage create/delete callsite(s) detected in unclassified file(s):\n"
            f"{details}\n\n"
            "Action: add the file to `_CALLSITE_CLASSIFICATION` in this guard with "
            "an explicit classification (tracked / safe_no_persistent_artifact / "
            "intentionally_excluded / missing) and a written reason. If you classify "
            "as `missing`, also add a paired audit residual to "
            "`.agent-temp/BE_STORAGE_ENGINE_AUDIT.md` naming the next bounded stream "
            "that closes the W1/W2 race window."
        )


def test_classification_map_has_no_stale_entries():
    """Every entry in `_CALLSITE_CLASSIFICATION` MUST correspond to a
    file that actually contains storage callsites. Stale entries
    indicate either an erroneous addition or a callsite that was
    refactored away - clean up the map."""
    found = _scan_for_storage_callsites()
    stale = sorted(set(_CALLSITE_CLASSIFICATION.keys()) - set(found.keys()))
    if stale:
        raise AssertionError(
            "Stale entries in `_CALLSITE_CLASSIFICATION` (file no longer "
            f"contains storage callsites):\n  {stale}\n\n"
            "Action: remove the entry. The guard is supposed to surface "
            "ACTIVE callsites only."
        )


def test_classifications_have_non_empty_reasons():
    """Every classification entry MUST have a non-empty reason string.
    `missing` entries MUST mention the closure path (next stream)."""
    for path, (cls, reason) in _CALLSITE_CLASSIFICATION.items():
        assert reason and reason.strip(), (
            f"Classification entry for {path!r} has an empty reason. "
            "All entries require an explicit rationale."
        )
        assert cls in (
            "tracked",
            "safe_no_persistent_artifact",
            "intentionally_excluded",
            "missing",
        ), f"Unknown classification {cls!r} for {path!r}."

        if cls == "missing":
            assert (
                "STORAGE-ENGINE-" in reason
                or "closure path" in reason.lower()
            ), (
                f"`missing` entry for {path!r} must name a closure path / "
                "next bounded stream. Today: "
                f"{reason!r}"
            )


def test_classification_includes_known_tracked_upload_callsites():
    """Sanity: `app/utils/result_store.py` is the only file that
    issues raw `storage.upload(...)` calls behind the
    record_pending + compensating_delete contract. Removing it is
    a regression. The other tracked files (results_db_service,
    file_sources, intermediate/db, file_service) consume the
    canonical `compensating_delete` helper (no raw storage call,
    not in scope of this scanner)."""
    p = "app/utils/result_store.py"
    assert p in _CALLSITE_CLASSIFICATION, (
        f"{p} must be classified as `tracked`. Removal indicates "
        "a regression: result_store no longer participates in the "
        "record_pending + compensating_delete contract."
    )
    cls, _ = _CALLSITE_CLASSIFICATION[p]
    assert cls == "tracked", (
        f"{p} downgraded from `tracked` to {cls!r}. Investigate."
    )


def test_no_silently_added_missing_classifications():
    """Soft cap: at most ONE `missing` classification is allowed
    without an audit-document update. Adding a 2nd `missing` here
    means the OUTBOX gap is widening; the developer should pause and
    update `BE_STORAGE_ENGINE_AUDIT.md` first.

    Today the only `missing` entry is `app/services/file_storage_dispatch.py`
    (W2 race, R-021.1). Closure path: `STORAGE-ENGINE-008-UPLOAD-INTENT-PATTERN`.
    """
    missing = [
        path
        for path, (cls, _) in _CALLSITE_CLASSIFICATION.items()
        if cls == "missing"
    ]
    # Locked at 1: one known orphan candidate (file_storage_dispatch).
    # Adding more REQUIRES updating this assertion + the audit report.
    assert len(missing) <= 1, (
        f"More than one `missing` classification detected: {missing}. "
        "Update `BE_STORAGE_ENGINE_AUDIT.md` with the new residual + "
        "closure path BEFORE relaxing this assertion. The cap exists "
        "to keep the OUTBOX gap from widening silently."
    )
