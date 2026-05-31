"""Source-bound guards for the auth-facade strangler boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_APP = _BACKEND_ROOT / "app"


_FACADE_MODULE = "app.core.auth.auth"


# facade baseline: full facade ban — no ImportFrom(app.core.auth.auth) at all.
_PHASE1_FILES = (
    _APP / "core" / "auth" / "auth_middleware.py",
    _APP / "core" / "auth" / "ownership.py",
    # `app.shared.authz_helpers` migrato a `app.core.auth.resource_access`
    # (canonical home: i checker DB-bound vivono accanto a ownership).
    _APP / "core" / "auth" / "resource_access.py",
    # typed-context also forbids facade entirely for these (CurrentUser-only
    # imports — nothing else was being pulled from the facade).
    _APP / "core" / "auth" / "async_user_recheck.py",
    _APP / "core" / "tickets" / "service.py",
)


# typed-context: per-name facade ban. Facade ImportFrom is allowed but it
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
}


# Phase-3: per-name facade ban for the next coherent router group.
# Same pattern as typed-context: facade may keep FastAPI Depends and
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


# typed-context: same facade ban for files / intermediate / projects /
# scheduler.
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


# Phase-6: routers using `group_filter` must not pull it from the facade.
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
    """facade baseline contract: each migrated file imports directly from
    leaf modules (`app.core.auth.jwt_token`, `app.core.auth.roles`,
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
    """typed-context contract: the file is still allowed to import FastAPI
    Depends (`get_current_user`, `require_*`, `require_superadmin`)
    from the facade, but it MUST NOT pull `CurrentUser` or ownership
    helpers from the facade — those have leaf sources."""
    assert path.exists(), f"migrated file missing: {path}"
    violations = _per_name_ban_violations(path, banned_names)
    assert violations == [], (
        f"{path.relative_to(_BACKEND_ROOT)} pulls typed-context banned "
        f"names from the facade:\n"
        + "\n".join(f"  line {ln}: {nm}" for ln, nm in violations)
        + "\nLeaf sources: CurrentUser ← app.core.auth.models; "
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
    """Phase-3 contract: extends typed-context to a second router group.
    The file may keep `get_current_user`, `require_*`, and
    `group_filter` on the facade. Banned names: `CurrentUser` and (for
    `features/routes.py`) the 4 ownership helpers."""
    assert path.exists(), f"migrated file missing: {path}"
    violations = _per_name_ban_violations(path, banned_names)
    assert violations == [], (
        f"{path.relative_to(_BACKEND_ROOT)} pulls Phase-3 banned "
        f"names from the facade:\n"
        + "\n".join(f"  line {ln}: {nm}" for ln, nm in violations)
        + "\nLeaf sources: CurrentUser ← app.core.auth.models; "
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
    """Phase-4 contract: applies the typed-context/3 split pattern to the
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
        + "\nLeaf sources: CurrentUser ← app.core.auth.models; "
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
    """typed-context contract: contract-boundary prep on
    files/intermediate/projects/scheduler. Same split as typed-context/3/4.
    Facade may keep FastAPI Depends (`get_current_user`,
    `require_*`, `require_*_owner`, `require_admin`,
    `require_user_or_above`, `require_superadmin`,
    `require_not_esecutore`). Banned: `CurrentUser` + ownership
    helpers (canonical leaves: `app.core.auth.models` +
    `app.core.auth.ownership`)."""
    assert path.exists(), f"migrated file missing: {path}"
    violations = _per_name_ban_violations(path, banned_names)
    assert violations == [], (
        f"{path.relative_to(_BACKEND_ROOT)} pulls typed-context banned "
        f"names from the facade:\n"
        + "\n".join(f"  line {ln}: {nm}" for ln, nm in violations)
        + "\nLeaf sources: CurrentUser ← app.core.auth.models; "
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
    routers using `group_filter` must not import it from the
    auth facade."""
    assert path.exists(), f"migrated file missing: {path}"
    violations = _per_name_ban_violations(path, banned_names)
    assert violations == [], (
        f"{path.relative_to(_BACKEND_ROOT)} pulls Phase-6 banned "
        f"name `group_filter` from the facade:\n"
        + "\n".join(f"  line {ln}: {nm}" for ln, nm in violations)
        + "\nLeaf source: group_filter ← app.core.auth.ownership."
    )


def test_auth_middleware_imports_decode_token_from_jwt_leaf():
    """Lock the exact leaf source: `decode_token` must come from
    `app.core.auth.jwt_token`, not the facade."""
    path = _APP / "core" / "auth" / "auth_middleware.py"
    text = path.read_text(encoding="utf-8")
    assert "from app.core.auth.jwt_token import decode_token" in text, (
        "auth_middleware.py must import decode_token from app.core.auth.jwt_token "
        "(strangler facade baseline)."
    )


def test_ownership_imports_roles_from_leaf_module():
    """Lock the eager top-level import of `ROLES_ADMIN_OR_ABOVE` from
    `app.core.auth.roles`. Pre-facade baseline this was a lazy import from the
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
    `app.core.auth.ownership` (DB-bound canonical home).

    Successivamente `app.shared.authz_helpers` e' stato migrato a
    `app.core.auth.resource_access` (stesso payload, home canonical
    accanto al cluster auth/ownership)."""
    path = _APP / "core" / "auth" / "resource_access.py"
    text = path.read_text(encoding="utf-8")
    assert "from app.shared.contracts.auth import CurrentUser" in text
    assert "from app.core.auth.ownership import" in text
    assert "assert_group_access" in text
    assert "assert_resource_visible" in text


# typed-context leaf-import presence assertions


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
        "assert_resource_visible",
        "assert_can_modify",
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
            ("assert_resource_visible", "assert_can_modify"),
        ),
        (
            _APP / "core" / "datasources" / "router.py",
            ("assert_group_access", "assert_resource_visible",
             "assert_can_modify", "compute_can_edit"),
        ),
        (
            _APP / "core" / "eseguibili" / "router.py",
            ("assert_resource_visible", "assert_can_modify"),
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


# BE-GO-READY-AUTH-ENGINE-013-ROUTER-IMPORTS-OFF-AUTH-FACADE
#
# W-013 created the canonical leaf `app.core.auth.dependencies`
# for the five FastAPI auth Depends callables.

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
    """W-013: migrated routers consume dependency callables from the leaf."""
    assert path.exists(), f"W-013 migrated router missing: {path}"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_leaf: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == _AUTH_ENGINE_013_DEPENDENCY_LEAF
        ):
            imported_from_leaf.update(alias.name for alias in node.names)
    leaf_intersect = imported_from_leaf & set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert leaf_intersect, (
        f"{path.relative_to(_BACKEND_ROOT)} must import at least one of "
        f"{_AUTH_ENGINE_013_LEAF_SYMBOLS} from "
        f"`{_AUTH_ENGINE_013_DEPENDENCY_LEAF}`."
    )


@pytest.mark.parametrize(
    "path",
    _AUTH_ENGINE_013_MIGRATED_ROUTERS,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_auth_engine_013_migrated_router_does_not_import_dependencies_from_facade(
    path: Path,
):
    """W-013: migrated routers must not pull dependency callables via the facade."""
    assert path.exists(), f"W-013 migrated router missing: {path}"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_facade: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _FACADE_MODULE:
            imported_from_facade.update(alias.name for alias in node.names)
    facade_intersect = imported_from_facade & set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert not facade_intersect, (
        f"{path.relative_to(_BACKEND_ROOT)} pulls "
        f"{sorted(facade_intersect)} from `{_FACADE_MODULE}`."
    )


def test_auth_engine_013_dependencies_leaf_exports_all_five_callables():
    """W-013: dependency leaf exposes the documented public surface."""
    import app.core.auth.dependencies as deps

    expected = set(_AUTH_ENGINE_013_LEAF_SYMBOLS)
    assert hasattr(deps, "__all__"), (
        "app.core.auth.dependencies must declare `__all__`."
    )
    actual = set(deps.__all__)
    assert actual == expected, (
        f"app.core.auth.dependencies `__all__` drift:\n"
        f"  expected: {sorted(expected)}\n"
        f"  actual:   {sorted(actual)}"
    )
    for name in expected:
        assert hasattr(deps, name), (
            f"app.core.auth.dependencies missing documented callable: {name}"
        )


def test_auth_engine_013_facade_reexports_dependencies_from_leaf():
    """auth.py must remain a facade for standard dependency callables."""
    path = _APP / "core" / "auth" / "auth.py"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_leaf: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == _AUTH_ENGINE_013_DEPENDENCY_LEAF
        ):
            imported_from_leaf.update(alias.name for alias in node.names)

    missing = set(_AUTH_ENGINE_013_LEAF_SYMBOLS) - imported_from_leaf
    assert not missing, (
        "app.core.auth.auth must re-export dependency callables by "
        f"importing them from `{_AUTH_ENGINE_013_DEPENDENCY_LEAF}`. "
        f"Missing: {sorted(missing)}"
    )


# facade baseline3 (BE-GO-READY-AUTH-ENGINE-019) ownership re-export removal
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
# completed). `app.core.auth.resource_access` likewise imports from the
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
# the facade in these files.  The typed-context/3/4 per-name bans on
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
# ``auth.py`` keeps explicit public re-exports for all three callables so
# that existing ``from app.core.auth.auth import require_project_owner``
# call sites keep working with identity preserved.
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


def test_auth_engine_018_owner_deps_use_domain_access_boundaries():
    """Owner dependencies must ask domains, not raw DB leaves."""
    path = _APP / "core" / "auth" / "owner_dependencies.py"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))

    violations: list[str] = []
    raw_db_leaves = {
        "app.core.projects.db",
        "app.core.scheduler.db",
        "app.core.files.db",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in raw_db_leaves:
            names = ", ".join(alias.name for alias in node.names)
            violations.append(f"from {node.module} import {names}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in raw_db_leaves:
                    violations.append(f"import {alias.name}")

    assert not violations, (
        "app.core.auth.owner_dependencies must use domain-owned "
        "ownership/access helpers instead of raw DB leaves: "
        f"{violations}"
    )
    # Riconosci entrambe le forme di dipendenza dal boundary di dominio:
    #   from app.core.files.ownership import X   (ImportFrom.module)
    #   import app.core.files.ownership as files_ownership  (Import.alias.name)
    # La seconda e' richiesta perche' owner_dependencies chiama gli helper
    # via attributo-modulo (`files_ownership.load_file_owner_context`), cosi'
    # i test possono intercettare il seam del dominio senza raggiungere il db.
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules |= {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    expected_boundaries = {
        "app.core.projects.access_context",
        "app.core.scheduler.ownership",
        "app.core.files.ownership",
    }
    assert expected_boundaries <= imported_modules


def test_auth_engine_018_facade_reexports_owner_deps_from_leaf():
    """auth.py must remain a facade for owner dependency callables."""
    path = _APP / "core" / "auth" / "auth.py"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imported_from_leaf: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _AUTH_ENGINE_018_OWNER_LEAF:
            imported_from_leaf.update(alias.name for alias in node.names)

    missing = _AUTH_ENGINE_018_OWNER_SYMBOLS - imported_from_leaf
    assert not missing, (
        "app.core.auth.auth must re-export owner dependency callables by "
        f"importing them from `{_AUTH_ENGINE_018_OWNER_LEAF}`. "
        f"Missing: {sorted(missing)}"
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
