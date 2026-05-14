"""
Pydantic DTO per il sottosistema auth.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


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


# `from __future__ import annotations` (PEP 563) trasforma le annotation
# in stringhe ForwardRef. Pydantic v2 + FastAPI a runtime devono
# risolverle via `model_rebuild()` per generare lo schema OpenAPI dei
# parametri tipizzati nei router (`def create_user(data: UserCreate)`).
# Senza rebuild esplicito FastAPI raise `PydanticUserError: TypeAdapter
# [Annotated[ForwardRef('UserCreate'), Query(PydanticUndefined)]] is not
# fully defined` su /openapi.json. Costo del rebuild: zero a startup.
for _m in (
    LoginRequest, TokenResponse, CurrentUser, UserCreate,
    ChangePasswordRequest, UserResponse, ResetPasswordRequest,
    GroupCreate, MFAEnrollResponse, MFAVerifyRequest, MFADisableRequest,
    MFAStatusResponse, GroupResponse,
):
    _m.model_rebuild()
