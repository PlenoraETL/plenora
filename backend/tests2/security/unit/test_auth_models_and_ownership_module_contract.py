"""Smoke contract — STRUCT-005 Seam C + B': estrazione models + ownership.

Verifica due invarianti minime del refactor:

  1. I nuovi moduli `app.core.auth.models` e `app.core.auth.ownership`
     espongono i simboli attesi (13 BaseModel + 6 helper).

  2. `app.core.auth.auth` re-esporta gli stessi oggetti (identita'
     bit-per-bit). Cosi' i test che fanno
     `from app.core.auth.auth import CurrentUser, assert_group_access, ...`
     o `auth_mod.X` continuano a comportarsi come prima.

La copertura funzionale (auth/login/ownership/group access) e' gia'
garantita dai test pre-esistenti nelle suite `security`, `auth`,
`projects`, `scheduler`, `connections`, `datasources`, ecc.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


_MODELS = (
    "LoginRequest",
    "TokenResponse",
    "CurrentUser",
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

# BE-GO-READY-AUTH-ENGINE-019-OWNERSHIP-REEXPORT-REMOVAL:
# subset still re-exported from `app.core.auth.auth` for
# KEEP_COMPAT. The other 4 (`_extract_group_and_id`,
# `assert_group_access`, `compute_can_edit`, `assert_can_modify`)
# have ZERO callsites depending on facade identity (verified by
# grep across `app/` + `tests2/`) and are no longer re-exported.
# `assert_resource_visible` stays for Phase-2 imdf residual;
# `_check_ownership` stays for internal use by `require_*_owner`.
_FACADE_REEXPORTED_OWNERSHIP_HELPERS = (
    "assert_resource_visible",
    "_check_ownership",
)
_FACADE_NO_LONGER_REEXPORTED = (
    "_extract_group_and_id",
    "assert_group_access",
    "compute_can_edit",
    "assert_can_modify",
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
    dal facade dopo BE-GO-READY-AUTH-ENGINE-019-OWNERSHIP-REEXPORT-REMOVAL.

    Solo `assert_resource_visible` (Phase-2 imdf residual) e
    `_check_ownership` (uso interno dai 3 `require_*_owner`)
    restano sul facade. Per quei 2 nomi, identita' garantita:
    `auth_mod.X is ownership.X`.
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
