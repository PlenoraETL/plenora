"""
Auth shared contract — pure types + pure policy.

BE-GO-READY-AUTH-ENGINE-012-SHARED-CONTRACT-BOUNDARY.
"""

from __future__ import annotations

from typing import Optional

from app.core.auth.models import (
    ChangePasswordRequest,
    CurrentUser,
    GroupCreate,
    GroupResponse,
    LoginRequest,
    MFADisableRequest,
    MFAEnrollResponse,
    MFAStatusResponse,
    MFAVerifyRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from app.core.auth.roles import (
    ROLES_ABOVE_VIEWER,
    ROLES_ADMIN_OR_ABOVE,
    ROLES_KIOSK_ONLY,
    VALID_ROLES,
)


__all__ = [
    # Identity types
    "CurrentUser",
    # Role tuples
    "VALID_ROLES",
    "ROLES_ABOVE_VIEWER",
    "ROLES_ADMIN_OR_ABOVE",
    "ROLES_KIOSK_ONLY",
    # Pure policy
    "group_filter",
    # Auth DTOs (Phase-10): pure pydantic request/response payloads
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
]


def group_filter(current_user: CurrentUser) -> Optional[str]:
    """Ritorna il group_id da usare come filtro.

    Pure policy:
      - superadmin → ``None`` (nessun filtro, vede tutto);
      - altri → ``current_user.group_id``.

    Canonical home: this contract module. `app.core.auth.ownership`
    re-exports for KEEP_COMPAT (ownership cannot define it directly
    without dragging `app.db.db_service` into the contract chain).
    `app.core.auth.auth` re-exports for KEEP_COMPAT through
    ownership. Identity preserved across all three surfaces:
    `auth.group_filter is ownership.group_filter is
    contract.group_filter` — locked by
    `tests2/security/unit/test_auth_facade_strangler_contract.py::
    test_group_filter_leaf_canonical_home`.
    """
    if current_user.role == "superadmin":
        return None
    return current_user.group_id
