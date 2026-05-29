"""
Contract test BE-GO-READY-AUTH-ENGINE-012-SHARED-CONTRACT-BOUNDARY.

Lock the new shared auth contract module
"""

from __future__ import annotations

import os

# Ensure DATABASE_URL is set before any import that may load
# `app.db.pg_connection` (raises RuntimeError at module-level if missing).
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_APP = _BACKEND_ROOT / "app"
_CONTRACT_PATH = _APP / "shared" / "contracts" / "auth.py"
_CONTRACT_PKG_INIT = _APP / "shared" / "contracts" / "__init__.py"


# 1. Purity: the contract module imports zero forbidden surfaces


_FORBIDDEN_IMPORTS = {
    # Frameworks
    "fastapi",
    "starlette",
    # DB / Redis / I/O
    "app.db",
    "psycopg2",
    "redis",
    # Settings / config
    "app.config",
    # Disclosure / HTTPException-raising
    "app.core.auth.disclosure",
    # DB-bound auth modules
    "app.core.auth.ownership",
    "app.core.auth.mfa",
    # Stateful blacklist / rate-limit
    "app.core.security.token_blacklist",
    "app.core.security.login_rate_limiter",
    "app.core.security.password_hashing",
    "app.core.security.token_blacklist",
    "app.core.security.login_rate_limiter",
    # Cloud SDK
    "boto3",
    "app.infrastructure.storage.cloud_backends",
}


def _imports_in_file(path: Path) -> list[tuple[int, str]]:
    """Return [(lineno, module)] for every Import / ImportFrom node."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, alias.name))
    return out


def test_contract_module_exists():
    assert _CONTRACT_PATH.exists(), (
        f"Expected contract module at {_CONTRACT_PATH}"
    )
    assert _CONTRACT_PKG_INIT.exists(), (
        f"Expected contract package __init__ at {_CONTRACT_PKG_INIT}"
    )


def test_contract_module_imports_no_forbidden_surface():
    """The contract module's import list must be a subset of the
    allowed leaf modules (stdlib + `app.core.auth.models` +
    `app.core.auth.roles`)."""
    imports = _imports_in_file(_CONTRACT_PATH)
    violations: list[tuple[int, str]] = []
    for lineno, mod in imports:
        for forbidden in _FORBIDDEN_IMPORTS:
            if mod == forbidden or mod.startswith(forbidden + "."):
                violations.append((lineno, mod))
                break
    assert violations == [], (
        "app/shared/contracts/auth.py imports a forbidden surface:\n"
        + "\n".join(f"  line {ln}: {m}" for ln, m in violations)
        + "\nThe contract must stay pure: stdlib + "
        "`app.core.auth.models` + `app.core.auth.roles` only."
    )


def test_contract_init_imports_no_forbidden_surface():
    """The package __init__ is documentation-only; it must not
    introduce side-effect imports."""
    imports = _imports_in_file(_CONTRACT_PKG_INIT)
    # The __init__ should have ZERO imports (documentation-only).
    assert imports == [], (
        f"app/shared/contracts/__init__.py should be empty of "
        f"imports (documentation-only). Found: {imports}"
    )


# 2. Public surface: exactly the documented symbols


_EXPECTED_PUBLIC_SYMBOLS = {
    # Identity type
    "CurrentUser",
    # Role tuples
    "VALID_ROLES",
    "ROLES_ABOVE_VIEWER",
    "ROLES_ADMIN_OR_ABOVE",
    "ROLES_KIOSK_ONLY",
    # Pure policy
    "group_filter",
    # facade baseline0 (TE-016): pure auth DTOs
    "LoginRequest",
    "TokenResponse",
    "UserCreate",
    "UserUpdateRequest",
    "ChangePasswordRequest",
    "UserResponse",
    "ResetPasswordRequest",
    "GroupCreate",
    "GroupResponse",
    "MFAEnrollResponse",
    "MFAVerifyRequest",
    "MFADisableRequest",
    "MFAStatusResponse",
}


# facade baseline0 minor DTOs that the contract re-exports from
# `app.core.auth.models`. Identity must be preserved end-to-end:
# `contract.X is auth.models.X` (always); plus
# `contract.X is auth.X` for the subset still on the facade
# (the 3 DTOs `UserCreate`, `ResetPasswordRequest`, `GroupCreate`
# were removed from the facade by AUTH-ENGINE-021).
_PHASE10_MINOR_DTOS = (
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
)

# Subset still re-exported by the facade post-AUTH-ENGINE-021.
# Used by the facade-identity parametrization. The 3 DTOs removed
# from the facade keep their leaf-identity test (above) but no
# longer have a facade chain to verify.
_PHASE10_MINOR_DTOS_ON_FACADE = tuple(
    d
    for d in _PHASE10_MINOR_DTOS
    if d not in ("UserCreate", "ResetPasswordRequest", "GroupCreate")
)


def test_contract_module_all_matches_expected_surface():
    import app.shared.contracts.auth as contract

    assert hasattr(contract, "__all__"), (
        "app.shared.contracts.auth must declare `__all__` so the "
        "public surface is explicit."
    )
    actual = set(contract.__all__)
    assert actual == _EXPECTED_PUBLIC_SYMBOLS, (
        f"Contract surface drift:\n"
        f"  expected: {sorted(_EXPECTED_PUBLIC_SYMBOLS)}\n"
        f"  actual:   {sorted(actual)}\n"
        "Adding a symbol is a deliberate contract change. Update "
        "this test together with `__all__`."
    )


def test_contract_module_exports_each_documented_symbol():
    import app.shared.contracts.auth as contract

    for name in _EXPECTED_PUBLIC_SYMBOLS:
        assert hasattr(contract, name), (
            f"Contract missing documented symbol: {name}"
        )


# 3. Identity: contract symbols == leaf symbols (same Python objects)


def test_contract_current_user_is_leaf_identity():
    from app.core.auth.models import CurrentUser as leaf
    from app.shared.contracts.auth import CurrentUser as contract

    assert contract is leaf, (
        "`app.shared.contracts.auth.CurrentUser` must be the same "
        "Python class as `app.core.auth.models.CurrentUser`. "
        "Re-exporting the canonical leaf is mandatory; redefining "
        "would break monkeypatch surfaces and `Depends` typing."
    )


@pytest.mark.parametrize("dto_name", _PHASE10_MINOR_DTOS)
def test_contract_phase10_dto_is_leaf_identity(dto_name: str):
    """facade baseline0 (TE-016): each minor DTO re-exported by the
    contract must be the SAME class as `app.core.auth.models.X`
    (not a redefinition). Identity preservation is the binding
    invariant of every fan-out batch — without it, any consumer
    holding a reference to `auth.models.LoginRequest` and a
    consumer holding `contracts.auth.LoginRequest` would fail
    `isinstance` checks against each other."""
    import app.core.auth.models as leaf_mod
    import app.shared.contracts.auth as contract_mod

    leaf = getattr(leaf_mod, dto_name)
    contract = getattr(contract_mod, dto_name)

    assert contract is leaf, (
        f"`app.shared.contracts.auth.{dto_name}` must be the same "
        f"Python class as `app.core.auth.models.{dto_name}`. "
        "Re-exporting the canonical leaf is mandatory."
    )


@pytest.mark.parametrize("dto_name", _PHASE10_MINOR_DTOS_ON_FACADE)
def test_contract_phase10_dto_is_facade_identity(dto_name: str):
    """facade baseline0: identity also holds across the facade
    re-export chain for DTOs still re-exported by
    `app.core.auth.auth`. The chain
    `auth.auth.X is auth.models.X is contracts.auth.X` must
    survive any future facade rework.

    AUTH-ENGINE-021 removed `UserCreate`, `ResetPasswordRequest`,
    `GroupCreate` from the facade; those 3 DTOs are tested for
    LEAF identity only (`contract.X is auth.models.X`) and for
    facade ABSENCE (separate lock in
    `test_auth_models_and_ownership_module_contract.py`)."""
    import app.core.auth.auth as facade
    import app.shared.contracts.auth as contract_mod

    facade_obj = getattr(facade, dto_name)
    contract_obj = getattr(contract_mod, dto_name)

    assert contract_obj is facade_obj, (
        f"Identity chain broken for `{dto_name}`: "
        "the facade and the contract must resolve to the same "
        "class object (both routed through `app.core.auth.models`)."
    )


def test_contract_role_tuples_are_leaf_identity():
    from app.core.auth.roles import (
        ROLES_ABOVE_VIEWER as leaf_above,
        ROLES_ADMIN_OR_ABOVE as leaf_admin,
        ROLES_KIOSK_ONLY as leaf_kiosk,
        VALID_ROLES as leaf_valid,
    )
    from app.shared.contracts.auth import (
        ROLES_ABOVE_VIEWER as ct_above,
        ROLES_ADMIN_OR_ABOVE as ct_admin,
        ROLES_KIOSK_ONLY as ct_kiosk,
        VALID_ROLES as ct_valid,
    )

    assert ct_valid is leaf_valid
    assert ct_above is leaf_above
    assert ct_admin is leaf_admin
    assert ct_kiosk is leaf_kiosk


def test_group_filter_lives_only_in_shared_contract():
    """`group_filter` is a pure shared policy helper, not an ownership leaf export."""
    import app.core.auth.ownership as ownership
    from app.shared.contracts.auth import group_filter

    assert callable(group_filter)
    assert not hasattr(ownership, "group_filter")


# 4. group_filter behaviour preserved from Phase-6


def test_group_filter_superadmin_returns_none():
    from app.core.auth.models import CurrentUser
    from app.shared.contracts.auth import group_filter

    user = CurrentUser(
        user_id="u1", username="root", group_id="g1", role="superadmin",
    )
    assert group_filter(user) is None


def test_group_filter_non_superadmin_returns_group_id():
    from app.core.auth.models import CurrentUser
    from app.shared.contracts.auth import group_filter

    user = CurrentUser(
        user_id="u1", username="alice", group_id="g42", role="user",
    )
    assert group_filter(user) == "g42"


# 5. Migrated routers consume the contract (source-bound)


_PHASE7_MIGRATED_ROUTERS = (
    _APP / "core" / "logs" / "router.py",
    _APP / "core" / "intermediate" / "router.py",
    _APP / "core" / "projects" / "router.py",
    _APP / "core" / "scheduler" / "router.py",
)


def _imports_from(path: Path, module: str) -> list[set[str]]:
    """Return [name_set] for each ImportFrom node targeting `module`."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    out: list[set[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            out.append({a.name for a in node.names})
    return out


@pytest.mark.parametrize(
    "path",
    _PHASE7_MIGRATED_ROUTERS,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase7_router_imports_contract_for_current_user_and_group_filter(
    path: Path,
):
    """Each Phase-7 migrated router must import `CurrentUser` AND
    `group_filter` from `app.shared.contracts.auth`. Drift =
    regression to the leaf-only direct import (typed-context/6 shape) or
    to the facade (facade baseline shape)."""
    assert path.exists(), f"migrated file missing: {path}"
    contract_imports = _imports_from(path, "app.shared.contracts.auth")
    flat = set().union(*contract_imports) if contract_imports else set()
    assert "CurrentUser" in flat, (
        f"{path.relative_to(_BACKEND_ROOT)} must import "
        "`CurrentUser` from `app.shared.contracts.auth` (Phase-7 "
        "migration). Currently:\n"
        + "\n".join(f"  from app.shared.contracts.auth import {sorted(s)}" for s in contract_imports)
    )
    assert "group_filter" in flat, (
        f"{path.relative_to(_BACKEND_ROOT)} must import "
        "`group_filter` from `app.shared.contracts.auth` (Phase-7 "
        "migration)."
    )


@pytest.mark.parametrize(
    "path",
    _PHASE7_MIGRATED_ROUTERS,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase7_router_does_not_import_current_user_from_models_leaf(
    path: Path,
):
    """Phase-7 migrated routers MUST NOT pull `CurrentUser` from
    `app.core.auth.models` directly — the contract is the new
    canonical consumer-facing source. Models stays the canonical
    *implementation* home (and the contract re-exports from it),
    but router code goes through the contract."""
    assert path.exists(), f"migrated file missing: {path}"
    models_imports = _imports_from(path, "app.core.auth.models")
    flat = set().union(*models_imports) if models_imports else set()
    assert "CurrentUser" not in flat, (
        f"{path.relative_to(_BACKEND_ROOT)} pulls `CurrentUser` "
        "from `app.core.auth.models` directly. Phase-7 contract: "
        "router code consumes `CurrentUser` from "
        "`app.shared.contracts.auth` (which re-exports from the "
        "models leaf)."
    )


@pytest.mark.parametrize(
    "path",
    _PHASE7_MIGRATED_ROUTERS,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_phase7_router_does_not_import_group_filter_from_ownership_leaf(
    path: Path,
):
    """Same lock for `group_filter`: Phase-7 routers consume it
    from the contract, not directly from `ownership.py`."""
    assert path.exists(), f"migrated file missing: {path}"
    ownership_imports = _imports_from(path, "app.core.auth.ownership")
    flat = set().union(*ownership_imports) if ownership_imports else set()
    assert "group_filter" not in flat, (
        f"{path.relative_to(_BACKEND_ROOT)} pulls `group_filter` "
        "from `app.core.auth.ownership` directly. Phase-7 contract: "
        "routers consume `group_filter` from "
        "`app.shared.contracts.auth`."
    )
