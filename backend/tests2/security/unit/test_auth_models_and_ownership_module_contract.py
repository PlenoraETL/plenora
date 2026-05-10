"""
Smoke contract — extracted boundary + B': estrazione models + ownership.

Verifica due invarianti minime del refactor:
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# BE-GO-READY-AUTH-ENGINE-021-USERS-GROUPS-REEXPORT-REMOVAL:
# `UserCreate`, `ResetPasswordRequest`, `GroupCreate` rimossi dal
# facade re-export (zero callsite, 1-occurrence-only in auth.py
# Previous contract). `_FACADE_NO_LONGER_REEXPORTED_DTOS` lock simmetrico
# sotto. Tutti e 13 i DTO restano canonici in
# `app.core.auth.models` e raggiungibili via
# `app.shared.contracts.auth` (facade baseline0 fan-out).
_MODELS = (
    "LoginRequest",
    "TokenResponse",
    "CurrentUser",
    "ChangePasswordRequest",
    "UserResponse",
    "GroupResponse",
    "MFAEnrollResponse",
    "MFAVerifyRequest",
    "MFADisableRequest",
    "MFAStatusResponse",
)

# DTOs explicitly removed from the facade in AUTH-ENGINE-021. Their
# canonical home is `app.core.auth.models`; consumers must use the
# leaf module or the contract module (`app.shared.contracts.auth`).
_FACADE_NO_LONGER_REEXPORTED_DTOS = (
    "UserCreate",
    "ResetPasswordRequest",
    "GroupCreate",
)

# Functions explicitly removed from the facade in AUTH-ENGINE-021.
# Their canonical home is the per-domain router module
# (`app.core.auth.users_router` / `app.core.auth.groups_router`).
_FACADE_NO_LONGER_REEXPORTED_HANDLERS = (
    "list_users",
    "create_user",
    "update_user",
    "reset_password",
    "delete_user",
    "list_groups",
    "create_group",
    "get_group_summary",
    "delete_group",
)

# Full set of 6 helpers: ALL must live in the canonical leaf
# `app.core.auth.ownership`. Adding a 7th helper here is a
# contract change.
_OWNERSHIP_HELPERS = (
    "_extract_group_and_id",
    "assert_group_access",
    "compute_can_edit",
    "assert_can_modify",
    "assert_resource_visible",
    "_check_ownership",
)

# BE-GO-READY-AUTH-ENGINE-019: 4 ownership re-exports rimossi
# (`_extract_group_and_id`, `assert_group_access`,
# `compute_can_edit`, `assert_can_modify`).
# BE-GO-READY-AUTH-ENGINE-020: chiude R-019.1 — IMDF migrato
# al leaf, anche `assert_resource_visible` rimosso dal facade.
# AUTH-ENGINE-022: anche `_check_ownership` non e' piu' re-exportato dal
# facade; le dependency owner vivono in `owner_dependencies`.
_FACADE_REEXPORTED_OWNERSHIP_HELPERS = ()
_FACADE_NO_LONGER_REEXPORTED = (
    "_extract_group_and_id",
    "assert_group_access",
    "compute_can_edit",
    "assert_can_modify",
    "assert_resource_visible",
    "_check_ownership",
)


def test_models_module_exposes_all_pydantic_classes():
    from app.core.auth import models

    for name in _MODELS:
        cls = getattr(models, name, None)
        assert cls is not None, f"models.{name} mancante nel modulo nuovo"
        assert isinstance(cls, type) and issubclass(cls, BaseModel), (
            f"models.{name} dovrebbe essere una sottoclasse di pydantic.BaseModel"
        )


def test_ownership_module_exposes_all_helpers():
    from app.core.auth import ownership

    for name in _OWNERSHIP_HELPERS:
        fn = getattr(ownership, name, None)
        assert callable(fn), f"ownership.{name} mancante o non callable"


def test_auth_reexports_models_with_object_identity():
    """auth.py re-esporta gli STESSI oggetti di models.py — non copie.
    Cosi' `auth_mod.CurrentUser` e `models.CurrentUser` sono lo stesso
    oggetto, e isinstance/equality cross-import resta consistente.
    """
    from app.core.auth import auth as auth_mod
    from app.core.auth import models

    for name in _MODELS:
        assert getattr(auth_mod, name) is getattr(models, name), (
            f"auth.{name} e models.{name} dovrebbero essere lo stesso oggetto"
        )


def test_auth_reexports_remaining_ownership_helpers_with_object_identity():
    """Identity contract per gli helper ownership ANCORA re-esportati
    dal facade. Oggi il set e' vuoto: il test resta come lock per
    impedire re-export accidentali futuri senza aggiornare il contratto.
    """
    from app.core.auth import auth as auth_mod
    from app.core.auth import ownership

    for name in _FACADE_REEXPORTED_OWNERSHIP_HELPERS:
        assert getattr(auth_mod, name) is getattr(ownership, name), (
            f"auth.{name} e ownership.{name} dovrebbero essere lo stesso "
            "oggetto (residual re-export ancora supportato)"
        )


def test_auth_no_longer_reexports_removed_ownership_helpers():
    """BE-GO-READY-AUTH-ENGINE-019: 4 helper rimossi dal facade
    (`_extract_group_and_id`, `assert_group_access`,
    `compute_can_edit`, `assert_can_modify`). Devono NON essere
    accessibili come attributi del modulo `app.core.auth.auth`.

    Conseguenza: `from app.core.auth.auth import <name>` solleva
    `ImportError` e `auth_mod.<name>` solleva `AttributeError`.
    Tutti i consumer production (6 router) gia' importano dal leaf.
    """
    from app.core.auth import auth as auth_mod

    for name in _FACADE_NO_LONGER_REEXPORTED:
        assert not hasattr(auth_mod, name), (
            f"app.core.auth.auth.{name} e' stato rimosso da "
            "BE-GO-READY-AUTH-ENGINE-019. Se questa asserzione "
            "fallisce, qualcuno ha re-introdotto il re-export. "
            "Importare da `app.core.auth.ownership` invece."
        )


def test_auth_no_longer_reexports_removed_ownership_helpers_via_import():
    """Defense-in-depth: `from app.core.auth.auth import <removed>`
    deve sollevare `ImportError`. Lock simmetrico al precedente.
    """
    import importlib

    auth_mod = importlib.import_module("app.core.auth.auth")
    # Force module attribute access via __dict__ lookup (mirrors what
    # `from ... import` does internally).
    for name in _FACADE_NO_LONGER_REEXPORTED:
        assert name not in auth_mod.__dict__, (
            f"`from app.core.auth.auth import {name}` is no longer "
            "supported after BE-GO-READY-AUTH-ENGINE-019. "
            "Importare da `app.core.auth.ownership` invece."
        )


def test_require_owner_dependencies_remain_in_auth_module():
    """Le 3 dependency FastAPI restano in auth.py (cycle prevention)."""
    from app.core.auth import auth as auth_mod

    for name in ("require_project_owner", "require_schedule_owner", "require_file_owner"):
        assert callable(getattr(auth_mod, name, None)), (
            f"{name} dovrebbe restare definita in auth.py"
        )


# AUTH-ENGINE-021 removal locks


def test_auth_no_longer_reexports_removed_dtos():
    """BE-GO-READY-AUTH-ENGINE-021: 3 DTOs removed from the facade
    (`UserCreate`, `ResetPasswordRequest`, `GroupCreate`). Each was
    1-occurrence-only in auth.py Previous contract (zero internal use)
    and had zero `from app.core.auth.auth import` consumers anywhere.

    Re-introducing the re-export silently widens the facade surface;
    the leaf module `app.core.auth.models` and the contract module
    `app.shared.contracts.auth` are the canonical homes.
    """
    from app.core.auth import auth as auth_mod

    for name in _FACADE_NO_LONGER_REEXPORTED_DTOS:
        assert not hasattr(auth_mod, name), (
            f"app.core.auth.auth.{name} was removed by "
            "BE-GO-READY-AUTH-ENGINE-021. If this assertion fails, "
            "someone re-introduced the re-export. Import from "
            "`app.core.auth.models` (leaf) or "
            "`app.shared.contracts.auth` (facade baseline0 fan-out) instead."
        )


def test_auth_no_longer_reexports_removed_dtos_via_import():
    """Defense-in-depth: `from app.core.auth.auth import <removed_dto>`
    must raise ImportError. Symmetric to the hasattr lock."""
    import importlib

    auth_mod = importlib.import_module("app.core.auth.auth")
    for name in _FACADE_NO_LONGER_REEXPORTED_DTOS:
        assert name not in auth_mod.__dict__, (
            f"`from app.core.auth.auth import {name}` is no longer "
            "supported after BE-GO-READY-AUTH-ENGINE-021. "
            "Import from `app.core.auth.models` instead."
        )


def test_auth_no_longer_reexports_removed_handlers():
    """BE-GO-READY-AUTH-ENGINE-021: 9 router handler functions removed
    from the facade (5 users + 4 groups). Each was kept exclusively
    for `auth_mod.<name>` test patch surfaces; the 4 affected tests
    have been migrated to `app.core.auth.users_router` /
    `app.core.auth.groups_router` (canonical handler home since
    AUTH-ENGINE-003/004 extraction).
    """
    from app.core.auth import auth as auth_mod

    for name in _FACADE_NO_LONGER_REEXPORTED_HANDLERS:
        assert not hasattr(auth_mod, name), (
            f"app.core.auth.auth.{name} was removed by "
            "BE-GO-READY-AUTH-ENGINE-021. If this assertion fails, "
            "someone re-introduced the re-export. Call the handler "
            "via `app.core.auth.users_router` or "
            "`app.core.auth.groups_router` instead."
        )


def test_auth_no_longer_reexports_removed_handlers_via_import():
    """Defense-in-depth: `from app.core.auth.auth import <handler>`
    must raise ImportError for the 9 removed handler names."""
    import importlib

    auth_mod = importlib.import_module("app.core.auth.auth")
    for name in _FACADE_NO_LONGER_REEXPORTED_HANDLERS:
        assert name not in auth_mod.__dict__, (
            f"`from app.core.auth.auth import {name}` is no longer "
            "supported after BE-GO-READY-AUTH-ENGINE-021. "
            "Call via `app.core.auth.users_router` / "
            "`app.core.auth.groups_router` instead."
        )


# AUTH-ENGINE-014 removal lock


def test_auth_no_longer_reexports_group_filter():
    """BE-GO-READY-AUTH-ENGINE-014-AUTH-FACADE-REEXPORT-DEATHMAP-DTO-CUT:
    the `group_filter` KEEP_COMPAT re-export was deleted from
    `app.core.auth.auth`. The 9 app routers already consume the
    leaf `app.core.auth.ownership` directly. Production callers via
    the facade: ZERO.

    Re-introducing `from app.core.auth.ownership import group_filter`
    (or any other re-export of `group_filter`) at the top of
    `auth.py` widens the facade surface and reverses AUTH-ENGINE-014.

    Symmetric to `test_auth_no_longer_reexports_removed_dtos` /
    `test_auth_no_longer_reexports_removed_handlers`: hasattr +
    __dict__ both checked.
    """
    import importlib

    auth_mod = importlib.import_module("app.core.auth.auth")
    assert not hasattr(auth_mod, "group_filter"), (
        "app.core.auth.auth.group_filter was removed by "
        "BE-GO-READY-AUTH-ENGINE-014. If this assertion fails, "
        "someone re-introduced the re-export. Import from "
        "`app.core.auth.ownership` instead (canonical home), or "
        "`app.shared.contracts.auth` for shared DTO contracts."
    )
    assert "group_filter" not in auth_mod.__dict__, (
        "`from app.core.auth.auth import group_filter` is no "
        "longer supported after BE-GO-READY-AUTH-ENGINE-014. "
        "Import from `app.core.auth.ownership` instead."
    )


def test_users_router_module_exposes_handlers():
    """Positive lock: the 5 users handler functions live on
    `app.core.auth.users_router` (canonical home post-extraction)."""
    from app.core.auth import users_router as users_mod

    for name in (
        "list_users",
        "create_user",
        "update_user",
        "reset_password",
        "delete_user",
    ):
        assert callable(getattr(users_mod, name, None)), (
            f"app.core.auth.users_router.{name} must be callable. "
            "Removing it would break the 4 tests migrated by "
            "BE-GO-READY-AUTH-ENGINE-021 + the FastAPI router "
            "registration."
        )


def test_groups_router_module_exposes_handlers():
    """Positive lock: the 4 groups handler functions live on
    `app.core.auth.groups_router` (canonical home post-extraction)."""
    from app.core.auth import groups_router as groups_mod

    for name in (
        "list_groups",
        "create_group",
        "get_group_summary",
        "delete_group",
    ):
        assert callable(getattr(groups_mod, name, None)), (
            f"app.core.auth.groups_router.{name} must be callable. "
            "Removing it would break the 2 tests migrated by "
            "BE-GO-READY-AUTH-ENGINE-021 + the FastAPI router "
            "registration."
        )
