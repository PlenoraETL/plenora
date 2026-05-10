"""
FastAPI auth dependencies - canonical leaf.

"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.auth import db as _users_dal
from app.core.auth import disclosure as _disclosure
from app.core.auth.roles import (
    ROLES_ABOVE_VIEWER,
    ROLES_ADMIN_OR_ABOVE,
    ROLES_KIOSK_ONLY,
)
from app.shared.contracts.auth import CurrentUser
from app.core.auth.jwt_token import decode_token
from app.shared.token_blacklist import is_jti_blacklisted


__all__ = [
    "get_current_user",
    "require_user_or_above",
    "require_not_esecutore",
    "require_admin",
    "require_superadmin",
]


_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> CurrentUser:
    """Inietta l'utente autenticato. 401 se token mancante o invalido."""
    if credentials is None:
        _disclosure.raise_disclosure(_disclosure.token_missing())
    payload = decode_token(credentials.credentials)
    if payload is None:
        _disclosure.raise_disclosure(_disclosure.token_invalid())
    # Blacklist check: se il jti e' stato revocato via /auth/logout
    # rifiuta anche se la signature+exp sarebbero valide. Precede la
    # query DB (piu' economico di un SELECT users
    # Backward compat: token pre-rollout jti non hanno la claim -> None
    # > `is_jti_blacklisted(None)` False -> passa, come prima
    if is_jti_blacklisted(payload.get("jti")):
        _disclosure.raise_disclosure(_disclosure.token_revoked())
    user = _users_dal.get_user(payload["sub"])
    if not user or not user.is_active:
        _disclosure.raise_disclosure(_disclosure.user_not_found_or_disabled())
    return CurrentUser(
        user_id=payload["sub"],
        username=payload["username"],
        group_id=payload["group_id"],
        role=payload["role"],
    )


def require_user_or_above(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Richiede ruolo user, admin o superadmin (esclude viewer ed
    esecutore). Da usare per endpoint di creazione/modifica
    risorse: viewer e' sola lettura, esecutore non vede nemmeno
    la UI di gestione risorse.
    """
    if current_user.role not in ROLES_ABOVE_VIEWER:
        _disclosure.raise_disclosure(_disclosure.role_insufficient_for_write())
    return current_user


def require_not_esecutore(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Esclude SOLO il ruolo esecutore. Usato sugli endpoint di
    lettura "generale" (list_projects, list_connections,
    list_files, list_schedules, ecc.) che devono restare
    accessibili a viewer/user/admin/superadmin ma non al ruolo
    kiosk esecutore (che vede SOLO gli eseguibili del proprio
    gruppo).
    """
    if current_user.role in ROLES_KIOSK_ONLY:
        _disclosure.raise_disclosure(_disclosure.role_kiosk_only())
    return current_user


def require_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Richiede ruolo admin o superadmin."""
    if current_user.role not in ROLES_ADMIN_OR_ABOVE:
        _disclosure.raise_disclosure(_disclosure.role_admin_required())
    return current_user


def require_superadmin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Richiede ruolo superadmin."""
    if current_user.role != "superadmin":
        _disclosure.raise_disclosure(_disclosure.role_superadmin_required())
    return current_user
