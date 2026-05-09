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
        "audit_action",       # audit log action registry
        "authz_helpers",      # authorization predicate helpers
        "crypto",             # cryptographic primitives (fernet, hashing)
        "jwt_token",          # JWT encode/decode helpers
        "login_rate_limiter", # per-user login throttle
        "password_hashing",   # bcrypt password helpers
        "token_blacklist",    # JWT revocation store
        "upload_guards",      # upload size / MIME type enforcement
        "cloud_backends",          # compatibility alias for infrastructure storage
        "file_helpers",            # pandas-backed file inspection helpers
        "memory_guard",            # per-tenant memory allocation guard
        "storage_compensation",    # object-storage failure compensation helpers
        "storage_reconciliation",  # compatibility alias for resources reconciliation
        "columns_metadata",       # column-level metadata helpers
        "models",                 # shared Pydantic base models
        "clock",                       # wall-clock abstraction for testability
        "idempotency",                 # idempotency key helpers
    }
)

_ROOT_MODULE_CLASSIFICATIONS: dict[str, str] = {
    "api_utils": "MOVE_TO_CONNECTIONS_CANDIDATE",
    "async_http": "MOVE_TO_CONNECTIONS_CANDIDATE",
    "request_utils": "KEEP_ROOT_CROSS_CUTTING",
    "async_user_recheck": "MOVE_TO_SECURITY_PACKAGE_LATER",
    "error_codes": "KEEP_ROOT_CROSS_CUTTING",
    "error_envelope": "KEEP_ROOT_CROSS_CUTTING",
    "error_sanitizer": "KEEP_ROOT_CROSS_CUTTING",
    "audit_action": "MOVE_TO_SECURITY_PACKAGE_LATER",
    "authz_helpers": "MOVE_TO_SECURITY_PACKAGE_LATER",
    "crypto": "MOVE_TO_SECURITY_PACKAGE_LATER",
    "jwt_token": "MOVE_TO_SECURITY_PACKAGE_LATER",
    "login_rate_limiter": "MOVE_TO_SECURITY_PACKAGE_LATER",
    "password_hashing": "MOVE_TO_SECURITY_PACKAGE_LATER",
    "token_blacklist": "MOVE_TO_SECURITY_PACKAGE_LATER",
    "upload_guards": "MOVE_TO_SECURITY_PACKAGE_LATER",
    "cloud_backends": "KEEP_ROOT_CROSS_CUTTING",
    "file_helpers": "HEAVY_ROOT_UTILITY",
    "memory_guard": "MOVE_TO_EXECUTION_CANDIDATE",
    "storage_compensation": "MOVE_TO_RESOURCES_CANDIDATE",
    "storage_reconciliation": "MOVE_TO_RESOURCES_CANDIDATE",
    "columns_metadata": "MOVE_TO_RESOURCES_CANDIDATE",
    "models": "HEAVY_ROOT_UTILITY",
    "clock": "KEEP_ROOT_CROSS_CUTTING",
    "idempotency": "MOVE_TO_EXECUTION_CANDIDATE",
}

_CLASSIFICATION_BUCKETS: frozenset[str] = frozenset(
    {
        "KEEP_ROOT_CROSS_CUTTING",
        "MOVE_TO_RESOURCES_CANDIDATE",
        "MOVE_TO_EXECUTION_CANDIDATE",
        "MOVE_TO_CONNECTIONS_CANDIDATE",
        "MOVE_TO_SECURITY_PACKAGE_LATER",
        "HEAVY_ROOT_UTILITY",
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


def test_allowed_root_modules_have_classification() -> None:
    """Keep root module migration intent explicit and machine-readable."""
    assert set(_ROOT_MODULE_CLASSIFICATIONS) == set(_ALLOWED_ROOT_MODULES)
    unknown_buckets = set(_ROOT_MODULE_CLASSIFICATIONS.values()) - _CLASSIFICATION_BUCKETS
    assert not unknown_buckets, (
        f"Unknown shared root classification bucket(s): {sorted(unknown_buckets)}"
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
