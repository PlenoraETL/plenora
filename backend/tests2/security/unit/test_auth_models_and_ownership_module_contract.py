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

_OWNERSHIP_HELPERS = (
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


def test_auth_reexports_ownership_helpers_with_object_identity():
    """Stesso contratto per gli helper ownership: identita' garantita,
    cosi' chi ha gia' fatto `from app.core.auth.auth import assert_group_access`
    continua a vedere la funzione vera, e i monkeypatch su `auth_mod.X`
    si comportano come prima.
    """
    from app.core.auth import auth as auth_mod
    from app.core.auth import ownership

    for name in _OWNERSHIP_HELPERS:
        assert getattr(auth_mod, name) is getattr(ownership, name), (
            f"auth.{name} e ownership.{name} dovrebbero essere lo stesso oggetto"
        )


def test_require_owner_dependencies_remain_in_auth_module():
    """Le 3 dependency FastAPI restano in auth.py (cycle prevention)."""
    from app.core.auth import auth as auth_mod

    for name in ("require_project_owner", "require_schedule_owner", "require_file_owner"):
        assert callable(getattr(auth_mod, name, None)), (
            f"{name} dovrebbe restare definita in auth.py"
        )
