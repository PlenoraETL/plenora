"""Auth shared contract: pure DTOs, role constants, and pure policy."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator, model_validator


VALID_ROLES: tuple[str, ...] = (
    "esecutore",
    "viewer",
    "user",
    "admin",
    "superadmin",
)

ROLES_ADMIN_OR_ABOVE: tuple[str, ...] = ("admin", "superadmin")
ROLES_ABOVE_VIEWER: tuple[str, ...] = ("user", "admin", "superadmin")
ROLES_KIOSK_ONLY: tuple[str, ...] = ("esecutore",)


class LoginRequest(BaseModel):
    username: str
    password: str
    # None is valid for users without MFA; enrolled users must provide a code.
    mfa_code: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    group_id: str
    role: str
    expires_in: int


class CurrentUser(BaseModel):
    user_id: str
    username: str
    group_id: str
    role: str


class UserCreate(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    group_id: str
    role: str = "user"


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class UserResponse(BaseModel):
    id: str
    username: str
    email: Optional[str]
    group_id: str
    role: str
    is_active: bool
    created_at: str


class ResetPasswordRequest(BaseModel):
    new_password: str


class UserUpdateRequest(BaseModel):
    """Body tipizzato per `PUT /auth/users/{user_id}` (admin update)."""

    email: Optional[str] = None
    # StrictBool: rifiuta coercion da string ("yes", "false") - `is_active`
    # e' un security flag e una coercion silenziosa puo' risultare in
    # account disattivati per errore.
    is_active: Optional[StrictBool] = None
    role: Optional[str] = None
    group_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_password(cls, values):
        if isinstance(values, dict) and "password" in values:
            raise ValueError(
                "Per cambiare password usa POST /users/{id}/reset-password"
            )
        return values

    @field_validator("email")
    @classmethod
    def _email_format(cls, v):
        if v is None:
            return v
        if not v:
            raise ValueError("email deve essere una stringa non vuota")
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Formato email non valido")
        return v

    @field_validator("role")
    @classmethod
    def _role_allowed(cls, v):
        if v is None:
            return v
        if v not in VALID_ROLES:
            raise ValueError(
                f"Ruolo non valido: '{v}'. Validi: {VALID_ROLES}"
            )
        return v

    @field_validator("group_id")
    @classmethod
    def _group_id_nonempty(cls, v):
        if v is None:
            return v
        if not v:
            raise ValueError("group_id deve essere una stringa non vuota")
        return v


class GroupCreate(BaseModel):
    group_id: str
    name: str
    description: str = ""


class MFAEnrollResponse(BaseModel):
    secret: str
    provisioning_uri: str
    qr_code_png_base64: str


class MFAVerifyRequest(BaseModel):
    code: str


class MFADisableRequest(BaseModel):
    code: str


class MFAStatusResponse(BaseModel):
    enabled: bool
    enrolled_at: Optional[str] = None
    last_verified_at: Optional[str] = None


class GroupResponse(BaseModel):
    group_id: str
    name: str
    description: str = ""


def group_filter(current_user: CurrentUser) -> Optional[str]:
    """Return the group_id filter for a user, or None for superadmin."""
    if current_user.role == "superadmin":
        return None
    return current_user.group_id


for _m in (
    LoginRequest,
    TokenResponse,
    CurrentUser,
    UserCreate,
    ChangePasswordRequest,
    UserResponse,
    ResetPasswordRequest,
    UserUpdateRequest,
    GroupCreate,
    MFAEnrollResponse,
    MFAVerifyRequest,
    MFADisableRequest,
    MFAStatusResponse,
    GroupResponse,
):
    _m.model_rebuild()


__all__ = [
    "CurrentUser",
    "VALID_ROLES",
    "ROLES_ABOVE_VIEWER",
    "ROLES_ADMIN_OR_ABOVE",
    "ROLES_KIOSK_ONLY",
    "group_filter",
    "LoginRequest",
    "TokenResponse",
    "UserCreate",
    "ChangePasswordRequest",
    "UserResponse",
    "ResetPasswordRequest",
    "UserUpdateRequest",
    "GroupCreate",
    "GroupResponse",
    "MFAEnrollResponse",
    "MFAVerifyRequest",
    "MFADisableRequest",
    "MFAStatusResponse",
]
