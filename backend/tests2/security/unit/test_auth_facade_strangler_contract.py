"""Source-bound guard — facade strangler Phase-1 + Phase-2.

Locks the auth-facade strangler against regression. Two contracts:

PHASE-1 (BE-GO-READY-AUTH-ENGINE-005): full facade ban.
  3 files where NO `from app.core.auth.auth import ...` ImportFrom
  is allowed at all (every consumed name has a leaf):
    1. `app/core/auth/auth_middleware.py` — `decode_token` from
       `app.shared.jwt_token`.
    2. `app/core/auth/ownership.py` — `ROLES_ADMIN_OR_ABOVE` from
       `app.core.auth.roles` (was lazy facade import inside
       `_check_ownership`).
    3. `app/shared/authz_helpers.py` — `CurrentUser` from
       `app.core.auth.models`; `assert_group_access` +
       `assert_resource_visible` from `app.core.auth.ownership`.

PHASE-2 (BE-GO-READY-AUTH-ENGINE-006): per-name facade ban.
  Files that still legitimately import FastAPI Depends
  (`get_current_user`, `require_*`) from the facade, but MUST NOT
  re-import `CurrentUser` or ownership helpers from the facade:
    - `app/shared/async_user_recheck.py` — full ban (CurrentUser only).
    - `app/core/tickets/service.py`        — full ban (CurrentUser only).
    - `app/core/admin_backups/router.py`   — facade may keep
      `require_superadmin`; CurrentUser banned.
    - `app/core/admin_system_status/router.py` — same.
    - `app/core/tickets/router.py` — facade may keep
      `get_current_user`; CurrentUser banned.
    - `app/core/orchestrator/router.py` — facade may keep
      `get_current_user`, `require_not_esecutore`; CurrentUser +
      `assert_group_access` banned.
    - `app/core/imdf/router.py` — facade may keep
      `get_current_user`; CurrentUser + `assert_resource_visible`
      banned (the latter was a body-local lazy facade import).

Why source-bound:
  Importing the modules at test time would still drag the facade via
  transitive imports (e.g. `auth.models` is OK, but loading `auth.py`
  itself runs `import app.core.auth.disclosure` etc.). The contract we
  want to lock is purely structural: target names cannot appear in any
  `ImportFrom(module="app.core.auth.auth")` node of the migrated file.

Stays source-bound to mirror `tests2/shared/test_architecture_legacy_patterns.py`
posture: zero app imports, zero side effects.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_APP = _BACKEND_ROOT / "app"


_FACADE_MODULE = "app.core.auth.auth"


# Phase-1: full facade ban — no ImportFrom(app.core.auth.auth) at all.
_PHASE1_FILES = (
    _APP / "core" / "auth" / "auth_middleware.py",
    _APP / "core" / "auth" / "ownership.py",
    _APP / "shared" / "authz_helpers.py",
    # Phase-2 also forbids facade entirely for these (CurrentUser-only
    # imports — nothing else was being pulled from the facade).
    _APP / "shared" / "async_user_recheck.py",
    _APP / "core" / "tickets" / "service.py",
)


# Phase-2: per-name facade ban. Facade ImportFrom is allowed but it
# may NOT carry these names (every name in the set has a leaf source).
_PHASE2_PER_NAME_BAN: dict[Path, frozenset[str]] = {
    _APP / "core" / "admin_backups" / "router.py":
        frozenset({"CurrentUser"}),
    _APP / "core" / "admin_system_status" / "router.py":
        frozenset({"CurrentUser"}),
    _APP / "core" / "tickets" / "router.py":
        frozenset({"CurrentUser"}),
    _APP / "core" / "orchestrator" / "router.py":
        frozenset({"CurrentUser", "assert_group_access"}),
    # imdf/router.py: CurrentUser top-level migrated to leaf
    # (Phase-9 contract fan-out). `assert_resource_visible`
    # migrated to leaf by AUTH-ENGINE-020 (closes R-019.1):
    # the IMDF router now imports it from
    # `app.core.auth.ownership` at module top, the 2 IMDF
    # tests monkeypatch via the consuming router-module
    # surface, and the facade re-export was removed.
    # AUTH-ENGINE-025 adds `assert_resource_visible` to this
    # per-name ban dict as a defense-in-depth lock alongside
    # the global Phase-13 lock + the dedicated
    # `test_phase2_imdf_lazy_assert_resource_visible_migrated_to_leaf`
    # test: the symbol cannot be re-imported via the facade in
    # `imdf/router.py` even if a future diff also widens the
    # global allowlist.
    _APP / "core" / "imdf" / "router.py":
        frozenset({"CurrentUser", "assert_resource_visible"}),
}


# Phase-3: per-name facade ban for the next coherent router group.
# Same pattern as Phase-2: facade may keep FastAPI Depends and
# `group_filter` (which still lives in auth.py:295), but `CurrentUser`
# and ownership leaves must come from their leaf modules.
_PHASE3_PER_NAME_BAN: dict[Path, frozenset[str]] = {
    _APP / "core" / "logs" / "router.py":
        frozenset({"CurrentUser"}),
    _APP / "core" / "logs" / "runs_router.py":
        frozenset({"CurrentUser"}),
    _APP / "core" / "estrai" / "router.py":
        frozenset({"CurrentUser"}),
    _APP / "core" / "manipola" / "api" / "transforms_api.py":
        frozenset({"CurrentUser"}),
    # features/routes.py also bans 4 ownership helpers — they live
    # in `app.core.auth.ownership` as their canonical home.
    _APP / "core" / "features" / "routes.py":
        frozenset({
            "CurrentUser",
            "assert_group_access",
            "assert_resource_visible",
            "assert_can_modify",
            "compute_can_edit",
        }),
}


# Phase-4: same pattern as Phase-3, applied to the next coherent router
# group (arricchisci/carica/connections/datasources/eseguibili).
# Facade may keep FastAPI Depends + `group_filter`. CurrentUser and
# ownership helpers MUST come from leaf modules.
_OWNERSHIP_HELPER_BAN_NAMES = frozenset({
    "CurrentUser",
    "assert_group_access",
    "assert_resource_visible",
    "assert_can_modify",
    "compute_can_edit",
})

_PHASE4_PER_NAME_BAN: dict[Path, frozenset[str]] = {
    _APP / "core" / "arricchisci" / "router.py":
        _OWNERSHIP_HELPER_BAN_NAMES,
    # carica/router.py only consumed `assert_resource_visible` from
    # ownership; CurrentUser is still banned regardless.
    _APP / "core" / "carica" / "router.py":
        frozenset({"CurrentUser", "assert_resource_visible"}),
    _APP / "core" / "connections" / "router.py":
        _OWNERSHIP_HELPER_BAN_NAMES,
    _APP / "core" / "datasources" / "router.py":
        _OWNERSHIP_HELPER_BAN_NAMES,
    _APP / "core" / "eseguibili" / "router.py":
        _OWNERSHIP_HELPER_BAN_NAMES,
}


# Phase-5 (BE-GO-READY-AUTH-ENGINE-010): contract-boundary prep on
# the next coherent router group (files / intermediate / projects /
# scheduler). Same split as Phase-3/4: facade may keep FastAPI
# Depends (`get_current_user`, `require_*`, `require_*_owner`,
# `require_admin`, `require_user_or_above`, `require_superadmin`,
# `require_not_esecutore`) and `group_filter`. CurrentUser + the 4
# ownership helpers MUST come from their leaf modules
# (`app.core.auth.models` / `app.core.auth.ownership`).
#
# Audit context — see `.agent-temp/BE_AUTH_ENGINE_AUDIT.md` Incoming
# Phase-5 section for the proposed `app.shared.contracts.auth`
# split (pure contracts vs. implementation deps). This phase
# preserves the facade as the dependency-magnet target only for
# names that cannot move yet (FastAPI Depends + `group_filter`),
# leaving every leaf-safe symbol on a direct leaf import.
_PHASE5_PER_NAME_BAN: dict[Path, frozenset[str]] = {
    _APP / "core" / "files" / "router.py":
        _OWNERSHIP_HELPER_BAN_NAMES,
    _APP / "core" / "intermediate" / "router.py":
        frozenset({"CurrentUser", "assert_group_access"}),
    _APP / "core" / "projects" / "router.py":
        frozenset({"CurrentUser", "assert_group_access"}),
    _APP / "core" / "scheduler" / "router.py":
        frozenset({"CurrentUser", "assert_group_access"}),
}


# Phase-6 (BE-GO-READY-AUTH-ENGINE-011): `group_filter` leaf
# extraction. The function moved to its canonical home
# `app.core.auth.ownership` (pure policy helper, zero side
# effects). The auth.py facade keeps a `KEEP_COMPAT` re-export
# (no auth.py shrink) but app routers MUST consume `group_filter`
# from the leaf — facade pull becomes a strangler regression.
#
# The 9 router files below are the complete set of app callsites
# using `group_filter` at this batch's start. Facade may keep
# FastAPI Depends + `require_*` (Bucket A from the Phase-5
# audit's contract-boundary analysis); banned name: just
# `group_filter`.
_PHASE6_GROUP_FILTER_BAN: frozenset[str] = frozenset({"group_filter"})

_PHASE6_PER_NAME_BAN: dict[Path, frozenset[str]] = {
    _APP / "core" / "connections" / "router.py":
        _PHASE6_GROUP_FILTER_BAN,
    _APP / "core" / "datasources" / "router.py":
        _PHASE6_GROUP_FILTER_BAN,
    _APP / "core" / "eseguibili" / "router.py":
        _PHASE6_GROUP_FILTER_BAN,
    _APP / "core" / "features" / "routes.py":
        _PHASE6_GROUP_FILTER_BAN,
    _APP / "core" / "files" / "router.py":
        _PHASE6_GROUP_FILTER_BAN,
    _APP / "core" / "intermediate" / "router.py":
        _PHASE6_GROUP_FILTER_BAN,
    _APP / "core" / "logs" / "router.py":
        _PHASE6_GROUP_FILTER_BAN,
    _APP / "core" / "projects" / "router.py":
        _PHASE6_GROUP_FILTER_BAN,
    _APP / "core" / "scheduler" / "router.py":
        _PHASE6_GROUP_FILTER_BAN,
}


def _imports_facade(path: Path) -> list[tuple[int, str]]:
    """Return [(lineno, names_imported)] for each `from app.core.auth.auth import ...`
    found anywhere in the file (top-level OR nested inside funcs).
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == _FACADE_MODULE:
                names = ", ".join(a.name for a in node.names)
                hits.append((node.lineno, names))
    return hits


@pytest.mark.parametrize(
    "path",
    _PHASE1_FILES,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase1_migrated_file_does_not_import_from_facade(path: Path):
    """Phase-1 contract: each migrated file imports directly from
    leaf modules (`app.shared.jwt_token`, `app.core.auth.roles`,
    `app.core.auth.models`, `app.core.auth.ownership`) — NOT from
    the facade `app.core.auth.auth` at all."""
    assert path.exists(), f"migrated file missing: {path}"
    hits = _imports_facade(path)
    assert hits == [], (
        f"{path.name} regressed to facade-import:\n"
        + "\n".join(f"  line {ln}: from app.core.auth.auth import {nm}" for ln, nm in hits)
        + "\nUse the leaf module instead "
        "(jwt_token / roles / models / ownership / disclosure)."
    )


def _per_name_ban_violations(
    path: Path, banned_names: frozenset[str]
) -> list[tuple[int, str]]:
    """Return [(lineno, banned_name)] for every facade ImportFrom in
    `path` that pulls a name in `banned_names`."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _FACADE_MODULE:
            for alias in node.names:
                if alias.name in banned_names:
                    violations.append((node.lineno, alias.name))
    return violations


@pytest.mark.parametrize(
    "path,banned_names",
    list(_PHASE2_PER_NAME_BAN.items()),
    ids=lambda v: (
        str(v.relative_to(_BACKEND_ROOT)).replace("\\", "/")
        if isinstance(v, Path)
        else "names"
    ),
)
def test_phase2_migrated_file_does_not_import_banned_names_from_facade(
    path: Path, banned_names: frozenset[str]
):
    """Phase-2 contract: the file is still allowed to import FastAPI
    Depends (`get_current_user`, `require_*`, `require_superadmin`)
    from the facade, but it MUST NOT pull `CurrentUser` or ownership
    helpers from the facade — those have leaf sources."""
    assert path.exists(), f"migrated file missing: {path}"
    violations = _per_name_ban_violations(path, banned_names)
    assert violations == [], (
        f"{path.relative_to(_BACKEND_ROOT)} pulls Phase-2 banned "
        f"names from the facade:\n"
        + "\n".join(f"  line {ln}: {nm}" for ln, nm in violations)
        + f"\nLeaf sources: CurrentUser ← app.core.auth.models; "
        "assert_group_access / assert_resource_visible ← "
        "app.core.auth.ownership."
    )


@pytest.mark.parametrize(
    "path,banned_names",
    list(_PHASE3_PER_NAME_BAN.items()),
    ids=lambda v: (
        str(v.relative_to(_BACKEND_ROOT)).replace("\\", "/")
        if isinstance(v, Path)
        else "names"
    ),
)
def test_phase3_migrated_file_does_not_import_banned_names_from_facade(
    path: Path, banned_names: frozenset[str]
):
    """Phase-3 contract: extends Phase-2 to a second router group.
    The file may keep `get_current_user`, `require_*`, and
    `group_filter` on the facade. Banned names: `CurrentUser` and (for
    `features/routes.py`) the 4 ownership helpers."""
    assert path.exists(), f"migrated file missing: {path}"
    violations = _per_name_ban_violations(path, banned_names)
    assert violations == [], (
        f"{path.relative_to(_BACKEND_ROOT)} pulls Phase-3 banned "
        f"names from the facade:\n"
        + "\n".join(f"  line {ln}: {nm}" for ln, nm in violations)
        + f"\nLeaf sources: CurrentUser ← app.core.auth.models; "
        "assert_group_access / assert_resource_visible / "
        "assert_can_modify / compute_can_edit ← app.core.auth.ownership."
    )


@pytest.mark.parametrize(
    "path,banned_names",
    list(_PHASE4_PER_NAME_BAN.items()),
    ids=lambda v: (
        str(v.relative_to(_BACKEND_ROOT)).replace("\\", "/")
        if isinstance(v, Path)
        else "names"
    ),
)
def test_phase4_migrated_file_does_not_import_banned_names_from_facade(
    path: Path, banned_names: frozenset[str]
):
    """Phase-4 contract: applies the Phase-2/3 split pattern to the
    arricchisci/carica/connections/datasources/eseguibili router group.
    Facade may keep `get_current_user`, `require_*`,
    `require_not_esecutore`, `require_admin`, `require_user_or_above`,
    `group_filter`. Banned: `CurrentUser` + the 4 ownership helpers
    (each canonical home is `app.core.auth.models` or
    `app.core.auth.ownership`)."""
    assert path.exists(), f"migrated file missing: {path}"
    violations = _per_name_ban_violations(path, banned_names)
    assert violations == [], (
        f"{path.relative_to(_BACKEND_ROOT)} pulls Phase-4 banned "
        f"names from the facade:\n"
        + "\n".join(f"  line {ln}: {nm}" for ln, nm in violations)
        + f"\nLeaf sources: CurrentUser ← app.core.auth.models; "
        "ownership helpers ← app.core.auth.ownership."
    )


@pytest.mark.parametrize(
    "path,banned_names",
    list(_PHASE5_PER_NAME_BAN.items()),
    ids=lambda v: (
        str(v.relative_to(_BACKEND_ROOT)).replace("\\", "/")
        if isinstance(v, Path)
        else "names"
    ),
)
def test_phase5_migrated_file_does_not_import_banned_names_from_facade(
    path: Path, banned_names: frozenset[str]
):
    """Phase-5 contract: contract-boundary prep on
    files/intermediate/projects/scheduler. Same split as Phase-2/3/4.
    Facade may keep FastAPI Depends (`get_current_user`,
    `require_*`, `require_*_owner`, `require_admin`,
    `require_user_or_above`, `require_superadmin`,
    `require_not_esecutore`). Banned: `CurrentUser` + ownership
    helpers (canonical leaves: `app.core.auth.models` +
    `app.core.auth.ownership`)."""
    assert path.exists(), f"migrated file missing: {path}"
    violations = _per_name_ban_violations(path, banned_names)
    assert violations == [], (
        f"{path.relative_to(_BACKEND_ROOT)} pulls Phase-5 banned "
        f"names from the facade:\n"
        + "\n".join(f"  line {ln}: {nm}" for ln, nm in violations)
        + f"\nLeaf sources: CurrentUser ← app.core.auth.models; "
        "ownership helpers ← app.core.auth.ownership."
    )


@pytest.mark.parametrize(
    "path,banned_names",
    list(_PHASE6_PER_NAME_BAN.items()),
    ids=lambda v: (
        str(v.relative_to(_BACKEND_ROOT)).replace("\\", "/")
        if isinstance(v, Path)
        else "names"
    ),
)
def test_phase6_migrated_file_does_not_import_group_filter_from_facade(
    path: Path, banned_names: frozenset[str]
):
    """Phase-6 contract (BE-GO-READY-AUTH-ENGINE-011):
    `group_filter` canonical home is `app.core.auth.ownership`.
    The 9 app routers using it MUST consume the leaf directly;
    facade pull is a strangler regression. The auth.py facade
    keeps a KEEP_COMPAT re-export so the symbol identity is
    preserved for any test/code that still imports
    `from app.core.auth.auth import group_filter` — but the
    routers themselves are pinned to the leaf."""
    assert path.exists(), f"migrated file missing: {path}"
    violations = _per_name_ban_violations(path, banned_names)
    assert violations == [], (
        f"{path.relative_to(_BACKEND_ROOT)} pulls Phase-6 banned "
        f"name `group_filter` from the facade:\n"
        + "\n".join(f"  line {ln}: {nm}" for ln, nm in violations)
        + "\nLeaf source: group_filter ← app.core.auth.ownership."
    )


def test_group_filter_leaf_canonical_home():
    """Lock the canonical home: `group_filter` lives in
    `app.core.auth.ownership` (pure policy helper, zero side
    effects). The 9 app routers using it consume the leaf directly
    per the Phase-6 strangler ban above.

    BE-GO-READY-AUTH-ENGINE-014: the auth.py facade KEEP_COMPAT
    re-export was deleted. Negative lock asserting the cut lives
    in
    `test_auth_models_and_ownership_module_contract.py
    ::test_auth_no_longer_reexports_group_filter`. This test now
    only pins that the leaf canonical home is callable.
    """
    from app.core.auth.ownership import group_filter as gf_leaf

    assert callable(gf_leaf), (
        "`app.core.auth.ownership.group_filter` must remain "
        "callable as the canonical home for the policy helper."
    )


def test_auth_middleware_imports_decode_token_from_jwt_leaf():
    """Lock the exact leaf source: `decode_token` must come from
    `app.shared.jwt_token`, not the facade."""
    path = _APP / "core" / "auth" / "auth_middleware.py"
    text = path.read_text(encoding="utf-8")
    assert "from app.shared.jwt_token import decode_token" in text, (
        "auth_middleware.py must import decode_token from app.shared.jwt_token "
        "(strangler Phase-1)."
    )


def test_ownership_imports_roles_from_leaf_module():
    """Lock the eager top-level import of `ROLES_ADMIN_OR_ABOVE` from
    `app.core.auth.roles`. Pre-Phase-1 this was a lazy import from the
    facade inside `_check_ownership`."""
    path = _APP / "core" / "auth" / "ownership.py"
    text = path.read_text(encoding="utf-8")
    assert (
        "from app.core.auth.roles import ROLES_ADMIN_OR_ABOVE" in text
    ), (
        "ownership.py must import ROLES_ADMIN_OR_ABOVE from "
        "app.core.auth.roles (leaf module). The legacy lazy import "
        "from the facade has been removed."
    )
    # Defense in depth: lazy facade import body must be gone.
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_check_ownership":
            body_src = ast.unparse(node)
            assert (
                "from app.core.auth.auth import ROLES_ADMIN_OR_ABOVE" not in body_src
            ), (
                "_check_ownership must not contain the legacy lazy "
                "`from app.core.auth.auth import ROLES_ADMIN_OR_ABOVE` line."
            )


def test_authz_helpers_imports_models_and_ownership_leaves():
    """Lock the split: `CurrentUser` and ownership helpers come from
    leaf modules — NOT from the facade.

    Phase-9 (TE-015): `CurrentUser` source moved from
    `app.core.auth.models` → `app.shared.contracts.auth` (the
    contract module re-exports from auth.models, identity
    preserved). `assert_*` ownership helpers stay on
    `app.core.auth.ownership` (DB-bound canonical home)."""
    path = _APP / "shared" / "authz_helpers.py"
    text = path.read_text(encoding="utf-8")
    assert "from app.shared.contracts.auth import CurrentUser" in text
    assert "from app.core.auth.ownership import" in text
    assert "assert_group_access" in text
    assert "assert_resource_visible" in text


# Phase-2 leaf-import presence assertions


def test_phase2_orchestrator_imports_assert_group_access_from_ownership():
    """`orchestrator/router.py` must import `assert_group_access` from
    the ownership leaf."""
    path = _APP / "core" / "orchestrator" / "router.py"
    text = path.read_text(encoding="utf-8")
    assert (
        "from app.core.auth.ownership import assert_group_access" in text
    ), (
        "orchestrator/router.py must import assert_group_access from "
        "app.core.auth.ownership."
    )


def test_phase2_imdf_lazy_assert_resource_visible_migrated_to_leaf():
    """BE-GO-READY-AUTH-ENGINE-020 closed R-019.1: imdf/router.py
    no longer carries a body-local lazy `from app.core.auth.auth
    import assert_resource_visible`; the import is now top-level
    `from app.core.auth.ownership import assert_resource_visible`.
    The 2 IMDF test files monkeypatch the consuming router-module
    surface (`app.core.imdf.router.assert_resource_visible`)
    instead of the facade.

    This lock supersedes the prior Phase-2 residual contract
    (`test_phase2_imdf_lazy_assert_resource_visible_kept_on_facade_residual`):
    the lazy facade import MUST NOT reappear, and the leaf import
    MUST be present.
    """
    path = _APP / "core" / "imdf" / "router.py"
    text = path.read_text(encoding="utf-8")
    # AST scan for the banned facade import (more robust than a
    # raw substring grep because the migration audit comment in
    # imdf/router.py mentions the old line as historical context).
    tree = ast.parse(text, filename=str(path))
    facade_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == _FACADE_MODULE
        and any(a.name == "assert_resource_visible" for a in node.names)
    ]
    assert facade_imports == [], (
        "imdf/router.py regressed to facade import for "
        "assert_resource_visible at line(s) "
        f"{[n.lineno for n in facade_imports]}. "
        "Use `from app.core.auth.ownership import "
        "assert_resource_visible` instead."
    )
    assert (
        "from app.core.auth.ownership import assert_resource_visible"
        in text
    ), (
        "imdf/router.py must import `assert_resource_visible` from "
        "the ownership leaf at module top after AUTH-ENGINE-020 "
        "(closes R-019.1)."
    )


# Phase-3 leaf-import presence assertions


def test_phase3_features_routes_imports_ownership_helpers_from_leaf():
    """`features/routes.py` must import the 4 ownership helpers
    (`assert_group_access`, `assert_resource_visible`,
    `assert_can_modify`, `compute_can_edit`) from
    `app.core.auth.ownership` — NOT from the facade."""
    path = _APP / "core" / "features" / "routes.py"
    text = path.read_text(encoding="utf-8")
    assert "from app.core.auth.ownership import" in text, (
        "features/routes.py must import ownership helpers from "
        "app.core.auth.ownership leaf."
    )
    for name in (
        "assert_group_access",
        "assert_resource_visible",
        "assert_can_modify",
        "compute_can_edit",
    ):
        assert name in text, (
            f"features/routes.py must still consume {name} (from "
            "ownership leaf)."
        )


# Phase-4 leaf-import presence assertions


@pytest.mark.parametrize(
    "path,expected_helpers",
    [
        (
            _APP / "core" / "arricchisci" / "router.py",
            ("assert_group_access", "assert_resource_visible",
             "assert_can_modify", "compute_can_edit"),
        ),
        (
            _APP / "core" / "carica" / "router.py",
            ("assert_resource_visible",),
        ),
        (
            _APP / "core" / "connections" / "router.py",
            ("assert_group_access", "assert_resource_visible",
             "assert_can_modify", "compute_can_edit"),
        ),
        (
            _APP / "core" / "datasources" / "router.py",
            ("assert_group_access", "assert_resource_visible",
             "assert_can_modify", "compute_can_edit"),
        ),
        (
            _APP / "core" / "eseguibili" / "router.py",
            ("assert_group_access", "assert_resource_visible",
             "assert_can_modify", "compute_can_edit"),
        ),
    ],
    ids=lambda v: (
        str(v.relative_to(_BACKEND_ROOT)).replace("\\", "/")
        if isinstance(v, Path)
        else "helpers"
    ),
)
def test_phase4_imports_ownership_helpers_from_leaf(
    path: Path, expected_helpers: tuple[str, ...]
):
    """Each Phase-4 migrated file must import its ownership helpers
    from `app.core.auth.ownership` — NOT from the facade."""
    text = path.read_text(encoding="utf-8")
    assert "from app.core.auth.ownership import" in text, (
        f"{path.name} must import ownership helpers from "
        "app.core.auth.ownership leaf."
    )
    for name in expected_helpers:
        assert name in text, (
            f"{path.name} must still consume {name} (from ownership leaf)."
        )


# Phase-7 (BE-GO-READY-AUTH-ENGINE-013) contract fan-out
#
# Phase-7 moves pure-contract consumption (`CurrentUser`,
# `group_filter`) for a coherent group of routers from the leaf
# implementation modules (`app.core.auth.models`,
# `app.core.auth.ownership`) to the shared contract module
# `app.shared.contracts.auth`. Identity is preserved end-to-end:
# the contract module re-exports `CurrentUser` from `auth.models`
# and defines `group_filter` as the canonical pure policy
# (`auth.ownership.group_filter` re-exports for KEEP_COMPAT).
#
# Routers continue to import:
#   - DB-bound ownership helpers (`assert_*`, `compute_can_edit`)
#     from `app.core.auth.ownership` (their canonical home — they
#     drag `app.db.db_service`);
#   - FastAPI Depends + `require_*` from the auth facade.
#
# The 5 routers below were migrated in TE-013 (this batch).
# Phase-7 supersedes the Phase-3/4/5 `models`-direct-leaf
# assertions for these files.

_PHASE7_CONTRACT_FAN_OUT_FILES = (
    _APP / "core" / "features" / "routes.py",
    _APP / "core" / "files" / "router.py",
    _APP / "core" / "connections" / "router.py",
    _APP / "core" / "datasources" / "router.py",
    _APP / "core" / "eseguibili" / "router.py",
)


@pytest.mark.parametrize(
    "path",
    _PHASE7_CONTRACT_FAN_OUT_FILES,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase7_imports_currentuser_and_group_filter_from_contract(path: Path):
    """Phase-7 contract fan-out: `CurrentUser` AND `group_filter`
    must come from `app.shared.contracts.auth` — NOT from
    `app.core.auth.models` / `app.core.auth.ownership`."""
    text = path.read_text(encoding="utf-8")
    assert (
        "from app.shared.contracts.auth import" in text
    ), (
        f"{path.relative_to(_BACKEND_ROOT)} must import from "
        "app.shared.contracts.auth (Phase-7 contract fan-out)."
    )
    for name in ("CurrentUser", "group_filter"):
        assert name in text, (
            f"{path.name} must consume `{name}` (Phase-7 contract)."
        )


@pytest.mark.parametrize(
    "path",
    _PHASE7_CONTRACT_FAN_OUT_FILES,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase7_does_not_import_currentuser_from_models_leaf(path: Path):
    """Phase-7 contract: source-bound check that the migrated file
    does NOT import `CurrentUser` from `app.core.auth.models`
    (canonical for Phase-3/4/5; superseded by the contract for
    Phase-7 files)."""
    text = path.read_text(encoding="utf-8")
    assert (
        "from app.core.auth.models import CurrentUser" not in text
    ), (
        f"{path.relative_to(_BACKEND_ROOT)} regressed to importing "
        "CurrentUser from `app.core.auth.models` — Phase-7 routes "
        "this through `app.shared.contracts.auth` instead."
    )


@pytest.mark.parametrize(
    "path",
    _PHASE7_CONTRACT_FAN_OUT_FILES,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase7_does_not_import_group_filter_from_ownership_leaf(path: Path):
    """Phase-7 contract: source-bound check that the migrated file
    does NOT import `group_filter` from `app.core.auth.ownership`
    (KEEP_COMPAT re-export still exists, but the canonical
    home is the contract module). Detects via AST to avoid false
    positives when `group_filter` appears only as a comment."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.auth.ownership":
            names = {a.name for a in node.names}
            assert "group_filter" not in names, (
                f"{path.relative_to(_BACKEND_ROOT)} line {node.lineno}: "
                "regressed to importing `group_filter` from "
                "`app.core.auth.ownership` — Phase-7 routes this "
                "through `app.shared.contracts.auth`."
            )


# Phase-8 (BE-GO-READY-AUTH-ENGINE-014) contract fan-out
#
# Phase-8 closes TE13-R1/R2: the 5 routers below previously
# imported `CurrentUser` from `app.core.auth.models`. They are
# now migrated to consume from `app.shared.contracts.auth`.
# Identity preserved (the contract re-exports `CurrentUser`
# from `auth.models`).
#
# Distinction from Phase-7: these 5 routers do NOT use
# `group_filter` — only `CurrentUser` was migrated. DB-bound
# ownership helpers stay on `app.core.auth.ownership` where
# present. FastAPI Depends + `require_*` stay on the auth facade.

_PHASE8_CONTRACT_FAN_OUT_FILES = (
    _APP / "core" / "arricchisci" / "router.py",
    _APP / "core" / "carica" / "router.py",
    _APP / "core" / "logs" / "runs_router.py",
    _APP / "core" / "estrai" / "router.py",
    _APP / "core" / "manipola" / "api" / "transforms_api.py",
)


@pytest.mark.parametrize(
    "path",
    _PHASE8_CONTRACT_FAN_OUT_FILES,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase8_imports_currentuser_from_contract(path: Path):
    """Phase-8 contract fan-out: `CurrentUser` must come from
    `app.shared.contracts.auth` — NOT from `app.core.auth.models`."""
    text = path.read_text(encoding="utf-8")
    assert (
        "from app.shared.contracts.auth import" in text
        and "CurrentUser" in text
    ), (
        f"{path.relative_to(_BACKEND_ROOT)} must import CurrentUser "
        "from app.shared.contracts.auth (Phase-8 contract fan-out)."
    )


@pytest.mark.parametrize(
    "path",
    _PHASE8_CONTRACT_FAN_OUT_FILES,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase8_does_not_import_currentuser_from_models_leaf(path: Path):
    """Phase-8 contract: source-bound check that the migrated file
    does NOT import `CurrentUser` from `app.core.auth.models`
    (Phase-3/4 canonical; superseded by the contract for Phase-8
    files). Detects via AST to avoid false positives when
    `CurrentUser` appears only as a comment or in another import
    block."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.auth.models":
            names = {a.name for a in node.names}
            assert "CurrentUser" not in names, (
                f"{path.relative_to(_BACKEND_ROOT)} line {node.lineno}: "
                "regressed to importing CurrentUser from "
                "`app.core.auth.models` — Phase-8 routes this "
                "through `app.shared.contracts.auth`."
            )


# Phase-9 (BE-GO-READY-AUTH-ENGINE-015) contract fan-out + facade death map
#
# Phase-9 closes TE14-R1: the remaining 8 non-router consumers of
# `CurrentUser` from `app.core.auth.models` migrate to consume
# from `app.shared.contracts.auth`. After this batch:
#   - `app.core.auth.ownership` retains its sibling-package
#     direct import from `auth.models` (cycle prevention — the
#     contract module re-exports FROM ownership/models, so
#     `ownership` cannot route through the contract);
#   - `app.shared.contracts.auth` itself owns the canonical
#     re-export (`from app.core.auth.models import CurrentUser`);
#   - all OTHER `app/+workers/` callsites consume from the
#     contract.
#
# These 8 files include 2 services/helpers (Phase-1 full-ban
# files extending), 2 worker-side helpers, and 6 routers (some
# previously on Phase-2/5 per-name ban — those bans still hold
# for the OTHER banned names; CurrentUser source moves only).

_PHASE9_CONTRACT_FAN_OUT_FILES = (
    _APP / "core" / "admin_backups" / "router.py",
    _APP / "core" / "admin_system_status" / "router.py",
    _APP / "core" / "imdf" / "router.py",
    _APP / "core" / "orchestrator" / "router.py",
    _APP / "core" / "tickets" / "router.py",
    _APP / "core" / "tickets" / "service.py",
    _APP / "shared" / "async_user_recheck.py",
    _APP / "shared" / "authz_helpers.py",
)


@pytest.mark.parametrize(
    "path",
    _PHASE9_CONTRACT_FAN_OUT_FILES,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase9_imports_currentuser_from_contract(path: Path):
    """Phase-9 contract fan-out: `CurrentUser` must come from
    `app.shared.contracts.auth` — NOT from `app.core.auth.models`."""
    text = path.read_text(encoding="utf-8")
    assert (
        "from app.shared.contracts.auth import" in text
        and "CurrentUser" in text
    ), (
        f"{path.relative_to(_BACKEND_ROOT)} must import CurrentUser "
        "from app.shared.contracts.auth (Phase-9 contract fan-out)."
    )


@pytest.mark.parametrize(
    "path",
    _PHASE9_CONTRACT_FAN_OUT_FILES,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase9_does_not_import_currentuser_from_models_leaf(path: Path):
    """Phase-9 contract: source-bound check that the migrated file
    does NOT import `CurrentUser` from `app.core.auth.models`.
    AST-bound to avoid false positives from comments."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.auth.models":
            names = {a.name for a in node.names}
            assert "CurrentUser" not in names, (
                f"{path.relative_to(_BACKEND_ROOT)} line {node.lineno}: "
                "regressed to importing CurrentUser from "
                "`app.core.auth.models` — Phase-9 routes this "
                "through `app.shared.contracts.auth`."
            )


# Files that legitimately keep `from app.core.auth.models import
# CurrentUser` post-Phase-9. Three categories:
#
# A) The auth facade itself (`app.core.auth.auth`): re-exports
#    `CurrentUser` for KEEP_COMPAT (`from app.core.auth.auth
#    import CurrentUser` keeps working for any external caller
#    not yet migrated to the contract).
# B) The contract module IS the canonical re-export site
#    (`shared/contracts/auth.py`) — it MUST consume
#    `auth.models` directly to provide the contract surface.
#
# BE-GO-READY-AUTH-ENGINE-011-SHARED-CURRENT-USER-CONTRACT:
# `auth.ownership`, `auth.users_router`, and `auth.groups_router`
# were trimmed from this allowlist. The "siblings keep direct
# import to avoid round-trip noise" reasoning was overridden by
# the W-011 brief: the round-trip is one extra import edge but
# yields a cleaner architectural shape (auth-package internals
# go through the same contract surface that external routers
# use, so a future contributor cannot tell `auth/` siblings from
# any other consumer by their import shape). The contract
# re-exports the same `CurrentUser` class (identity preserved),
# so behavior is unchanged.
_PHASE9_MODELS_LEAF_ALLOWLIST = frozenset({
    str((_APP / "core" / "auth" / "auth.py").relative_to(_BACKEND_ROOT)).replace("\\", "/"),
    str((_APP / "shared" / "contracts" / "auth.py").relative_to(_BACKEND_ROOT)).replace("\\", "/"),
})


def test_phase9_models_leaf_currentuser_consumers_locked_to_allowlist():
    """Source-bound facade death map lock: after Phase-9, only the
    explicit allowlist (`auth.ownership`, `contracts.auth`) is
    permitted to import `CurrentUser` directly from
    `app.core.auth.models`. Every other consumer regression to
    the leaf would be caught here."""
    found: set[str] = set()
    for py in _APP.rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "from app.core.auth.models" not in text:
            continue
        try:
            tree = ast.parse(text, filename=str(py))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.core.auth.models":
                if any(a.name == "CurrentUser" for a in node.names):
                    rel = str(py.relative_to(_BACKEND_ROOT)).replace("\\", "/")
                    found.add(rel)
                    break

    extra = found - _PHASE9_MODELS_LEAF_ALLOWLIST
    assert not extra, (
        "Files outside the Phase-9 allowlist regressed to importing "
        "CurrentUser from `app.core.auth.models`:\n"
        + "\n".join(f"  {p}" for p in sorted(extra))
        + "\nMigrate them to `from app.shared.contracts.auth import "
        "CurrentUser` instead, or extend "
        "_PHASE9_MODELS_LEAF_ALLOWLIST with documented rationale."
    )

    missing = _PHASE9_MODELS_LEAF_ALLOWLIST - found
    assert not missing, (
        "Phase-9 allowlist names files that no longer import "
        "CurrentUser from auth.models. Trim the allowlist:\n"
        + "\n".join(f"  {p}" for p in sorted(missing))
    )


# BE-GO-READY-AUTH-ENGINE-011-SHARED-CURRENT-USER-CONTRACT
#
# W-011 migrated the three remaining `app.core.auth/` consumers
# (`ownership.py`, `users_router.py`, `groups_router.py`) to
# import `CurrentUser` from `app.shared.contracts.auth` instead
# of `app.core.auth.models`. The Phase-9 allowlist trimmed those
# three files (above). This block adds the POSITIVE lock: each
# of the three migrated files MUST now import `CurrentUser`
# from the shared contract.
#
# Distinct from the Phase-7 router lock in
# `test_auth_shared_contract_module_contract.py` (which covers
# `logs/intermediate/projects/scheduler` non-auth routers): the
# files locked here are auth-package internals that previously
# stayed on the models leaf for "round-trip noise" reasoning.
# W-011 reverses that decision -- one consistent contract
# boundary across all consumers.

_AUTH_ENGINE_011_MIGRATED = (
    _APP / "core" / "auth" / "ownership.py",
    _APP / "core" / "auth" / "users_router.py",
    _APP / "core" / "auth" / "groups_router.py",
)


def _imports_named_from(path: Path, module: str, name: str) -> bool:
    """Return True iff ``path`` has a top-level
    ``from <module> import ... <name> ...`` ImportFrom node."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            if any(a.name == name for a in node.names):
                return True
    return False


@pytest.mark.parametrize(
    "path",
    _AUTH_ENGINE_011_MIGRATED,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_auth_engine_011_migrated_imports_current_user_from_contract(
    path: Path,
):
    """W-011 contract: the three migrated `app.core.auth/`
    consumers MUST import `CurrentUser` from the shared contract
    module. Reverting the import edge to `app.core.auth.models`
    re-introduces the auth-package coupling W-011 closed.

    Identity is preserved (`contracts.auth.CurrentUser is
    auth.models.CurrentUser` -- locked separately by
    `test_contract_current_user_is_leaf_identity`), so behavior
    is unchanged; only the import shape is."""
    assert path.exists(), f"W-011 migrated file missing: {path}"
    assert _imports_named_from(
        path, "app.shared.contracts.auth", "CurrentUser",
    ), (
        f"{path.relative_to(_BACKEND_ROOT)} must import "
        "`CurrentUser` from `app.shared.contracts.auth` "
        "(BE-GO-READY-AUTH-ENGINE-011). The contract re-exports "
        "the same class from `app.core.auth.models`; behavior "
        "is unchanged but the consumer-side import edge moves to "
        "the shared boundary."
    )


@pytest.mark.parametrize(
    "path",
    _AUTH_ENGINE_011_MIGRATED,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_auth_engine_011_migrated_does_not_import_current_user_from_models(
    path: Path,
):
    """W-011 contract negative: the three migrated files MUST
    NOT pull `CurrentUser` directly from `app.core.auth.models`.
    Defense-in-depth alongside the Phase-9 allowlist trim --
    catches a regression where a contributor adds `CurrentUser`
    BACK to a `from app.core.auth.models import (...)` block."""
    assert path.exists(), f"W-011 migrated file missing: {path}"
    assert not _imports_named_from(
        path, "app.core.auth.models", "CurrentUser",
    ), (
        f"{path.relative_to(_BACKEND_ROOT)} pulls `CurrentUser` "
        "from `app.core.auth.models` directly. W-011 contract: "
        "router/ownership code consumes `CurrentUser` from "
        "`app.shared.contracts.auth` (which re-exports from the "
        "models leaf -- identity preserved). The other DTOs that "
        "remain on the models leaf are out of W-011 scope."
    )


# BE-GO-READY-AUTH-ENGINE-013-ROUTER-IMPORTS-OFF-AUTH-FACADE
#
# W-013 created the canonical leaf `app.core.auth.dependencies`
# for the five FastAPI auth Depends callables
# (`get_current_user`, `require_user_or_above`,
# `require_not_esecutore`, `require_admin`,
# `require_superadmin`). The `app.core.auth.auth` facade keeps
# re-exporting them for KEEP_COMPAT (identity preserved -- the
# facade's `from app.core.auth.dependencies import ...` block
# is one canonical re-export step, no duplication).
#
# The bounded migration in this batch flipped two routers in
# `app.core.logs/` to consume the dependency callables from
# the leaf instead of the facade. This positive lock prevents
# either of those routers from regressing back to
# `from app.core.auth.auth import get_current_user, ...`.
# Other routers (logs/intermediate/projects/scheduler etc.)
# remain on the facade pending their own bounded migrations
# in future batches.

_AUTH_ENGINE_013_DEPENDENCY_LEAF = "app.core.auth.dependencies"
_AUTH_ENGINE_013_LEAF_SYMBOLS = (
    "get_current_user",
    "require_user_or_above",
    "require_not_esecutore",
    "require_admin",
    "require_superadmin",
)
_AUTH_ENGINE_013_MIGRATED_ROUTERS = (
    _APP / "core" / "logs" / "router.py",
    _APP / "core" / "logs" / "runs_router.py",
)


@pytest.mark.parametrize(
    "path",
    _AUTH_ENGINE_013_MIGRATED_ROUTERS,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_auth_engine_013_migrated_router_imports_dependencies_from_leaf(
    path: Path,
):
    """W-013 contract: each migrated router MUST import the
    Depends callables it actually uses from the canonical leaf
    `app.core.auth.dependencies`. Reverting to
    `from app.core.auth.auth import get_current_user, ...`
    re-introduces the facade coupling W-013 closed.

    Identity is preserved (`auth.get_current_user is
    dependencies.get_current_user`) so behavior is unchanged;
    only the import edge moves to the canonical owner."""
    assert path.exists(), f"W-013 migrated router missing: {path}"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_leaf: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _AUTH_ENGINE_013_DEPENDENCY_LEAF:
            for alias in node.names:
                imported_from_leaf.add(alias.name)
    leaf_intersect = imported_from_leaf & set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert leaf_intersect, (
        f"{path.relative_to(_BACKEND_ROOT)} must import at least "
        f"one of {_AUTH_ENGINE_013_LEAF_SYMBOLS} from "
        f"`{_AUTH_ENGINE_013_DEPENDENCY_LEAF}`. W-013 created the "
        "canonical leaf; the migrated router must consume from it "
        "to avoid the facade-coupling regression."
    )


@pytest.mark.parametrize(
    "path",
    _AUTH_ENGINE_013_MIGRATED_ROUTERS,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_auth_engine_013_migrated_router_does_not_import_dependencies_from_facade(
    path: Path,
):
    """W-013 negative lock: the two migrated routers MUST NOT
    pull any of the five dependency callables from
    `app.core.auth.auth` (the facade). Defense-in-depth
    alongside the positive lock: catches a regression where a
    contributor adds a dep import via the facade alongside the
    leaf import (e.g. `from app.core.auth.auth import
    require_admin` next to the leaf import).

    The migrated routers MAY still import non-dependency
    symbols from the facade if needed (none currently); the
    lock is narrow to the five Depends callables locked here."""
    assert path.exists(), f"W-013 migrated router missing: {path}"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_facade: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.auth.auth":
            for alias in node.names:
                imported_from_facade.add(alias.name)
    facade_intersect = imported_from_facade & set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert not facade_intersect, (
        f"{path.relative_to(_BACKEND_ROOT)} pulls "
        f"{sorted(facade_intersect)} from `app.core.auth.auth` "
        "(the facade). W-013 contract: dependency callables "
        "consumed from `app.core.auth.dependencies` (the "
        "canonical leaf). Identity is preserved across the "
        "facade re-export, but the migrated router must point "
        "at the leaf so the facade-coupling regression cannot "
        "land silently."
    )


def test_auth_engine_013_dependencies_leaf_exports_all_five_callables():
    """W-013 contract: the canonical leaf
    `app.core.auth.dependencies` MUST expose all five
    dependency callables in `__all__`. Drift here would let a
    future contributor add a private helper without the
    explicit public-surface declaration the facade re-export
    relies on."""
    import app.core.auth.dependencies as deps

    expected = set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert hasattr(deps, "__all__"), (
        "app.core.auth.dependencies must declare `__all__` so the "
        "public dependency surface is explicit."
    )
    actual = set(deps.__all__)
    assert actual == expected, (
        f"app.core.auth.dependencies `__all__` drift:\n"
        f"  expected: {sorted(expected)}\n"
        f"  actual:   {sorted(actual)}"
    )
    for name in expected:
        assert hasattr(deps, name), (
            f"app.core.auth.dependencies missing documented "
            f"dependency callable: {name}"
        )


def test_auth_engine_013_facade_reexports_share_identity_with_leaf():
    """W-013 contract: the facade re-export of each dependency
    callable MUST be the SAME Python object as the leaf
    definition. Without this property, FastAPI `Depends(...)`
    resolutions through the two import paths would diverge,
    breaking caching keyed on the callable identity."""
    import app.core.auth.auth as facade
    import app.core.auth.dependencies as leaf

    for name in _AUTH_ENGINE_013_LEAF_SYMBOLS:
        leaf_obj = getattr(leaf, name)
        facade_obj = getattr(facade, name)
        assert leaf_obj is facade_obj, (
            f"Identity broken for `{name}`: "
            f"`app.core.auth.auth.{name}` is NOT "
            f"`app.core.auth.dependencies.{name}`. The facade "
            "must re-export the leaf's callable as the same "
            "Python object; FastAPI Depends resolution depends "
            "on it."
        )


# Phase-10 (BE-GO-READY-AUTH-ENGINE-016) minor-DTO contract fan-out
#
# Phase-10 extends the contract module to expose 12 pure auth
# DTOs (`LoginRequest`, `TokenResponse`, `UserCreate`, etc.).
# Pre-batch the only direct importers in `app/+workers/` were
# the 5 sibling files within `app.core.auth.*` (the facade itself
# + the extracted users/groups handlers + ownership) and the
# contract module's own re-export. There are NO non-sibling
# consumers in the runtime tree at the start of TE-016 — the fan-
# out is contract-side only, opening the door for future
# consumers (especially tests + worker-side helpers) to migrate.
#
# The allowlist guard below catches any future regression to
# `from app.core.auth.models import LoginRequest` (etc.) by
# non-sibling consumers — they must instead import from
# `app.shared.contracts.auth`.

_PHASE10_MINOR_DTOS = frozenset({
    "LoginRequest",
    "TokenResponse",
    "UserCreate",
    "ChangePasswordRequest",
    "UserResponse",
    "ResetPasswordRequest",
    "GroupCreate",
    "GroupResponse",
    "MFAEnrollResponse",
    "MFAVerifyRequest",
    "MFADisableRequest",
    "MFAStatusResponse",
})

# Same allowlist shape as Phase-9 — the 5 sibling/contract-owner
# files that legitimately import from `app.core.auth.models`.
# Reuses `_PHASE9_MODELS_LEAF_ALLOWLIST` semantically; declared
# separately for the DTO-specific guard's failure message.
_PHASE10_MODELS_LEAF_ALLOWLIST = frozenset({
    str((_APP / "core" / "auth" / "auth.py").relative_to(_BACKEND_ROOT)).replace("\\", "/"),
    str((_APP / "core" / "auth" / "ownership.py").relative_to(_BACKEND_ROOT)).replace("\\", "/"),
    str((_APP / "core" / "auth" / "users_router.py").relative_to(_BACKEND_ROOT)).replace("\\", "/"),
    str((_APP / "core" / "auth" / "groups_router.py").relative_to(_BACKEND_ROOT)).replace("\\", "/"),
    str((_APP / "shared" / "contracts" / "auth.py").relative_to(_BACKEND_ROOT)).replace("\\", "/"),
})


def test_phase10_minor_dto_consumers_locked_to_allowlist():
    """Whole-tree scan: only the documented allowlist files may
    import any of the 12 Phase-10 minor DTOs from
    `app.core.auth.models` directly. Every other consumer must
    go through `app.shared.contracts.auth`."""
    found: dict[str, set[str]] = {}
    for py in _APP.rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "from app.core.auth.models" not in text:
            continue
        try:
            tree = ast.parse(text, filename=str(py))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.core.auth.models":
                hit_dtos = {a.name for a in node.names if a.name in _PHASE10_MINOR_DTOS}
                if hit_dtos:
                    rel = str(py.relative_to(_BACKEND_ROOT)).replace("\\", "/")
                    found.setdefault(rel, set()).update(hit_dtos)

    extra = {p: dtos for p, dtos in found.items() if p not in _PHASE10_MODELS_LEAF_ALLOWLIST}
    assert not extra, (
        "Files outside the Phase-10 allowlist regressed to "
        "importing minor auth DTOs from `app.core.auth.models`:\n"
        + "\n".join(f"  {p}: {sorted(dtos)}" for p, dtos in sorted(extra.items()))
        + "\nMigrate them to `from app.shared.contracts.auth "
        "import <DTO>` instead, or extend "
        "_PHASE10_MODELS_LEAF_ALLOWLIST with documented rationale."
    )


def test_phase10_contract_module_exposes_all_minor_dtos():
    """Source-bound: `app.shared.contracts.auth` MUST expose
    every Phase-10 minor DTO in its `__all__`. Otherwise the
    fan-out is incomplete — consumers cannot route through the
    contract without a missing symbol."""
    contract_path = _APP / "shared" / "contracts" / "auth.py"
    text = contract_path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(contract_path))

    # Find `__all__` assignment.
    all_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                all_names.add(elt.value)

    missing = _PHASE10_MINOR_DTOS - all_names
    assert not missing, (
        "app/shared/contracts/auth.py `__all__` is missing "
        "Phase-10 minor DTOs:\n"
        + "\n".join(f"  {n}" for n in sorted(missing))
        + "\nAdd them to `__all__` AND to the `from "
        "app.core.auth.models import (...)` block."
    )


# Phase-11 (BE-GO-READY-AUTH-ENGINE-017) — pure-DTO consumer migration
# in tests2/ + workers/
#
# Phase-11 extends the Phase-9/10 contract fan-out to the test
# tree (`tests2/`) and worker tree (`workers/`). Pure-DTO names
# (`CurrentUser`, the 12 minor DTOs, role tuples, `group_filter`)
# previously imported from `app.core.auth.auth` (facade re-export)
# or `app.core.auth.models` (leaf) by non-app-tree consumers
# must now route through `app.shared.contracts.auth`.
#
# Allowlist: 2 contract test files that legitimately need to
# import directly from the facade/leaf to assert the identity
# property (`auth.X is contract.X is models.X`). Adding any
# new consumer outside the allowlist requires migrating that
# consumer, not extending the allowlist silently.
#
# Distinction from Phase-9/10:
#   - Phase-9/10 scan `_APP.rglob` (production tree).
#   - Phase-11 scans `tests2/` and `workers/`.
#   - Both feed into the same architectural goal: the runtime +
#     test surface consume `app.shared.contracts.auth` for pure
#     DTOs; only auth siblings + the contract module itself
#     consume from the leaf.

_PURE_DTO_NAMES_PHASE11 = frozenset({
    "CurrentUser",
    # Role tuples (constants, pure)
    "VALID_ROLES",
    "ROLES_ABOVE_VIEWER",
    "ROLES_ADMIN_OR_ABOVE",
    "ROLES_KIOSK_ONLY",
    # Pure policy
    "group_filter",
    # Phase-10 minor DTOs
    "LoginRequest",
    "TokenResponse",
    "UserCreate",
    "ChangePasswordRequest",
    "UserResponse",
    "ResetPasswordRequest",
    "GroupCreate",
    "GroupResponse",
    "MFAEnrollResponse",
    "MFAVerifyRequest",
    "MFADisableRequest",
    "MFAStatusResponse",
})


# Allowlist for Phase-11: contract test files that MUST import
# directly from the facade/leaf to assert identity equality
# (`auth.X is contract.X`). They are fundamentally testing the
# strangler/contract-module wiring, so routing them through the
# contract module would tautologize the tests.
#
# BE-GO-READY-AUTH-ENGINE-014: removed
# `tests2/security/unit/test_auth_facade_strangler_contract.py`
# from the allowlist. Its previous facade-leaf identity assertion
# was the `group_filter` re-export check, which has been deleted
# along with the re-export itself. The test file no longer imports
# any pure-DTO name from `app.core.auth.auth` /
# `app.core.auth.models`; keeping the allowlist entry would be a
# stale exemption.
_PHASE11_FACADE_LEAF_ALLOWLIST = frozenset({
    "tests2/security/unit/test_auth_shared_contract_module_contract.py",
})


def _scan_tree_for_pure_dto_facade_or_leaf_imports(roots: list[Path]) -> dict[str, list[tuple[int, str, list[str]]]]:
    """Scan `roots` for ImportFrom of pure DTO names from
    `app.core.auth.auth` or `app.core.auth.models`. Return dict
    {rel_path: [(lineno, module, names), ...]}.
    """
    found: dict[str, list[tuple[int, str, list[str]]]] = {}
    for root in roots:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            try:
                tree = ast.parse(text, filename=str(py))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.module not in (
                    "app.core.auth.auth", "app.core.auth.models",
                ):
                    continue
                hit = [a.name for a in node.names if a.name in _PURE_DTO_NAMES_PHASE11]
                if not hit:
                    continue
                rel = str(py.relative_to(_BACKEND_ROOT)).replace("\\", "/")
                found.setdefault(rel, []).append(
                    (node.lineno, node.module, hit)
                )
    return found


def test_phase11_tests2_workers_pure_dto_consumers_locked_to_allowlist():
    """BE-GO-READY-AUTH-ENGINE-017: pure-DTO consumers in
    `tests2/` + `workers/` must route through
    `app.shared.contracts.auth`. The only exceptions are 2
    contract test files asserting identity equality between
    facade/leaf/contract paths.

    Migration target: any test/worker file importing
    `CurrentUser`, the 12 minor DTOs, role tuples, or
    `group_filter` from `app.core.auth.auth` /
    `app.core.auth.models` must instead import from
    `app.shared.contracts.auth`. Mixed imports (pure-DTO +
    facade implementation names) must split into 2 lines.
    """
    roots = [_BACKEND_ROOT / "tests2", _BACKEND_ROOT / "workers"]
    found = _scan_tree_for_pure_dto_facade_or_leaf_imports(roots)
    extra = {
        rel: hits for rel, hits in found.items()
        if rel not in _PHASE11_FACADE_LEAF_ALLOWLIST
    }
    assert not extra, (
        "Phase-11 (TE-017) regression: files in tests2/ + workers/ "
        "outside the allowlist still import pure DTO names "
        "directly from `app.core.auth.auth` or "
        "`app.core.auth.models`:\n"
        + "\n".join(
            f"  {rel}: " + "; ".join(
                f"line {ln} from {mod} import {sorted(nm)}"
                for (ln, mod, nm) in hits
            )
            for rel, hits in sorted(extra.items())
        )
        + "\nMigrate them to `from app.shared.contracts.auth "
        "import <name>`. For mixed imports, split into 2 lines: "
        "pure DTOs from contracts; impure facade names "
        "(require_*, get_current_user, etc.) from "
        "`app.core.auth.auth`."
    )


def test_phase11_allowlist_is_used_only_when_legitimate():
    """Lock complementare: l'allowlist Phase-11 deve elencare
    SOLO file che hanno effettivamente import facade/leaf attivi.
    Trim regression: se uno dei 2 file allowlistati smettesse di
    importare pure-DTO names da auth.auth/models, il rationale
    dell'allowlist sarebbe stantio e l'entry andrebbe rimossa.
    """
    roots = [_BACKEND_ROOT / "tests2", _BACKEND_ROOT / "workers"]
    found = _scan_tree_for_pure_dto_facade_or_leaf_imports(roots)
    stale_allowlist_entries = _PHASE11_FACADE_LEAF_ALLOWLIST - set(found.keys())
    assert not stale_allowlist_entries, (
        "Phase-11 allowlist contains entries that no longer "
        "import pure-DTO names from auth.auth/auth.models:\n"
        + "\n".join(f"  {p}" for p in sorted(stale_allowlist_entries))
        + "\nTrim them from `_PHASE11_FACADE_LEAF_ALLOWLIST`."
    )


# Phase-12 (BE-GO-READY-AUTH-ENGINE-018) — pure-DTO consumer migration
# in app/+workers/ FROM the facade
#
# Phase-12 closes the symmetry gap left by Phase-9/10/11:
#
#   - Phase-9 covers `from app.core.auth.models import CurrentUser`
#     in `app/+workers/` (allowlist = 5 sibling files).
#   - Phase-10 covers `from app.core.auth.models import <minor_DTO>`
#     in `app/+workers/` (same allowlist).
#   - Phase-11 covers BOTH facade and leaf in `tests2/+workers/`.
#   - Phase-12 (this lock) covers FACADE-side imports in `app/+workers/`:
#     `from app.core.auth.auth import <pure_DTO_name>`.
#
# Pure-DTO names = `CurrentUser` + 12 minor DTOs + 4 role tuples +
# `group_filter` (the same surface enumerated by Phase-11). Allowlist
# contains only the facade module itself (`app/core/auth/auth.py`)
# which is the canonical re-export site — every other consumer must
# go through `app.shared.contracts.auth`.
#
# The facade still re-exports these names for the test surface
# (`auth_mod.<name>` monkey-patch contracts in `tests2/security/unit/`),
# so this lock only restricts NEW production-code imports from
# pulling DTOs via the facade. Removal of the facade re-exports
# themselves is gated on test-side migration (see Phase-11's
# tests2/ allowlist).

_PHASE12_PURE_DTO_NAMES = _PURE_DTO_NAMES_PHASE11

# Allowlist: only `auth.py` itself may import pure DTO names from
# `app.core.auth.auth` (the facade). Every other file in
# `app/+workers/` must route through the contract module. The
# facade self-import is a degenerate case (it imports `from
# app.core.auth.models`, not from itself), so this allowlist is
# conceptually empty — but we keep it as an explicit set so a
# future degenerate import would still surface as a controlled
# allowlist entry rather than silent regression.
_PHASE12_FACADE_DTO_ALLOWLIST: frozenset[str] = frozenset()


def test_phase12_app_workers_no_pure_dto_imports_from_facade():
    """BE-GO-READY-AUTH-ENGINE-018: pure-DTO consumers in
    `app/+workers/` MUST NOT import `CurrentUser`, the 12 minor
    DTOs, role tuples, or `group_filter` from
    `app.core.auth.auth` (the facade). The contract module
    `app.shared.contracts.auth` is the canonical consumer-facing
    source. Phase-9/10 already cover the symmetric ban for
    `app.core.auth.models`; Phase-12 closes the gap on the
    facade side.

    Failure mode: a developer adds a new production-code module
    that does `from app.core.auth.auth import LoginRequest` (etc.)
    and bypasses the contract. The strangler must not silently
    accept that.
    """
    roots = [_APP, _BACKEND_ROOT / "workers"]
    found: dict[str, list[tuple[int, list[str]]]] = {}
    for root in roots:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            try:
                tree = ast.parse(text, filename=str(py))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.module != _FACADE_MODULE:
                    continue
                hit = [a.name for a in node.names if a.name in _PHASE12_PURE_DTO_NAMES]
                if not hit:
                    continue
                rel = str(py.relative_to(_BACKEND_ROOT)).replace("\\", "/")
                found.setdefault(rel, []).append((node.lineno, hit))

    extra = {
        rel: hits for rel, hits in found.items()
        if rel not in _PHASE12_FACADE_DTO_ALLOWLIST
    }
    assert not extra, (
        "Phase-12 (TE-018) regression: files in app/+workers/ "
        "import pure DTO / role / group_filter names from the "
        "facade `app.core.auth.auth`:\n"
        + "\n".join(
            f"  {rel}: " + "; ".join(
                f"line {ln} import {sorted(nm)}"
                for (ln, nm) in hits
            )
            for rel, hits in sorted(extra.items())
        )
        + "\nMigrate them to `from app.shared.contracts.auth "
        "import <name>` instead. The facade re-export still "
        "exists for test monkey-patch contracts, but production "
        "consumers must route through the contract module."
    )


# Phase-13 (BE-GO-READY-AUTH-ENGINE-019) ownership re-export removal
#
# Stream 019 removed 4 ownership helper re-exports from the facade
# (`app.core.auth.auth`):
#
#   - `_extract_group_and_id`
#   - `assert_group_access`
#   - `compute_can_edit`
#   - `assert_can_modify`
#
# Audit findings: zero callsites in `app/` or `tests2/` depended on
# facade identity for any of these. All 6 production routers that
# consume ownership helpers (files / arricchisci / features /
# connections / datasources / eseguibili) already import from the
# leaf `app.core.auth.ownership` (Phase-7/8 contract fan-out
# completed). `app.shared.authz_helpers` likewise imports from the
# leaf.
#
# The remaining ownership re-export on the facade is documented:
#
#   - `_check_ownership`: internal use by the 3 `require_*_owner`
#     wrappers in `auth.py`; not a public API.

_PHASE13_REMOVED_OWNERSHIP_NAMES = frozenset(
    {
        "_extract_group_and_id",
        "assert_group_access",
        "compute_can_edit",
        "assert_can_modify",
        # BE-GO-READY-AUTH-ENGINE-020: closed R-019.1. IMDF
        # migrated to leaf; facade re-export removed.
        "assert_resource_visible",
    }
)


def _scan_facade_imports_of(
    roots: tuple[Path, ...], banned_names: frozenset[str]
) -> dict[str, list[tuple[int, str]]]:
    """Walk every .py under each root and collect facade imports
    that pull a name in `banned_names`.

    Returns a dict keyed by relative path -> list of (lineno, name).
    """
    hits: dict[str, list[tuple[int, str]]] = {}
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                continue
            file_hits: list[tuple[int, str]] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == _FACADE_MODULE:
                    for alias in node.names:
                        if alias.name in banned_names:
                            file_hits.append((node.lineno, alias.name))
            if file_hits:
                rel = str(path.relative_to(_BACKEND_ROOT)).replace("\\", "/")
                hits[rel] = file_hits
    return hits


def test_phase13_no_facade_import_of_removed_ownership_names_in_app():
    """Source-bound: NO file under `app/` (excluding `auth.py`
    itself, which sources them locally) may do
    `from app.core.auth.auth import <removed_name>`.

    Every router / shared / service must consume ownership helpers
    from the leaf `app.core.auth.ownership`. The 4 removed names
    cannot be re-introduced as a facade import without this lock
    catching it at CI time.
    """
    hits = _scan_facade_imports_of(
        (_APP,), _PHASE13_REMOVED_OWNERSHIP_NAMES
    )
    # `app/core/auth/auth.py` itself imports from the leaf and may
    # legitimately mention these names as a local target (it does
    # not — they were removed in 019 — but we explicitly allowlist
    # the file so a future internal reuse is not blocked).
    hits.pop("app/core/auth/auth.py", None)

    assert not hits, (
        "BE-GO-READY-AUTH-ENGINE-019 regression: file(s) under app/ "
        "import removed ownership names from the facade:\n"
        + "\n".join(
            f"  {rel}: " + "; ".join(
                f"line {ln} import {nm}" for (ln, nm) in lh
            )
            for rel, lh in sorted(hits.items())
        )
        + "\nImport from `app.core.auth.ownership` instead."
    )


def test_phase13_no_facade_import_of_removed_ownership_names_in_tests():
    """Symmetric lock for the test surface. Tests may legitimately
    monkeypatch via `*_router_module.<helper>` (already widespread)
    or directly call `ownership.<helper>`. They must NOT re-introduce
    `from app.core.auth.auth import <removed_name>`.

    Allowlist: the strangler-contract test file mentions the names
    in docstrings + parametrize lists and is excluded.
    """
    tests_root = _BACKEND_ROOT / "tests2"
    hits = _scan_facade_imports_of(
        (tests_root,), _PHASE13_REMOVED_OWNERSHIP_NAMES
    )
    # The strangler-contract test files (this file + the smoke
    # contract) reference the removed names in docstrings and lists
    # but never via facade ImportFrom; the AST scan filters those
    # already. Defensive allowlist in case a new contract test
    # legitimately exercises the removal.
    allowlist = {
        "tests2/security/unit/test_auth_facade_strangler_contract.py",
        "tests2/security/unit/test_auth_models_and_ownership_module_contract.py",
    }
    hits = {rel: lh for rel, lh in hits.items() if rel not in allowlist}

    assert not hits, (
        "BE-GO-READY-AUTH-ENGINE-019 regression: test file(s) "
        "import removed ownership names from the facade:\n"
        + "\n".join(
            f"  {rel}: " + "; ".join(
                f"line {ln} import {nm}" for (ln, nm) in lh
            )
            for rel, lh in sorted(hits.items())
        )
        + "\nImport from `app.core.auth.ownership` directly, or "
        "monkeypatch via the consuming router module."
    )


def test_phase13_facade_does_not_expose_removed_ownership_names():
    """Runtime defense-in-depth: `app.core.auth.auth` MUST NOT
    expose the 4 removed names as module attributes. Catches a
    diff that re-introduces the import block silently.
    """
    import importlib

    auth_mod = importlib.import_module("app.core.auth.auth")
    leaked = sorted(
        n for n in _PHASE13_REMOVED_OWNERSHIP_NAMES if hasattr(auth_mod, n)
    )
    assert not leaked, (
        f"BE-GO-READY-AUTH-ENGINE-019 regression: "
        f"`app.core.auth.auth` re-exposes removed names: {leaked}. "
        "Importare da `app.core.auth.ownership` invece."
    )


# BE-GO-READY-AUTH-ENGINE-016 — dep-import off facade, Slice A
#
# Migrated 6 routers to import the five FastAPI dependency callables
# from `app.core.auth.dependencies` (the canonical leaf) instead of
# the facade `app.core.auth.auth`.  After this batch each of the 6
# files has ZERO remaining `from app.core.auth.auth import ...` lines.
#
# Positive lock: each router must import at least one dep callable
# from the leaf.  Negative lock: no dep callable may be pulled via
# the facade in these files.  The Phase-2/3/4 per-name bans on
# `CurrentUser` etc. are still in force (vacuously satisfied now, but
# kept as regression guards).

_AUTH_ENGINE_016_MIGRATED_ROUTERS = (
    _APP / "core" / "admin_backups" / "router.py",
    _APP / "core" / "admin_system_status" / "router.py",
    _APP / "core" / "tickets" / "router.py",
    _APP / "core" / "estrai" / "router.py",
    _APP / "core" / "manipola" / "api" / "transforms_api.py",
    _APP / "core" / "carica" / "router.py",
)


@pytest.mark.parametrize(
    "path",
    _AUTH_ENGINE_016_MIGRATED_ROUTERS,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_auth_engine_016_migrated_router_imports_dependencies_from_leaf(
    path: Path,
):
    """AUTH-ENGINE-016 positive lock: each migrated router MUST import
    at least one dependency callable from the canonical leaf
    `app.core.auth.dependencies`.  Reverting to the facade re-import
    re-introduces the coupling AUTH-ENGINE-016 closed."""
    assert path.exists(), f"AUTH-ENGINE-016 migrated router missing: {path}"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_leaf: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _AUTH_ENGINE_013_DEPENDENCY_LEAF:
            for alias in node.names:
                imported_from_leaf.add(alias.name)
    leaf_intersect = imported_from_leaf & set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert leaf_intersect, (
        f"{path.relative_to(_BACKEND_ROOT)} must import at least one of "
        f"{_AUTH_ENGINE_013_LEAF_SYMBOLS} from "
        f"`{_AUTH_ENGINE_013_DEPENDENCY_LEAF}`. "
        "AUTH-ENGINE-016: dependency callables must come from the "
        "canonical leaf, not the facade."
    )


@pytest.mark.parametrize(
    "path",
    _AUTH_ENGINE_016_MIGRATED_ROUTERS,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_auth_engine_016_migrated_router_does_not_import_dependencies_from_facade(
    path: Path,
):
    """AUTH-ENGINE-016 negative lock: the 6 migrated routers MUST NOT
    pull any dependency callable from `app.core.auth.auth` (the facade).
    Catches a regression where a dep import is added back via the facade
    alongside or instead of the leaf import."""
    assert path.exists(), f"AUTH-ENGINE-016 migrated router missing: {path}"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_facade: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.auth.auth":
            for alias in node.names:
                imported_from_facade.add(alias.name)
    facade_intersect = imported_from_facade & set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert not facade_intersect, (
        f"{path.relative_to(_BACKEND_ROOT)} pulls "
        f"{sorted(facade_intersect)} from `app.core.auth.auth` (the facade). "
        "AUTH-ENGINE-016: dependency callables must be consumed from "
        "`app.core.auth.dependencies` (canonical leaf). Identity is "
        "preserved across the facade re-export, but the migrated router "
        "must point at the leaf."
    )


def test_phase13_facade_no_longer_exposes_check_ownership():
    """BE-GO-READY-AUTH-ENGINE-018 completion lock: after extracting
    the three ``require_*_owner`` wrappers to
    ``app.core.auth.owner_dependencies``, ``auth.py`` no longer
    imports ``_check_ownership`` from ``ownership.py``. The attribute
    MUST NOT appear on the facade module.

    Pre-018 the positive lock was: ``_check_ownership`` must be on the
    facade (internal use by the three wrappers). Post-018 the wrappers
    moved to the owner_dependencies leaf, which imports directly from
    ``app.core.auth.ownership``. The facade re-import was removed.
    """
    import importlib

    auth_mod = importlib.import_module("app.core.auth.auth")
    assert not hasattr(auth_mod, "_check_ownership"), (
        "app.core.auth.auth._check_ownership must NOT be present on the "
        "facade after AUTH-ENGINE-018. The three require_*_owner wrappers "
        "moved to app.core.auth.owner_dependencies which imports directly "
        "from app.core.auth.ownership. Re-introducing the facade import "
        "would regress the cut."
    )


# BE-GO-READY-AUTH-ENGINE-017 — dep-import off facade, Slice B
#
# Migrated 6 routers to import the five FastAPI dependency callables
# from `app.core.auth.dependencies` (the canonical leaf) instead of
# the facade `app.core.auth.auth`.
#
# Post AUTH-ENGINE-018: scheduler/router.py and files/router.py no
# longer retain a facade import for require_*_owner either; those
# callables are now in app.core.auth.owner_dependencies. The
# positive/negative locks below still hold (standard deps from leaf).
#
# Positive lock: each router must import at least one dep callable
# from the leaf.  Negative lock: none of the five standard callables
# may be pulled via the facade in these files.

_AUTH_ENGINE_017_MIGRATED_ROUTERS = (
    _APP / "core" / "connections" / "router.py",
    _APP / "core" / "features" / "routes.py",
    _APP / "core" / "scheduler" / "router.py",
    _APP / "core" / "eseguibili" / "router.py",
    _APP / "core" / "intermediate" / "router.py",
    _APP / "core" / "files" / "router.py",
)


@pytest.mark.parametrize(
    "path",
    _AUTH_ENGINE_017_MIGRATED_ROUTERS,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_auth_engine_017_migrated_router_imports_dependencies_from_leaf(
    path: Path,
):
    """AUTH-ENGINE-017 positive lock: each migrated router MUST import
    at least one dependency callable from the canonical leaf
    `app.core.auth.dependencies`.  Reverting to the facade re-import
    re-introduces the coupling AUTH-ENGINE-017 closed."""
    assert path.exists(), f"AUTH-ENGINE-017 migrated router missing: {path}"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_leaf: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _AUTH_ENGINE_013_DEPENDENCY_LEAF:
            for alias in node.names:
                imported_from_leaf.add(alias.name)
    leaf_intersect = imported_from_leaf & set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert leaf_intersect, (
        f"{path.relative_to(_BACKEND_ROOT)} must import at least one of "
        f"{_AUTH_ENGINE_013_LEAF_SYMBOLS} from "
        f"`{_AUTH_ENGINE_013_DEPENDENCY_LEAF}`. "
        "AUTH-ENGINE-017: dependency callables must come from the "
        "canonical leaf, not the facade."
    )


@pytest.mark.parametrize(
    "path",
    _AUTH_ENGINE_017_MIGRATED_ROUTERS,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_auth_engine_017_migrated_router_does_not_import_dependencies_from_facade(
    path: Path,
):
    """AUTH-ENGINE-017 negative lock: the 6 migrated routers MUST NOT
    pull any of the five standard dependency callables from
    `app.core.auth.auth` (the facade).  Domain-specific
    `require_*_owner` callables are not in scope for this lock —
    they remain on the facade until their own leaf extraction batch.
    Catches a regression where a standard dep import is added back
    via the facade alongside or instead of the leaf import."""
    assert path.exists(), f"AUTH-ENGINE-017 migrated router missing: {path}"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_facade: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.auth.auth":
            for alias in node.names:
                imported_from_facade.add(alias.name)
    facade_intersect = imported_from_facade & set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert not facade_intersect, (
        f"{path.relative_to(_BACKEND_ROOT)} pulls "
        f"{sorted(facade_intersect)} from `app.core.auth.auth` (the facade). "
        "AUTH-ENGINE-017: standard dependency callables must be consumed from "
        "`app.core.auth.dependencies` (canonical leaf). Identity is "
        "preserved across the facade re-export, but the migrated router "
        "must point at the leaf."
    )


# BE-GO-READY-AUTH-ENGINE-018 — owner dependency leaf extraction
#
# AUTH-ENGINE-018 extracted the three DB-bound owner FastAPI Depends
# callables from ``app.core.auth.auth`` into the new canonical leaf
# ``app.core.auth.owner_dependencies``:
#
#   - ``require_project_owner``   (projects/router.py)
#   - ``require_schedule_owner``  (scheduler/router.py)
#   - ``require_file_owner``      (files/router.py)
#
# Cycle concern resolved: ``app.core.auth.ownership`` no longer imports
# from ``auth.py`` (AUTH-ENGINE-005), so the new leaf can safely import
# ``get_current_user`` from ``app.core.auth.dependencies`` and
# ``_check_ownership`` from ``app.core.auth.ownership`` without cycles.
#
# ``auth.py`` keeps KEEP_COMPAT re-exports for all three callables so
# that any remaining legacy ``from app.core.auth.auth import
# require_project_owner`` keeps working with identity preserved.
#
# ``projects/router.py`` was also migrated off all five standard facade
# deps to ``app.core.auth.dependencies`` in this same batch.
#
# The three migrated routers must:
#   1. Import require_*_owner from the owner_dependencies leaf.
#   2. NOT import require_*_owner from the auth facade.
# ``projects/router.py`` additionally:
#   3. Must import standard deps from ``app.core.auth.dependencies``.
#   4. Must NOT import them from the facade.

_AUTH_ENGINE_018_OWNER_LEAF = "app.core.auth.owner_dependencies"
_AUTH_ENGINE_018_OWNER_SYMBOLS = frozenset({
    "require_project_owner",
    "require_schedule_owner",
    "require_file_owner",
})
_AUTH_ENGINE_018_MIGRATED_ROUTERS = (
    _APP / "core" / "projects" / "router.py",
    _APP / "core" / "scheduler" / "router.py",
    _APP / "core" / "files" / "router.py",
)


@pytest.mark.parametrize(
    "path",
    _AUTH_ENGINE_018_MIGRATED_ROUTERS,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_auth_engine_018_migrated_router_imports_owner_deps_from_leaf(
    path: Path,
):
    """AUTH-ENGINE-018 positive lock: each migrated router MUST import
    any ``require_*_owner`` callable it uses from the canonical leaf
    ``app.core.auth.owner_dependencies``. Reverting to the facade
    re-import re-introduces the coupling AUTH-ENGINE-018 closed."""
    assert path.exists(), f"AUTH-ENGINE-018 migrated router missing: {path}"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_leaf: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _AUTH_ENGINE_018_OWNER_LEAF:
            for alias in node.names:
                imported_from_leaf.add(alias.name)
    leaf_intersect = imported_from_leaf & _AUTH_ENGINE_018_OWNER_SYMBOLS
    assert leaf_intersect, (
        f"{path.relative_to(_BACKEND_ROOT)} must import at least one of "
        f"{sorted(_AUTH_ENGINE_018_OWNER_SYMBOLS)} from "
        f"`{_AUTH_ENGINE_018_OWNER_LEAF}`. "
        "AUTH-ENGINE-018: owner dependency callables must come from the "
        "canonical leaf, not the facade."
    )


@pytest.mark.parametrize(
    "path",
    _AUTH_ENGINE_018_MIGRATED_ROUTERS,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_auth_engine_018_migrated_router_does_not_import_owner_deps_from_facade(
    path: Path,
):
    """AUTH-ENGINE-018 negative lock: the three migrated routers MUST
    NOT pull any ``require_*_owner`` callable from ``app.core.auth.auth``
    (the facade). Catches a regression where a router adds back the
    facade import alongside or instead of the leaf import."""
    assert path.exists(), f"AUTH-ENGINE-018 migrated router missing: {path}"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_facade: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _FACADE_MODULE:
            for alias in node.names:
                imported_from_facade.add(alias.name)
    facade_intersect = imported_from_facade & _AUTH_ENGINE_018_OWNER_SYMBOLS
    assert not facade_intersect, (
        f"{path.relative_to(_BACKEND_ROOT)} pulls "
        f"{sorted(facade_intersect)} from `app.core.auth.auth` (the facade). "
        "AUTH-ENGINE-018: owner dependency callables must be consumed from "
        "`app.core.auth.owner_dependencies` (canonical leaf). Identity is "
        "preserved across the facade re-export, but the migrated router "
        "must point at the leaf."
    )


def test_auth_engine_018_owner_deps_leaf_exports_all_three_callables():
    """AUTH-ENGINE-018 contract: the canonical leaf
    ``app.core.auth.owner_dependencies`` MUST expose all three
    owner dependency callables in ``__all__``. Drift here would let
    a future contributor add a private helper without an explicit
    public-surface declaration."""
    import app.core.auth.owner_dependencies as owner_deps

    expected = _AUTH_ENGINE_018_OWNER_SYMBOLS
    assert hasattr(owner_deps, "__all__"), (
        "app.core.auth.owner_dependencies must declare `__all__` so the "
        "public dependency surface is explicit."
    )
    actual = set(owner_deps.__all__)
    assert actual == expected, (
        f"app.core.auth.owner_dependencies `__all__` drift:\n"
        f"  expected: {sorted(expected)}\n"
        f"  actual:   {sorted(actual)}"
    )
    for name in expected:
        assert hasattr(owner_deps, name), (
            f"app.core.auth.owner_dependencies missing documented "
            f"owner dependency callable: {name}"
        )


def test_auth_engine_018_facade_reexports_share_identity_with_leaf():
    """AUTH-ENGINE-018 contract: the facade re-export of each owner
    dependency callable MUST be the SAME Python object as the leaf
    definition. Without this property, FastAPI ``Depends(...)``
    resolutions through the two import paths would diverge, breaking
    caching keyed on the callable identity."""
    import app.core.auth.auth as facade
    import app.core.auth.owner_dependencies as owner_deps

    for name in _AUTH_ENGINE_018_OWNER_SYMBOLS:
        leaf_obj = getattr(owner_deps, name)
        facade_obj = getattr(facade, name)
        assert leaf_obj is facade_obj, (
            f"Identity broken for `{name}`: "
            f"`app.core.auth.auth.{name}` is NOT "
            f"`app.core.auth.owner_dependencies.{name}`. The facade "
            "must re-export the leaf's callable as the same "
            "Python object; FastAPI Depends resolution depends on it."
        )


def test_auth_engine_018_projects_router_imports_standard_deps_from_leaf():
    """AUTH-ENGINE-018: ``projects/router.py`` also migrated all five
    standard dependency callables off the facade. Positive lock."""
    path = _APP / "core" / "projects" / "router.py"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_leaf: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _AUTH_ENGINE_013_DEPENDENCY_LEAF:
            for alias in node.names:
                imported_from_leaf.add(alias.name)
    leaf_intersect = imported_from_leaf & set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert leaf_intersect, (
        "projects/router.py must import at least one standard dep callable "
        f"from `{_AUTH_ENGINE_013_DEPENDENCY_LEAF}` (AUTH-ENGINE-018)."
    )


def test_auth_engine_018_projects_router_no_standard_deps_from_facade():
    """AUTH-ENGINE-018 negative lock: ``projects/router.py`` must NOT
    pull any of the five standard dependency callables from the facade."""
    path = _APP / "core" / "projects" / "router.py"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_facade: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _FACADE_MODULE:
            for alias in node.names:
                imported_from_facade.add(alias.name)
    facade_intersect = imported_from_facade & set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert not facade_intersect, (
        f"projects/router.py pulls {sorted(facade_intersect)} from the facade. "
        "AUTH-ENGINE-018: standard deps must come from "
        "`app.core.auth.dependencies`."
    )


# BE-GO-READY-AUTH-ENGINE-019 — dep-import sweep, wider slice
#
# Migrated 7 files + cleaned main.py:
#
#   Routers (top-level import moved to leaf):
#   - app/core/orchestrator/router.py  get_current_user, require_not_esecutore
#   - app/core/imdf/router.py          get_current_user
#   - app/core/datasources/router.py   get_current_user, require_not_esecutore,
#                                       require_user_or_above, require_admin
#   - app/core/arricchisci/router.py   require_not_esecutore, require_admin
#   - app/core/auth/shares_router.py   require_superadmin
#
#   Auth-package internals (lazy proxy bodies migrated to leaf):
#   - app/core/auth/groups_router.py   lazy get_current_user, require_admin,
#                                       require_superadmin inside proxy fns
#   - app/core/auth/users_router.py    lazy get_current_user, require_admin
#                                       inside proxy fns
#
#   main.py: removed unused get_current_user import; only
#   `router as auth_router` stays on the facade.
#
# ast.walk catches body-local lazy imports as well as top-level ones.

_AUTH_ENGINE_019_MIGRATED_FILES = (
    _APP / "core" / "orchestrator" / "router.py",
    _APP / "core" / "imdf" / "router.py",
    _APP / "core" / "datasources" / "router.py",
    _APP / "core" / "arricchisci" / "router.py",
    _APP / "core" / "auth" / "shares_router.py",
    _APP / "core" / "auth" / "groups_router.py",
    _APP / "core" / "auth" / "users_router.py",
)


@pytest.mark.parametrize(
    "path",
    _AUTH_ENGINE_019_MIGRATED_FILES,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_auth_engine_019_migrated_file_imports_dependencies_from_leaf(
    path: Path,
):
    """AUTH-ENGINE-019 positive lock: each migrated file MUST import
    at least one standard dependency callable from the canonical leaf
    `app.core.auth.dependencies`. Body-local (lazy) imports are also
    caught via ast.walk. Reverting to the facade re-introduces the
    coupling AUTH-ENGINE-019 closed."""
    assert path.exists(), f"AUTH-ENGINE-019 migrated file missing: {path}"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_leaf: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _AUTH_ENGINE_013_DEPENDENCY_LEAF:
            for alias in node.names:
                imported_from_leaf.add(alias.name)
    leaf_intersect = imported_from_leaf & set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert leaf_intersect, (
        f"{path.relative_to(_BACKEND_ROOT)} must import at least one of "
        f"{_AUTH_ENGINE_013_LEAF_SYMBOLS} from "
        f"`{_AUTH_ENGINE_013_DEPENDENCY_LEAF}`. "
        "AUTH-ENGINE-019: dependency callables must come from the "
        "canonical leaf, not the facade."
    )


@pytest.mark.parametrize(
    "path",
    _AUTH_ENGINE_019_MIGRATED_FILES,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_auth_engine_019_migrated_file_does_not_import_dependencies_from_facade(
    path: Path,
):
    """AUTH-ENGINE-019 negative lock: the 7 migrated files MUST NOT
    pull any standard dependency callable from `app.core.auth.auth`.
    ast.walk catches body-local lazy imports as well as top-level ones."""
    assert path.exists(), f"AUTH-ENGINE-019 migrated file missing: {path}"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_facade: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.auth.auth":
            for alias in node.names:
                imported_from_facade.add(alias.name)
    facade_intersect = imported_from_facade & set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert not facade_intersect, (
        f"{path.relative_to(_BACKEND_ROOT)} pulls "
        f"{sorted(facade_intersect)} from `app.core.auth.auth` (the facade). "
        "AUTH-ENGINE-019: standard dependency callables must be consumed from "
        "`app.core.auth.dependencies` (canonical leaf)."
    )


def test_auth_engine_019_main_does_not_import_dep_callables_from_facade():
    """AUTH-ENGINE-019 negative lock for main.py: the only remaining
    facade import in main.py is `router as auth_router` (the auth
    router object). The previously unused `get_current_user` import
    was removed. This lock prevents any of the five standard dep
    callables from re-appearing in main.py's facade import."""
    path = _APP / "main.py"
    assert path.exists(), f"main.py missing at {path}"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_facade: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.auth.auth":
            for alias in node.names:
                imported_from_facade.add(alias.name)
    facade_intersect = imported_from_facade & set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert not facade_intersect, (
        f"main.py pulls {sorted(facade_intersect)} from `app.core.auth.auth`. "
        "AUTH-ENGINE-019: standard dep callables must not appear in main.py's "
        "facade import. `router as auth_router` is the only permitted name."
    )
