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
    # imdf/router.py: CurrentUser top-level migrated to leaf;
    # body-local lazy `assert_resource_visible` deliberately KEPT on
    # facade because `tests2/security/unit/test_imdf_security_contract.py`
    # monkeypatches `auth_mod.assert_resource_visible` for the
    # `_assert_bundle_group_access` flow. Phase-3 will migrate the
    # test to monkeypatch the leaf and then ban the lazy facade import
    # here too.
    _APP / "core" / "imdf" / "router.py":
        frozenset({"CurrentUser"}),
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
    """Lock the split: `CurrentUser` from `auth.models`, ownership
    helpers from `auth.ownership`. Pre-Phase-1 all 3 came from the
    facade in a single ImportFrom block."""
    path = _APP / "shared" / "authz_helpers.py"
    text = path.read_text(encoding="utf-8")
    assert "from app.core.auth.models import CurrentUser" in text
    assert "from app.core.auth.ownership import" in text
    assert "assert_group_access" in text
    assert "assert_resource_visible" in text


# ---------------------------------------------------------------------------
# Phase-2 leaf-import presence assertions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        _APP / "shared" / "async_user_recheck.py",
        _APP / "core" / "tickets" / "service.py",
        _APP / "core" / "admin_backups" / "router.py",
        _APP / "core" / "admin_system_status" / "router.py",
        _APP / "core" / "tickets" / "router.py",
        _APP / "core" / "orchestrator" / "router.py",
        _APP / "core" / "imdf" / "router.py",
    ],
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase2_imports_currentuser_from_models_leaf(path: Path):
    """Every Phase-2 migrated file must import `CurrentUser` from the
    leaf `app.core.auth.models`."""
    text = path.read_text(encoding="utf-8")
    assert "from app.core.auth.models import CurrentUser" in text, (
        f"{path.name} must import CurrentUser from app.core.auth.models "
        "(strangler Phase-2)."
    )


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


def test_phase2_imdf_lazy_assert_resource_visible_kept_on_facade_residual():
    """Phase-2 trade-off: `imdf/router.py:138` keeps the body-local
    lazy `from app.core.auth.auth import assert_resource_visible`
    because `test_imdf_security_contract.py` monkeypatches
    `auth_mod.assert_resource_visible`. Phase-3 owns: migrate the
    test to monkeypatch the leaf AND retarget this lazy import to
    `app.core.auth.ownership`. Locked here as an explicit residual
    so the lazy facade import does not silently spread."""
    path = _APP / "core" / "imdf" / "router.py"
    text = path.read_text(encoding="utf-8")
    assert (
        "from app.core.auth.auth import assert_resource_visible" in text
    ), (
        "imdf/router.py must still keep the lazy facade import for "
        "assert_resource_visible (test monkeypatch surface). A future "
        "phase will migrate the test and retarget this to the leaf."
    )


# ---------------------------------------------------------------------------
# Phase-3 leaf-import presence assertions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        _APP / "core" / "logs" / "router.py",
        _APP / "core" / "logs" / "runs_router.py",
        _APP / "core" / "estrai" / "router.py",
        _APP / "core" / "manipola" / "api" / "transforms_api.py",
        _APP / "core" / "features" / "routes.py",
    ],
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase3_imports_currentuser_from_models_leaf(path: Path):
    """Every Phase-3 migrated file must import `CurrentUser` from the
    leaf `app.core.auth.models`."""
    text = path.read_text(encoding="utf-8")
    assert "from app.core.auth.models import CurrentUser" in text, (
        f"{path.name} must import CurrentUser from app.core.auth.models "
        "(strangler Phase-3)."
    )


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


# ---------------------------------------------------------------------------
# Phase-4 leaf-import presence assertions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        _APP / "core" / "arricchisci" / "router.py",
        _APP / "core" / "carica" / "router.py",
        _APP / "core" / "connections" / "router.py",
        _APP / "core" / "datasources" / "router.py",
        _APP / "core" / "eseguibili" / "router.py",
    ],
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase4_imports_currentuser_from_models_leaf(path: Path):
    """Every Phase-4 migrated file must import `CurrentUser` from the
    leaf `app.core.auth.models`."""
    text = path.read_text(encoding="utf-8")
    assert "from app.core.auth.models import CurrentUser" in text, (
        f"{path.name} must import CurrentUser from app.core.auth.models "
        "(strangler Phase-4)."
    )


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
