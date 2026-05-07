"""
Architecture guard - app.shared root-level module allowlist.

BE-GO-READY-ARCH-059-W5: Prevents ``app.shared`` from becoming a junk drawer
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared]

_SHARED_DIR = Path(__file__).parents[2] / "app" / "shared"

# Allowlist - organized by cross-cutting bucket.
# Every root-level *.py file in app/shared/ must appear here.
# Engine-specific code belongs in the engine subpackages above.
_ALLOWED_ROOT_MODULES: frozenset[str] = frozenset(
    {
        "api_utils",          # generic FastAPI helpers (response shaping, pagination)
        "async_http",         # thin httpx async wrapper used across domains
        "request_utils",      # request context extraction helpers
        "async_user_recheck", # async per-request permission re-validation helper
        "error_codes",        # shared error code registry
        "error_envelope",     # standard error response envelope
        "error_sanitizer",    # strip sensitive data from error messages
        "exceptions",         # shared exception hierarchy
        "audit_action",       # audit log action registry
        "authz_helpers",      # authorization predicate helpers
        "crypto",             # cryptographic primitives (fernet, hashing)
        "jwt_token",          # JWT encode/decode helpers
        "login_rate_limiter", # per-user login throttle
        "password_hashing",   # bcrypt password helpers
        "sharing",            # resource sharing primitives
        "token_blacklist",    # JWT revocation store
        "upload_guards",      # upload size / MIME type enforcement
        "cloud_backends",          # object storage backend registry
        "file_helpers",            # pandas-backed file inspection helpers
        "memory_guard",            # per-tenant memory allocation guard
        "storage_compensation",    # object-storage failure compensation helpers
        "storage_dispatch",        # storage backend routing
        "storage_reconciliation",  # stale-blob reconciliation helpers
        "task_scoped_blob_store",  # per-task ephemeral blob store
        "column_types",           # column type enum / classification
        "columns_metadata",       # column-level metadata helpers
        "models",                 # shared Pydantic base models
        "clock",                       # wall-clock abstraction for testability
        "external_artifact_processor", # cross-domain artifact post-processing helpers
        "idempotency",                 # idempotency key helpers
    }
)

# Subpackages - not checked here; they have their own boundary tests.
_KNOWN_SUBPACKAGES: frozenset[str] = frozenset(
    {"execution", "connections", "resources", "contracts"}
)


def _actual_root_modules() -> set[str]:
    """Return stem names of all *.py files at the root of app/shared/."""
    return {
        p.stem
        for p in _SHARED_DIR.glob("*.py")
        if p.stem != "__init__"
    }


def test_no_new_root_level_modules_without_allowlist_entry() -> None:
    """Fail when a new root-level module is added to app.shared without approval.

    New engine-oriented code belongs in an engine subpackage.
    If a root-level module is genuinely cross-cutting, add it to
    ``_ALLOWED_ROOT_MODULES`` in this file with a comment explaining why.
    """
    actual = _actual_root_modules()
    unknown = actual - _ALLOWED_ROOT_MODULES
    assert not unknown, (
        f"New root-level module(s) detected in app/shared/: {sorted(unknown)}\n\n"
        "Engine-oriented code should go in a subpackage:\n"
        "  app.shared.execution.*   - canvas nodes, executor protocol, context\n"
        "  app.shared.resources.*   - metadata, loaders, storage/path helpers\n"
        "  app.shared.connections.* - provider protocols, connection info\n"
        "  app.shared.contracts.*   - cross-cutting auth contracts\n\n"
        "If this module is genuinely cross-cutting, add it to _ALLOWED_ROOT_MODULES "
        "in tests2/shared/test_shared_root_level_allowlist.py with a comment explaining why."
    )


def test_allowlist_has_no_phantom_entries() -> None:
    """Warn (via xfail) if the allowlist references modules that no longer exist.

    This keeps the allowlist tidy - stale entries should be removed.
    """
    actual = _actual_root_modules()
    phantom = _ALLOWED_ROOT_MODULES - actual
    assert not phantom, (
        f"Allowlist references modules that no longer exist: {sorted(phantom)}\n"
        "Remove stale entries from _ALLOWED_ROOT_MODULES in "
        "tests2/shared/test_shared_root_level_allowlist.py"
    )


def test_known_subpackages_present() -> None:
    """Engine subpackages must remain present as directories."""
    missing = []
    for pkg in sorted(_KNOWN_SUBPACKAGES):
        if not (_SHARED_DIR / pkg).is_dir():
            missing.append(pkg)
    assert not missing, (
        f"Engine subpackage(s) missing from app/shared/: {missing}\n"
        "These must not be deleted or flattened back into root level."
    )
