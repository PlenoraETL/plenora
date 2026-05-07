"""
Pydantic DTO per il sottosistema auth.

Estratto verbatim da `app.core.auth.auth` (STRUCT-005 Seam C). `auth.py`
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str
    # MFA opt-in: None per utenti senza MFA (comportamento invariato).
    # Per utenti con MFA attiva: richiesto, altrimenti 403 mfa_required.
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
