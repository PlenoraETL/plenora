"""Compatibility re-exports for auth DTOs.

Canonical auth contracts live in ``app.shared.contracts.auth`` so shared
modules do not import from the core auth package.
"""

from app.shared.contracts.auth import (
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
    UserUpdateRequest,
)

__all__ = [
    "ChangePasswordRequest",
    "CurrentUser",
    "GroupCreate",
    "GroupResponse",
    "LoginRequest",
    "MFADisableRequest",
    "MFAEnrollResponse",
    "MFAStatusResponse",
    "MFAVerifyRequest",
    "ResetPasswordRequest",
    "TokenResponse",
    "UserCreate",
    "UserResponse",
    "UserUpdateRequest",
]
