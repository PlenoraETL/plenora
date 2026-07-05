"""Dipendenze FastAPI condivise tra i router auth (users, groups).

Dipendenze unificate per evitare duplicazione tra i router e mantenere
sincronizzata la validazione. Mantengono il pattern lazy-import per
evitare cicli `users_router -> auth.py -> users_router`.

Pattern Go-friendly: funzioni modulo leaf, no classi, import inline
nel body (equivalente di `package-level alias` con call-site lookup
in Go).
"""

from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth import dependencies as auth_dependencies_module
from app.shared.contracts.auth import CurrentUser

_security = HTTPBearer(auto_error=False)


def get_current_user_proxy(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> CurrentUser:
    """Lazy proxy a `auth.dependencies.get_current_user`.

    L'import e' inline (non a top-level) per evitare il ciclo
    `users_router/groups_router -> auth -> router -> users_router`.
    """
    return auth_dependencies_module.get_current_user(credentials)


def require_admin(
    current_user: CurrentUser = Depends(get_current_user_proxy),
) -> CurrentUser:
    """Lazy proxy a `auth.dependencies.require_admin`."""
    return auth_dependencies_module.require_admin(current_user)


def require_superadmin(
    current_user: CurrentUser = Depends(get_current_user_proxy),
) -> CurrentUser:
    """Lazy proxy a `auth.dependencies.require_superadmin`."""
    return auth_dependencies_module.require_superadmin(current_user)
