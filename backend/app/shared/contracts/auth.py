"""
Auth shared contract — pure types + pure policy.

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

    Canonical home: this contract module. Compatibility exports keep
    older import paths working without moving DB-bound ownership logic
    into the shared contract package.
    """
    if current_user.role == "superadmin":
        return None
    return current_user.group_id
