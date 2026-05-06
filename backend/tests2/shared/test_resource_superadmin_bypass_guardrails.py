"""Source-bound guardrail: literal ``is_superadmin=True`` is forbidden
in production code under ``backend/app/`` except in an explicit allowlist.

BE-GO-READY-RESOURCE-ENGINE-015-SUPERADMIN-BYPASS-GUARDRAILS.

Why
---
``is_superadmin=True`` is the canonical bypass for cross-tenant
checks. It appears as:

  * ``ResourceRef(..., is_superadmin=True)`` — skips the SQL group
    filter inside ``ResultBlobLoader`` (the bypass is recorded in
    a structured audit log for symmetry).
  * ``SourceLoader().load(..., is_superadmin=True)`` — skips
    ``is_resource_visible`` inside the file branch.
  * ``ArtifactResolver.materialize_latest(..., is_superadmin=True)``
    — skips the project's ``group_id`` cross-tenant check.
  * ``app.shared.sharing.visible_resource_ids/is_resource_visible``
    — short-circuit the share/owner SQL.
  * ``app.core.auth.disclosure.admin_target_not_visible`` — chooses
    404 vs 403 depending on the caller's role (anti-enum invariant).

The privilege MUST follow the authenticated principal: routers and
services compute it from ``current_user.role == "superadmin"`` (or
the equivalent for system-level callers). A literal ``True`` in
production code reads as a *convenience flag* and is almost always
a regression — either a copy-paste from a test fixture or a quick
fix that disabled the tenant check while debugging.

What this test does
-------------------
Source-bound: every ``.py`` under ``backend/app/`` is scanned with
string literals and comments stripped via ``tokenize``. We look
for the code-level token sequence ``is_superadmin = True`` (or
the no-space ``is_superadmin=True`` form). Hits are forbidden
unless the file is in the ``_PRIVILEGED_BYPASS_ALLOWLIST`` below.

The allowlist is empty by design today: no production callsite
currently hard-codes the bypass (verified at the time this test
landed). Future allowlisting requires:

  * documented system-level/admin operation, NOT a normal user
    request flow;
  * explicit caller context that cannot be confused with a normal
    user request (e.g. a worker entrypoint, an admin-only CLI);
  * an audit log entry on the bypass site (the loader already
    emits one for ``ResultBlobLoader``).

This guard does NOT touch:

  * tests under ``backend/tests*`` — fixtures and contract tests
    legitimately construct privileged refs to verify the bypass
    semantics;
  * docstrings, comments, and log f-strings inside ``backend/app/``
    — those describe the contract; tokenize removes them before
    the scan.

Companion contracts:

  * ``test_resource_engine_anti_god_engine_lock.py`` — locks the
    engine boundary (no domain libs, byte-level only).
  * ``test_result_blob_loader_contract.py`` — verifies the audit
    log fires on ``is_superadmin=True``.
  * ``test_artifact_resolver.py::*superadmin*`` — verifies the
    cross-group bypass on ``materialize_latest``.
"""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = _REPO_ROOT / "backend"
_APP_ROOT = _BACKEND_ROOT / "app"


# ---------------------------------------------------------------------------
# Allowlist of production files allowed to contain a literal
# ``is_superadmin=True`` token. Empty by design.
#
# To add an entry, document:
#   - WHY the caller is system-level (not a user request);
#   - WHERE the audit log lives;
#   - WHEN the bypass fires (caller context).
# ---------------------------------------------------------------------------

_PRIVILEGED_BYPASS_ALLOWLIST: frozenset[str] = frozenset(
    {
        # (currently empty — no production callsite needs the literal)
    }
)


# ---------------------------------------------------------------------------
# Routers / services that MUST NOT construct privileged refs with the
# literal True bypass. Listed for documentation: the source-bound scan
# below already covers ``app/`` exhaustively, but pinning the user-facing
# entrypoints makes the failure message diagnostic.
# ---------------------------------------------------------------------------

_NORMAL_REQUEST_ENTRYPOINT_GLOBS: tuple[str, ...] = (
    "core/*/router.py",
    "core/*/routes.py",
    "core/*/api*.py",
    "services/*.py",
)


# ---------------------------------------------------------------------------
# Tokenize helpers
# ---------------------------------------------------------------------------


def _python_code_only(source: str) -> str:
    """Return source with string literals, f-string literal parts,
    and comments stripped.

    Python 3.12+ tokenizes f-strings into ``FSTRING_START`` /
    ``FSTRING_MIDDLE`` / ``FSTRING_END`` plus the embedded
    expression tokens. We strip the literal portions (they read
    as runtime data, not code) but keep the expressions (those
    really are code and must remain visible to the scan).

    Mirrors the spirit of the helper in
    ``test_resource_engine_anti_god_engine_lock.py``; the two
    source-bound locks share the same definition of "code".
    """
    out: list[str] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenizeError:
        # Fall back to raw text on malformed sources; the scan would
        # rather over-report (a false positive in a malformed file is
        # better than missing a real regression).
        return source
    _strip_types = {
        tokenize.STRING,
        tokenize.COMMENT,
        getattr(tokenize, "FSTRING_START", -1),
        getattr(tokenize, "FSTRING_MIDDLE", -1),
        getattr(tokenize, "FSTRING_END", -1),
    }
    for tok in tokens:
        if tok.type in _strip_types:
            continue
        out.append(tok.string)
    return " ".join(out)


def _iter_app_py():
    for path in _APP_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _has_literal_superadmin_true(code_only: str) -> bool:
    """True iff the stripped-source contains a code-level
    ``is_superadmin = True`` token sequence.

    Matches both ``is_superadmin=True`` and ``is_superadmin = True``
    (and any whitespace variant) because tokenize splits the
    operator and identifier regardless of source spacing.
    """
    # tokenize joins tokens with single spaces; the canonical form
    # in code_only is "is_superadmin = True" exactly.
    if "is_superadmin = True" in code_only:
        return True
    # Defensive: catch the rare case where the source uses no spacing
    # AND the tokenizer collapsed it differently (shouldn't happen
    # on stdlib tokenize, but cheap to check).
    compact = code_only.replace(" ", "")
    return "is_superadmin=True" in compact


# ---------------------------------------------------------------------------
# Source-bound guard tests
# ---------------------------------------------------------------------------


def test_no_literal_is_superadmin_true_in_app_outside_allowlist():
    """Every .py under ``backend/app/`` is scanned (with strings and
    comments stripped). Any code-level ``is_superadmin = True`` is
    forbidden unless the file is in ``_PRIVILEGED_BYPASS_ALLOWLIST``.

    Adding a new privileged caller requires explicit allowlisting
    AND the supporting audit log; do NOT widen the allowlist as a
    workaround for a normal-request flow.
    """
    offenders: list[str] = []
    for path in _iter_app_py():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        code_only = _python_code_only(text)
        if not _has_literal_superadmin_true(code_only):
            continue
        rel = str(path.relative_to(_BACKEND_ROOT)).replace("\\", "/")
        if rel in _PRIVILEGED_BYPASS_ALLOWLIST:
            continue
        offenders.append(rel)

    if offenders:
        details = "\n".join(f"  - {p}" for p in sorted(offenders))
        raise AssertionError(
            "Forbidden literal `is_superadmin=True` in production code:\n"
            f"{details}\n\n"
            "Routers and services MUST compute the bypass from the "
            "authenticated principal (e.g. "
            "`is_superadmin=current_user.role == 'superadmin'`). A "
            "literal `True` reads as a convenience flag and bypasses "
            "the cross-tenant fail-closed.\n\n"
            "If this is a documented system-level caller (worker, "
            "admin CLI, reconciliation job), add the file path to "
            "`_PRIVILEGED_BYPASS_ALLOWLIST` in this test with a "
            "comment explaining the audit log site and the caller "
            "context. See "
            "`.agent-temp/BE_RESOURCE_ENGINE_AUDIT.md` "
            "(BE-GO-READY-RESOURCE-ENGINE-015)."
        )


def test_no_router_constructs_resource_ref_with_literal_superadmin_true():
    """Tighter, diagnostic-only check on user-facing entrypoints.

    Even if the global guard above is satisfied, this test pins the
    invariant on routers/services explicitly: the failure message
    points at the canonical "convenience flag" pattern (a router
    constructing a ``ResourceRef(...)`` with ``is_superadmin=True``
    instead of computing from ``current_user.role``).
    """
    offenders: list[tuple[str, str]] = []
    for glob in _NORMAL_REQUEST_ENTRYPOINT_GLOBS:
        for path in _APP_ROOT.glob(glob):
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            code_only = _python_code_only(text)
            if _has_literal_superadmin_true(code_only):
                rel = str(path.relative_to(_BACKEND_ROOT)).replace("\\", "/")
                offenders.append((rel, "is_superadmin=True"))
            # The companion smell: ResourceRef(...) constructed AT
            # ALL inside a router/service. The engine's public entry
            # point is the registry; routers should never assemble
            # the ref themselves bypassing the engine boundary.
            if "ResourceRef (" in code_only or "ResourceRef(" in code_only.replace(" ", ""):
                rel = str(path.relative_to(_BACKEND_ROOT)).replace("\\", "/")
                offenders.append((rel, "ResourceRef(...)"))

    if offenders:
        details = "\n".join(f"  - {p}: {n}" for (p, n) in sorted(set(offenders)))
        raise AssertionError(
            "Router/service constructed a privileged ResourceRef or "
            "literal superadmin bypass:\n"
            f"{details}\n\n"
            "Normal request entrypoints (routers, services) must:\n"
            "  - compute `is_superadmin` from `current_user.role`;\n"
            "  - go through the engine's registry rather than "
            "instantiate `ResourceRef` directly.\n"
            "Constructing the ref in the router re-creates the "
            "vertical-feature shape the Resource Engine pilot is "
            "horizontalizing away from."
        )


def test_allowlist_paths_resolve_when_non_empty():
    """Defense-in-depth: every entry in
    ``_PRIVILEGED_BYPASS_ALLOWLIST`` MUST point at a real file
    under ``backend/``. A stale entry that no longer matches a
    file silently widens the guard (the file may have been moved
    or deleted, dragging the bypass with it).
    """
    for rel in _PRIVILEGED_BYPASS_ALLOWLIST:
        target = _BACKEND_ROOT / rel
        assert target.is_file(), (
            f"_PRIVILEGED_BYPASS_ALLOWLIST entry does not resolve: "
            f"{rel!r}. Either the path is wrong or the file moved; "
            "either way, the allowlist is now stale."
        )
