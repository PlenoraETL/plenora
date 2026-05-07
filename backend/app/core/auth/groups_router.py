"""
Auth groups admin CRUD router — typed boundary.

BE-GO-READY-AUTH-ENGINE-004-GROUPS-ROUTER-EXTRACTION.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth import disclosure as _disclosure
from app.core.auth.models import (
    GroupCreate,
    GroupResponse,
)
from app.shared.contracts.auth import CurrentUser
from app.shared import error_codes
from app.shared.audit_action import audit_action
from app.shared.error_envelope import raise_typed_http
from app.utils.logger import get_logger

from app.db.config import groups as _groups


logger = get_logger(__name__)


__all__ = ["groups_router"]


groups_router = APIRouter(prefix="/auth/groups", tags=["groups"])
_security = HTTPBearer(auto_error=False)


def _get_current_user_proxy(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> CurrentUser:
    """Lazy proxy to avoid `groups_router -> auth.py -> groups_router` cycle.
    BE-GO-READY-AUTH-ENGINE-019: import moved to `dependencies` leaf.
    """
    from app.core.auth.dependencies import get_current_user

    return get_current_user(credentials)


def require_admin(
    current_user: CurrentUser = Depends(_get_current_user_proxy),
) -> CurrentUser:
    """Lazy proxy preserving the existing `auth.require_admin` behavior."""
    from app.core.auth.dependencies import require_admin as _require_admin

    return _require_admin(current_user)


def require_superadmin(
    current_user: CurrentUser = Depends(_get_current_user_proxy),
) -> CurrentUser:
    """Lazy proxy preserving the existing `auth.require_superadmin` behavior."""
    from app.core.auth.dependencies import require_superadmin as _require_superadmin

    return _require_superadmin(current_user)


@groups_router.get("", response_model=list[GroupResponse])
def list_groups(current_user: CurrentUser = Depends(require_admin)):
    """
    Lista gruppi.
    superadmin → tutti i gruppi (cross-tenant)
    admin      → solo il proprio gruppo (l'admin non può fare sharing
                  cross-gruppo, quindi non ha bisogno di vedere gli altri)
    """
    if current_user.role == "superadmin":
        groups = _groups.list_groups()
    else:
        g = _groups.get_group(current_user.group_id)
        groups = [g] if g else []
    # FIXME: #3 (2026-04-23): description=NULL nel DB ritorna None da dict.get,
    # non la stringa default; GroupResponse.description: str alza ValidationError.
    # Stesso pattern null-safe applicato su name per robustezza.
    return [GroupResponse(
                group_id=g["id"],
                name=g.get("name") or "",
                description=g.get("description") or "")
            for g in groups if g]


@groups_router.post("", response_model=GroupResponse, status_code=201)
@audit_action(action="group.create")
def create_group(data: GroupCreate,
                 current_user: CurrentUser = Depends(require_superadmin)):
    """Crea nuovo gruppo (solo superadmin)."""
    if _groups.get_group(data.group_id):
        _disclosure.raise_disclosure(_disclosure.group_id_already_exists())
    group = _groups.create_group(data.group_id, data.name, data.description)
    return GroupResponse(group_id=group["id"], name=group["name"], description=group.get("description", ""))


@groups_router.get("/{group_id}/summary")
def get_group_summary(group_id: str,
                      current_user: CurrentUser = Depends(require_superadmin)):
    """
    Ritorna il conteggio di tutte le entità del gruppo.
    Usato dal frontend per mostrare il modale di conferma prima del cascade delete.
    """
    if not _groups.get_group(group_id):
        _disclosure.raise_disclosure(_disclosure.group_not_found())
    return _groups.get_group_summary(group_id)


@groups_router.delete("/{group_id}", status_code=200)
@audit_action(action="group.delete", target_param="group_id")
def delete_group(group_id: str,
                 current_user: CurrentUser = Depends(require_superadmin)):
    """
    Cascade delete gruppo (solo superadmin).
    Elimina in ordine: schedules, api_connections, datasources, file_sources,
    connections, projects (+ results.db + intermediate.db), users, log.db, gruppo.
    Blocca solo se il gruppo è 'default'.
    """
    if group_id == "default":
        _disclosure.raise_disclosure(_disclosure.group_default_not_deletable())
    if not _groups.get_group(group_id):
        _disclosure.raise_disclosure(_disclosure.group_not_found())

    try:
        report = _groups.delete_group_cascade(group_id)
    except Exception as e:
        logger.error(f"[CRASH] delete_group_cascade fallito per group_id={group_id}: {e}", exc_info=True)
        raise_typed_http(
            error_codes.INTERNAL_ERROR,
            status_code=500,
            message="Errore interno del server",
        )

    return {"deleted": True, "group_id": group_id, "report": report}
