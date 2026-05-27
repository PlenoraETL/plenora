"""
Architecture guard - app.shared root-level module allowlist.

BE-GO-READY-ARCH-059-W5: Prevents ``app.shared`` from becoming a junk drawer
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared]

_SHARED_DIR = Path(__file__).parents[2] / "app" / "shared"

# Allowlist - organized by cross-cutting bucket.
# Every root-level *.py file in app/shared/ must appear here.
# Engine-specific code belongs in the engine subpackages above.
_ALLOWED_ROOT_MODULES: frozenset[str] = frozenset(
    {
        "request_utils",      # request context extraction helpers
        "error_codes",        # shared error code registry
        "error_envelope",     # standard error response envelope
        "error_sanitizer",    # strip sensitive data from error messages
        "login_rate_limiter", # compat alias to app.core.security.login_rate_limiter
        "password_hashing",   # compat alias to app.core.security.password_hashing
        "token_blacklist",    # compat alias to app.core.security.token_blacklist
        "upload_guards",      # compat alias to app.core.security.upload_guards
        "clock",                       # wall-clock abstraction for testability
        "canvas_types",                # canvas type helpers shared across engines
    }
)

_ROOT_MODULE_CLASSIFICATIONS: dict[str, str] = {
    "request_utils": "KEEP_ROOT_CROSS_CUTTING",
    "error_codes": "KEEP_ROOT_CROSS_CUTTING",
    "error_envelope": "KEEP_ROOT_CROSS_CUTTING",
    "error_sanitizer": "KEEP_ROOT_CROSS_CUTTING",
    "login_rate_limiter": "KEEP_COMPAT_SECURITY_WRAPPER",
    "password_hashing": "KEEP_COMPAT_SECURITY_WRAPPER",
    "token_blacklist": "KEEP_COMPAT_SECURITY_WRAPPER",
    "upload_guards": "KEEP_COMPAT_SECURITY_WRAPPER",
    "clock": "KEEP_ROOT_CROSS_CUTTING",
    "canvas_types": "KEEP_ROOT_CROSS_CUTTING",
}

_CLASSIFICATION_BUCKETS: frozenset[str] = frozenset(
    {
        "KEEP_ROOT_CROSS_CUTTING",
        "MOVE_TO_RESOURCES_CANDIDATE",
        "MOVE_TO_EXECUTION_CANDIDATE",
        "MOVE_TO_CONNECTIONS_CANDIDATE",
        "MOVE_TO_SECURITY_PACKAGE_LATER",
        "KEEP_COMPAT_SECURITY_WRAPPER",
        "HEAVY_ROOT_UTILITY",
    }
)

# Subpackages - not checked here; they have their own boundary tests.
_KNOWN_SUBPACKAGES: frozenset[str] = frozenset(
    {"execution", "connections", "resources", "contracts"}
)

_ALLOWED_UPWARD_IMPORTS: dict[str, frozenset[str]] = {
    "error_codes": frozenset(
        {
            "app.core.admin_backups.errors",
            "app.core.connections.errors",
            "app.core.datasources.errors",
            "app.core.tickets.errors",
        }
    ),
    "login_rate_limiter": frozenset(
        {"app.core.security", "app.core.security.login_rate_limiter"}
    ),
    "password_hashing": frozenset(
        {"app.core.security", "app.core.security.password_hashing"}
    ),
    "token_blacklist": frozenset(
        {"app.core.security", "app.core.security.token_blacklist"}
    ),
    "upload_guards": frozenset(
        {"app.core.security", "app.core.security.upload_guards"}
    ),
}


def _actual_root_modules() -> set[str]:
    """Return stem names of all *.py files at the root of app/shared/."""
    return {
        p.stem
        for p in _SHARED_DIR.glob("*.py")
        if p.stem != "__init__"
    }


def _import_edges(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges.update(alias.name for alias in node.names)
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            edges.add(node.module)
    return edges


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


def test_shared_root_modules_do_not_import_domain_or_infrastructure_code() -> None:
    """Root shared modules must stay leaf-like unless explicitly transitional."""
    violations: list[str] = []
    for path in sorted(_SHARED_DIR.glob("*.py")):
        if path.stem == "__init__":
            continue
        allowed = _ALLOWED_UPWARD_IMPORTS.get(path.stem, frozenset())
        for edge in sorted(_import_edges(path)):
            if not (
                edge.startswith("app.core.")
                or edge.startswith("app.infrastructure.")
                or edge.startswith("app.db.")
            ):
                continue
            if edge in allowed:
                continue
            violations.append(f"{path.name} -> {edge}")

    assert violations == [], (
        "Root-level app.shared modules must not depend upward on domain, "
        "infrastructure, or DB modules. Move that code to the owning domain "
        f"or add a narrow transitional exception. Violations: {violations}"
    )
